"""Generate a bounded explanation benchmark with saved contexts and manual reviews."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.db import SessionLocal, engine
from gamerec.integrations.ollama import (
    OllamaResponseError,
    OllamaUnavailableError,
    chat_with_ollama,
    create_ollama_client,
)
from gamerec.schemas.explanation import SearchExplanation, SearchExplanationContext
from gamerec.services.explanation_context import get_search_explanation_context
from gamerec.services.recommendation_evaluation import latency_summary
from gamerec.services.search_explanation import (
    SYSTEM_PROMPT,
    SearchExplanationGeneration,
    build_search_explanation_messages,
    generate_search_explanation,
)

CASE_CATEGORIES = {
    "well_known",
    "less_familiar",
    "partial_match",
    "mismatch",
    "sparse_metadata",
}
REVIEW_DIMENSIONS = ("factual_accuracy", "query_relevance", "usefulness", "naturalness")


def load_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) > 20:
        raise ValueError("Cases must be a list with at most 20 entries")
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[int, str]] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "steam_app_id",
            "search_query",
            "category",
            "reason",
        }:
            raise ValueError("Invalid evaluation case fields")
        if any(
            not isinstance(case[key], str) or not case[key].strip()
            for key in ("case_id", "search_query", "category", "reason")
        ):
            raise ValueError("Case text must be nonblank strings")
        for key in ("case_id", "search_query", "category", "reason"):
            case[key] = case[key].strip()
        if type(case["steam_app_id"]) is not int or case["steam_app_id"] <= 0:
            raise ValueError("steam_app_id must be a positive integer")
        if case["category"] not in CASE_CATEGORIES:
            raise ValueError("Unknown case category")
        pair = (case["steam_app_id"], case["search_query"])
        if case["case_id"] in seen_ids or pair in seen_pairs:
            raise ValueError("Duplicate case ID or game-query pair")
        seen_ids.add(case["case_id"])
        seen_pairs.add(pair)
    return cases


async def read_contexts(db: AsyncSession, cases: list[dict]) -> list[dict]:
    records = []
    for case in cases:
        context = await get_search_explanation_context(
            db, case["steam_app_id"], case["search_query"]
        )
        records.append(
            {
                **case,
                "context": context.model_dump(
                    mode="json", exclude={"insufficient_descriptive_evidence"}
                )
                if context
                else None,
            }
        )
    return records


def load_snapshot(path: Path, cases: list[dict]) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if not isinstance(records, list) or len(records) != len(cases):
        raise ValueError("Snapshot does not match the selected cases")
    for case, record in zip(cases, records, strict=True):
        if (
            not isinstance(record, dict)
            or "context" not in record
            or any(record.get(k) != v for k, v in case.items())
        ):
            raise ValueError("Snapshot does not match the selected cases")
        if record.get("context") is not None:
            context = SearchExplanationContext.model_validate(record["context"])
            if (context.steam_app_id, context.search_query) != (
                case["steam_app_id"],
                " ".join(case["search_query"].split()),
            ):
                raise ValueError("Snapshot context has a different game or query")
    return records


def load_previous_prompt(path: Path) -> dict:
    previous = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(previous, dict):
        raise TypeError("Previous prompt must be a JSON object")
    if (
        previous.get("model") != settings.ollama_model
        or previous.get("options")
        != {"temperature": 0, "num_ctx": 4096, "num_predict": 512}
        or not isinstance(previous.get("system_prompt"), str)
        or not previous["system_prompt"].strip()
        or not isinstance(previous.get("response_schema"), dict)
        or set(previous["response_schema"].get("properties", {}))
        != {"matching_features", "explanation"}
    ):
        raise ValueError(
            "Previous prompt must use the same model, options and response fields"
        )
    return previous


def validate_review(review: dict | None) -> dict:
    blank = dict.fromkeys(REVIEW_DIMENSIONS)
    blank.update(hallucination_detected=None, notes="")
    if review is None:
        return blank
    if not isinstance(review, dict) or not set(review) <= set(blank):
        raise ValueError("Invalid reviewer fields")
    blank.update(review)
    for key in REVIEW_DIMENSIONS:
        score = blank[key]
        if score is not None and (type(score) is not int or not 1 <= score <= 5):
            raise ValueError("Reviewer scores must be integers 1–5 or null")
    verdict = blank["hallucination_detected"]
    if verdict is not None and type(verdict) is not bool and verdict != "undetermined":
        raise ValueError(
            "Hallucination verdict must be true, false, undetermined or null"
        )
    if not isinstance(blank["notes"], str):
        raise TypeError("Reviewer notes must be a string")
    return blank


def summarize_results(records: list[dict]) -> dict:
    summary = {}
    variants = sorted({v for r in records for v in r["generations"]})
    for variant in variants:
        results = [
            r["generations"][variant] for r in records if variant in r["generations"]
        ]
        reviews = [validate_review(r.get("human_review")) for r in results]
        dimensions = {}
        for dimension in REVIEW_DIMENSIONS:
            scores = [r[dimension] for r in reviews if r[dimension] is not None]
            dimensions[dimension] = {
                "rated_count": len(scores),
                "mean": sum(scores) / len(scores) if scores else None,
            }
        samples = [
            r["generation_ms"] for r in results if not r["error"] and not r["fallback"]
        ]
        summary[variant] = {
            "case_count": len(results),
            "error_count": sum(r["error"] is not None for r in results),
            "fallback_count": sum(r["fallback"] for r in results),
            "warm_model_latency_ms": latency_summary(samples),
            "reviewed_all_dimensions": sum(
                all(r[k] is not None for k in REVIEW_DIMENSIONS) for r in reviews
            ),
            "dimensions": dimensions,
            "hallucination_verdicts": {
                "true": sum(r["hallucination_detected"] is True for r in reviews),
                "false": sum(r["hallucination_detected"] is False for r in reviews),
                "undetermined": sum(
                    r["hallucination_detected"] == "undetermined" for r in reviews
                ),
                "unrated": sum(r["hallucination_detected"] is None for r in reviews),
            },
        }
    return summary


async def measure_generation(
    client: httpx.AsyncClient,
    context: SearchExplanationContext | None,
    previous: dict | None = None,
) -> dict:
    start = time.perf_counter()
    result = {
        "output": None,
        "model": settings.ollama_model,
        "generation_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "ollama": None,
        "fallback": False,
        "error": None,
        "human_review": validate_review(None),
    }
    if context is None:
        result["error"] = {
            "type": "MissingGame",
            "message": "Game not found in catalogue",
        }
        return result
    try:
        if previous is None or context.insufficient_descriptive_evidence:
            generated = await generate_search_explanation(context, client)
        else:
            messages = build_search_explanation_messages(context)
            messages[0]["content"] = previous["system_prompt"]
            response = await chat_with_ollama(
                client, messages, previous["response_schema"]
            )
            generated = SearchExplanationGeneration(
                SearchExplanation.model_validate_json(response.message.content),
                response,
            )
        result["output"] = generated.explanation.model_dump()
        response = generated.ollama_response
        result["fallback"] = response is None
        if response:
            result.update(
                model=response.model,
                input_tokens=response.prompt_eval_count,
                output_tokens=response.eval_count,
                ollama=response.model_dump(exclude={"message"}),
            )
    except (OllamaUnavailableError, OllamaResponseError, ValidationError) as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    result["generation_ms"] = (time.perf_counter() - start) * 1000
    return result


async def evaluate(
    client: httpx.AsyncClient,
    records: list[dict],
    *,
    previous: dict | None = None,
    warmups: int = 1,
) -> dict:
    variants = (
        {"previous": previous, "revised": None} if previous else {"revised": None}
    )
    contexts = [
        SearchExplanationContext.model_validate(r["context"]) if r["context"] else None
        for r in records
    ]
    warm_context = next(
        (c for c in contexts if c and not c.insufficient_descriptive_evidence), None
    )
    warmup_results = {}
    evaluated = [
        {
            **r,
            "name": c.name if c else None,
            "llm_input": json.loads(build_search_explanation_messages(c)[1]["content"])
            if c
            else None,
            "generations": {},
        }
        for r, c in zip(records, contexts, strict=True)
    ]
    # Run each version as a batch; model startup and each prompt's warm-up are excluded.
    for variant, prompt in variants.items():
        warmup_results[variant] = []
        if warm_context:
            for _ in range(warmups):
                warmup_results[variant].append(
                    await measure_generation(client, warm_context, prompt)
                )
        for record, context in zip(evaluated, contexts, strict=True):
            record["generations"][variant] = await measure_generation(
                client, context, prompt
            )
            print(f"Evaluated {variant}: {record['case_id']}", flush=True)
    return {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "model": settings.ollama_model,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512},
        "system_prompt": SYSTEM_PROMPT,
        "response_schema": SearchExplanation.model_json_schema(),
        "previous_prompt": previous,
        "excluded_warmups": warmup_results,
        "cases": evaluated,
        "summary": summarize_results(evaluated),
    }


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("tests/data/explanation_evaluation_cases.json"),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Reuse contexts from a saved contexts/evaluation JSON",
    )
    parser.add_argument(
        "--compare-prompt",
        type=Path,
        help="Exact previous prompt/schema/model artifact",
    )
    parser.add_argument(
        "--review-file",
        type=Path,
        help="Summarize an edited evaluation JSON without inference",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("benchmark_results/explanations")
    )
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args(argv)
    if not 0 <= args.warmups <= 3:
        parser.error("warmups must be between 0 and 3")
    return args


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        if args.review_file:
            report = json.loads(args.review_file.read_text(encoding="utf-8"))
            print(
                json.dumps(
                    summarize_results(report["cases"]), indent=2, allow_nan=False
                )
            )
            return
        cases = load_cases(args.cases)
        previous = (
            load_previous_prompt(args.compare_prompt) if args.compare_prompt else None
        )
        if args.snapshot:
            records = load_snapshot(args.snapshot, cases)
        else:
            async with SessionLocal() as db:
                records = await read_contexts(db, cases)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot = args.output_dir / f"contexts_{timestamp}.json"
        write_json(snapshot, {"cases": records})
        async with create_ollama_client() as client:
            report = await evaluate(
                client, records, previous=previous, warmups=args.warmups
            )
        report["context_snapshot"] = str(snapshot)
        output = args.output_dir / f"evaluation_{timestamp}.json"
        write_json(output, report)
        print(f"Wrote {output}; edit human_review fields to supply reviewer ratings.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
