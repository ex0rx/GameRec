"""Benchmark the explanation API with bounded, warm HTTP requests."""

import argparse
import asyncio
import json
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from statistics import fmean

import httpx

from gamerec.core.config import settings
from gamerec.schemas.explanation import SearchExplanationResponse
from gamerec.scripts.evaluate_explanations import load_cases
from gamerec.services.recommendation_evaluation import latency_summary

CASE_IDS = (
    "zomboid_coop",
    "cyberpunk_progression",
    "quake_combat",
    "terraria_exploration",
    "tetrapulse_coop",
)
HTTP_ERRORS = {
    404: "game_not_found",
    422: "invalid_request",
    502: "invalid_model_output",
    503: "service_unavailable",
    504: "server_timeout",
}


def benchmark_cases(path: Path) -> list[dict]:
    cases = {case["case_id"]: case for case in load_cases(path)}
    return [cases[case_id] for case_id in CASE_IDS]


async def request_explanation(client: httpx.AsyncClient, case: dict) -> dict:
    started = time.perf_counter()
    status = None
    error = None
    try:
        response = await client.post(
            "/explanations/search",
            json={
                "steam_app_id": case["steam_app_id"],
                "search_query": case["search_query"],
            },
        )
        status = response.status_code
        if status != 200:
            error = HTTP_ERRORS.get(status, "http_error")
        else:
            result = SearchExplanationResponse.model_validate(response.json())
            if result.steam_app_id != case["steam_app_id"]:
                error = "invalid_api_response"
    except httpx.TimeoutException:
        error = "client_timeout"
    except httpx.RequestError:
        error = "transport_error"
    except ValueError:
        error = "invalid_api_response"
    return {
        "case_id": case["case_id"],
        "steam_app_id": case["steam_app_id"],
        "search_query": case["search_query"],
        "http_status": status,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "succeeded": error is None,
        "error_category": error,
    }


def summarize_requests(records: list[dict], elapsed_seconds: float) -> dict:
    if not isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("Elapsed time must be finite and non-negative")
    successful = [r["latency_ms"] for r in records if r["succeeded"]]
    failed = [r["latency_ms"] for r in records if not r["succeeded"]]
    errors = Counter(r["error_category"] for r in records if not r["succeeded"])

    def timing(samples: list[float]) -> dict:
        return {
            **latency_summary(samples),
            "mean_ms": fmean(samples) if samples else None,
        }

    timeouts = errors["client_timeout"] + errors["server_timeout"]
    return {
        "request_count": len(records),
        "successful_count": len(successful),
        "failed_count": len(failed),
        "success_rate": len(successful) / len(records) if records else None,
        "error_rate": len(failed) / len(records) if records else None,
        "timeout_count": timeouts,
        "other_error_count": len(failed) - timeouts,
        "errors_by_category": dict(sorted(errors.items())),
        "successful_latency_ms": timing(successful),
        "failed_latency_ms": timing(failed),
        "elapsed_seconds": elapsed_seconds,
        "throughput_rps": len(records) / elapsed_seconds if elapsed_seconds else None,
        "successful_throughput_rps": (
            len(successful) / elapsed_seconds if elapsed_seconds else None
        ),
    }


async def benchmark_level(
    client: httpx.AsyncClient,
    cases: list[dict],
    *,
    repetitions: int,
    concurrency: int,
    warmups: int,
) -> dict:
    if not cases or repetitions <= 0 or concurrency <= 0 or warmups < 0:
        raise ValueError("Cases, repetitions and concurrency must be positive")
    excluded = [
        await request_explanation(client, cases[i % len(cases)]) for i in range(warmups)
    ]
    semaphore = asyncio.Semaphore(concurrency)
    active = peak = 0
    started = time.perf_counter()

    async def measured(case: dict, request_index: int) -> dict:
        nonlocal active, peak
        async with semaphore:
            active += 1
            peak = max(peak, active)
            offset = (time.perf_counter() - started) * 1000
            try:
                record = await request_explanation(client, case)
                return {
                    **record,
                    "request_index": request_index,
                    "started_offset_ms": offset,
                    "finished_offset_ms": (time.perf_counter() - started) * 1000,
                }
            finally:
                active -= 1

    records = await asyncio.gather(
        *(measured(case, i) for i, case in enumerate(cases * repetitions))
    )
    elapsed = time.perf_counter() - started
    return {
        "concurrency": concurrency,
        "excluded_warmups": excluded,
        "warmups_succeeded": bool(excluded) and all(r["succeeded"] for r in excluded),
        "peak_in_flight": peak,
        "overlap_observed": peak > 1,
        **summarize_requests(records, elapsed),
        "requests": records,
    }


async def benchmark(
    client: httpx.AsyncClient,
    cases: list[dict],
    *,
    repetitions: int,
    concurrency_levels: list[int],
    warmups: int,
    timeout: float,
) -> dict:
    levels = []
    for concurrency in concurrency_levels:
        levels.append(
            await benchmark_level(
                client,
                cases,
                repetitions=repetitions,
                concurrency=concurrency,
                warmups=warmups,
            )
        )
        print(
            f"Measured concurrency {concurrency}: {levels[-1]['successful_count']} successful",
            flush=True,
        )
    return {
        "benchmarked_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "configured_model": settings.ollama_model,
            "client_python": platform.python_version(),
            "client_platform": platform.platform(),
            "inference_mode": None,
        },
        "workload": cases,
        "repetitions_per_case": repetitions,
        "measured_requests_per_level": len(cases) * repetitions,
        "concurrency_levels": concurrency_levels,
        "warmups_per_level": warmups,
        "request_timeout_seconds": timeout,
        "levels": levels,
        "cold_start": None,
        "internal_ollama_timings": None,
        "limitations": [
            "Local small-sample benchmark; not evidence of production scalability.",
            "Hardware, model size and current system load affect these measurements.",
            "Latency excludes waiting for the benchmark semaphore; offsets record scheduling.",
            "Throughput uses complete measured batch time, excluding warm-ups.",
            "p95 uses nearest rank per outcome group; with 20 samples it is the second-slowest.",
            "Model configuration is local benchmark configuration, not verified by the public API.",
            "The public API omits model timings; cold lifecycle and internal timings need separate verification.",
            "Factuality and usefulness are separate Phase 10C assessments; human labels are pending.",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("tests/data/explanation_evaluation_cases.json"),
    )
    parser.add_argument(
        "--repetitions", type=int, default=4, help="Measured repetitions per case"
    )
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 5])
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.repetitions <= 0 or any(level <= 0 for level in args.concurrency):
        parser.error("repetitions and concurrency must be positive")
    if len(set(args.concurrency)) != len(args.concurrency):
        parser.error("concurrency levels must be distinct")
    if args.warmups < 0 or not isfinite(args.timeout) or args.timeout <= 0:
        parser.error("warmups must be non-negative and timeout finite and positive")
    url = httpx.URL(args.base_url)
    if (
        url.scheme not in ("http", "https")
        or not url.host
        or url.username
        or url.password
    ):
        parser.error("base URL must be HTTP(S) without credentials")
    return args


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cases = benchmark_cases(args.cases)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout
    ) as client:
        report = await benchmark(
            client,
            cases,
            repetitions=args.repetitions,
            concurrency_levels=args.concurrency,
            warmups=args.warmups,
            timeout=args.timeout,
        )
    report["api_base_url"] = args.base_url
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = (
        args.output
        or Path("benchmark_results/explanations") / f"performance_{timestamp}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(f"Wrote {output}")


if __name__ == "__main__":
    asyncio.run(main())
