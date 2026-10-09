"""Bounded explanation evaluation, snapshot reuse and honest review coverage."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from gamerec.schemas.explanation import SearchExplanationContext
from gamerec.scripts import evaluate_explanations as script


def case(case_id="stored", app_id=10, query="Cooperative crafting"):
    return {
        "case_id": case_id,
        "steam_app_id": app_id,
        "search_query": query,
        "category": "well_known",
        "reason": "Tests stored descriptive evidence",
    }


def context(app_id=10, query="Cooperative crafting", **kwargs):
    return SearchExplanationContext(
        steam_app_id=app_id,
        name="Stored game",
        search_query=query,
        description="Craft together.",
        **kwargs,
    )


def record(value=None):
    return {**case(), "context": (value or context()).model_dump(mode="json")}


def save_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def chat_response():
    return {
        "model": script.settings.ollama_model,
        "done": True,
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "explanation": "Craft together in this cooperative game.",
                    "matching_features": ["Cooperative crafting"],
                }
            ),
        },
        "prompt_eval_count": 123,
        "eval_count": 24,
    }


def generation(review=None, *, error=None, fallback=False, elapsed=10):
    return {
        "generation_ms": elapsed,
        "error": error,
        "fallback": fallback,
        "human_review": review,
    }


def test_load_cases_empty_and_normalizes_text(tmp_path):
    path = tmp_path / "cases.json"
    save_json(path, [])
    assert script.load_cases(path) == []
    value = case(query="  Cooperative crafting  ")
    save_json(path, [value])
    assert script.load_cases(path)[0]["search_query"] == "Cooperative crafting"


def test_tracked_dataset_covers_requested_groups_and_known_examples():
    cases = script.load_cases(
        Path(__file__).parent / "data" / "explanation_evaluation_cases.json"
    )
    assert 15 <= len(cases) <= 20
    assert {case["category"] for case in cases} == script.CASE_CATEGORIES
    assert {108600, 1091500, 2310, 105600, 1086940} <= {
        case["steam_app_id"] for case in cases
    }


@pytest.mark.parametrize(
    "cases",
    [
        {},
        [case(str(index), index + 1) for index in range(21)],
        [case(), case()],
        [case(), case("different-id")],
        [case(), case(app_id=20, query="Different query")],
        [{**case(), "steam_app_id": True}],
        [{**case(), "steam_app_id": 0}],
        [{**case(), "search_query": " "}],
        [{**case(), "category": "invented"}],
        [{**case(), "extra": "not allowed"}],
        [{key: value for key, value in case().items() if key != "reason"}],
    ],
)
def test_load_cases_rejects_invalid_or_unbounded_inputs(tmp_path, cases):
    with pytest.raises(ValueError):
        script.load_cases(save_json(tmp_path / "cases.json", cases))


@pytest.mark.asyncio
async def test_read_contexts_preserves_exact_json_snapshot_and_missing_game(
    monkeypatch, fake_db
):
    stored = context(release_date=date(2020, 1, 2))
    getter = AsyncMock(side_effect=[stored, None])
    monkeypatch.setattr(script, "get_search_explanation_context", getter)
    cases = [case(), case("missing", 20)]
    records = await script.read_contexts(fake_db, cases)
    assert records[0]["context"]["release_date"] == "2020-01-02"
    assert records[0]["context"]["description"] == stored.description
    assert records[1]["context"] is None
    assert [call.args for call in getter.await_args_list] == [
        (fake_db, 10, cases[0]["search_query"]),
        (fake_db, 20, cases[1]["search_query"]),
    ]
    json.dumps(records, allow_nan=False)


@pytest.mark.parametrize(
    "mutation", ["case", "game", "query", "count", "missing_context"]
)
def test_snapshot_rejects_mismatched_identities(tmp_path, mutation):
    records = [record()]
    if mutation == "case":
        records[0]["case_id"] = "different"
    elif mutation == "game":
        records[0]["context"]["steam_app_id"] = 20
    elif mutation == "query":
        records[0]["context"]["search_query"] = "different"
    elif mutation == "missing_context":
        del records[0]["context"]
    else:
        records = []
    path = save_json(tmp_path / "snapshot.json", {"cases": records})
    with pytest.raises(ValueError):
        script.load_snapshot(path, [case()])


@pytest.mark.parametrize("review", [None, {}])
def test_missing_or_empty_reviews_leave_all_scores_unrated(review):
    summary = script.summarize_results(
        [{"generations": {"revised": generation(review)}}]
    )["revised"]
    assert summary["reviewed_all_dimensions"] == 0
    assert all(
        item == {"rated_count": 0, "mean": None}
        for item in summary["dimensions"].values()
    )
    assert summary["hallucination_verdicts"] == {
        "true": 0,
        "false": 0,
        "undetermined": 0,
        "unrated": 1,
    }


def test_partial_ratings_use_only_supplied_labels_and_preserve_undetermined():
    summary = script.summarize_results(
        [
            {"generations": {"revised": generation({"factual_accuracy": 4})}},
            {
                "generations": {
                    "revised": generation(
                        {
                            "factual_accuracy": 2,
                            "hallucination_detected": "undetermined",
                        }
                    )
                }
            },
            {"generations": {"revised": generation()}},
        ]
    )["revised"]
    assert summary["dimensions"]["factual_accuracy"] == {
        "rated_count": 2,
        "mean": 3,
    }
    assert summary["dimensions"]["usefulness"]["mean"] is None
    assert summary["hallucination_verdicts"]["undetermined"] == 1
    assert summary["hallucination_verdicts"]["unrated"] == 2
    assert script.summarize_results([]) == {}


def test_missing_review_key_leaves_quality_metrics_unrated():
    result = generation()
    del result["human_review"]
    summary = script.summarize_results([{"generations": {"revised": result}}])[
        "revised"
    ]
    assert summary["reviewed_all_dimensions"] == 0
    assert summary["dimensions"]["factual_accuracy"]["mean"] is None
    assert summary["hallucination_verdicts"]["unrated"] == 1


@pytest.mark.parametrize("mutation", [None, "model", "options", "schema", "prompt"])
def test_previous_prompt_requires_comparable_model_settings_and_response_fields(
    tmp_path, mutation
):
    previous = {
        "model": script.settings.ollama_model,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512},
        "system_prompt": "Exact saved instructions",
        "response_schema": script.SearchExplanation.model_json_schema(),
    }
    if mutation == "model":
        previous["model"] = "different-model"
    elif mutation == "options":
        previous["options"]["temperature"] = 1
    elif mutation == "schema":
        previous["response_schema"]["properties"]["limitations"] = {"type": "array"}
    elif mutation == "prompt":
        previous["system_prompt"] = " "
    path = save_json(tmp_path / "previous.json", previous)
    if mutation:
        with pytest.raises(ValueError):
            script.load_previous_prompt(path)
    else:
        assert script.load_previous_prompt(path) == previous


@pytest.mark.parametrize(
    "review",
    [
        {"factual_accuracy": 0},
        {"factual_accuracy": 6},
        {"query_relevance": True},
        {"naturalness": 2.5},
        {"hallucination_detected": "yes"},
        {"extra": 3},
    ],
)
def test_review_validation_rejects_invalid_labels(review):
    with pytest.raises(ValueError):
        script.validate_review(review)


def test_review_validation_rejects_nonstring_notes():
    with pytest.raises(TypeError, match="notes"):
        script.validate_review({"notes": None})


@pytest.mark.asyncio
async def test_evaluate_records_real_transport_results_and_separates_warmup():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=chat_response())

    records = [record(context(release_date=date(2020, 1, 2)))]
    async with httpx.AsyncClient(
        base_url="http://ollama", transport=httpx.MockTransport(handler)
    ) as client:
        report = await script.evaluate(client, records, warmups=1)
    result = report["cases"][0]["generations"]["revised"]
    assert set(result["output"]) == {"explanation", "matching_features"}
    assert result["input_tokens"] == 123
    assert result["output_tokens"] == 24
    assert result["model"] == script.settings.ollama_model
    assert result["error"] is None
    assert result["fallback"] is False
    assert result["generation_ms"] >= 0
    assert result["human_review"]["factual_accuracy"] is None
    assert report["cases"][0]["context"] == records[0]["context"]
    assert len(requests) == 2
    assert len(report["excluded_warmups"]["revised"]) == 1
    assert report["summary"]["revised"]["case_count"] == 1
    json.dumps(report, allow_nan=False)


@pytest.mark.asyncio
async def test_inference_error_fallback_and_missing_game_do_not_stop_evaluation():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    sparse = SearchExplanationContext(
        steam_app_id=20, name="Sparse game", search_query="Cooperative crafting"
    )
    records = [
        record(),
        {**case("sparse", 20), "context": sparse.model_dump(mode="json")},
        {**case("missing", 30), "context": None},
    ]
    async with httpx.AsyncClient(
        base_url="http://ollama", transport=httpx.MockTransport(handler)
    ) as client:
        report = await script.evaluate(client, records, warmups=0)
    results = [item["generations"]["revised"] for item in report["cases"]]
    assert results[0]["error"]["type"] == "OllamaUnavailableError"
    assert results[0]["output"] is None
    assert results[1]["fallback"] is True
    assert results[1]["output"]["matching_features"] == []
    assert results[1]["input_tokens"] is None
    assert results[2]["error"]["type"] == "MissingGame"
    assert calls == 1
    assert report["summary"]["revised"]["error_count"] == 2
    assert report["summary"]["revised"]["fallback_count"] == 1
    latency = report["summary"]["revised"]["warm_model_latency_ms"]
    assert latency["count"] == 0
    assert latency["median_ms"] is None
    assert latency["p95_ms"] is None


@pytest.mark.asyncio
async def test_previous_prompt_uses_same_context_and_settings_for_both_versions():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=chat_response())

    previous = {
        "system_prompt": "Exact saved previous instructions",
        "response_schema": script.SearchExplanation.model_json_schema(),
    }
    async with httpx.AsyncClient(
        base_url="http://ollama", transport=httpx.MockTransport(handler)
    ) as client:
        report = await script.evaluate(client, [record()], previous=previous, warmups=0)
    assert len(requests) == 2
    assert requests[0]["messages"][0]["content"] == previous["system_prompt"]
    assert requests[1]["messages"][0]["content"] == script.SYSTEM_PROMPT
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert requests[0]["model"] == requests[1]["model"]
    assert requests[0]["options"] == requests[1]["options"]
    assert set(report["cases"][0]["generations"]) == {"previous", "revised"}


@pytest.mark.asyncio
async def test_empty_evaluation_makes_no_requests_or_warmups():
    def forbidden(request):
        pytest.fail("Empty cases must not call Ollama")

    async with httpx.AsyncClient(
        base_url="http://ollama", transport=httpx.MockTransport(forbidden)
    ) as client:
        report = await script.evaluate(client, [], warmups=3)
    assert report["cases"] == []
    assert report["summary"] == {}
    assert report["excluded_warmups"] == {"revised": []}


@pytest.mark.parametrize("warmups", ["-1", "4"])
def test_cli_bounds_warmup_requests(warmups):
    with pytest.raises(SystemExit):
        script.parse_args(["--warmups", warmups])


def test_write_json_refuses_to_overwrite_existing_evidence(tmp_path):
    path = tmp_path / "report.json"
    script.write_json(path, {"initial": True})
    with pytest.raises(FileExistsError):
        script.write_json(path, {"initial": False})
    assert json.loads(path.read_text()) == {"initial": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "metadata", "evaluation"])
async def test_main_closes_database_client_and_engine_on_failures(
    monkeypatch, tmp_path, failure
):
    cases_path = save_json(tmp_path / "cases.json", [case()])
    session = AsyncMock()
    client = AsyncMock()
    client.__aenter__.return_value = client
    read = AsyncMock(return_value=[record()])
    evaluate = AsyncMock(return_value={"cases": [], "summary": {}})
    if failure == "metadata":
        read.side_effect = RuntimeError("metadata failed")
    if failure == "evaluation":
        evaluate.side_effect = RuntimeError("evaluation failed")
    dispose = AsyncMock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(script, "SessionLocal", lambda: session)
    monkeypatch.setattr(script, "read_contexts", read)
    monkeypatch.setattr(script, "create_ollama_client", factory)
    monkeypatch.setattr(script, "evaluate", evaluate)
    monkeypatch.setattr(script, "engine", Mock(dispose=dispose))
    argv = ["--cases", str(cases_path), "--output-dir", str(tmp_path / "results")]
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            await script.main(argv)
    else:
        await script.main(argv)
        assert len(list((tmp_path / "results").glob("evaluation_*.json"))) == 1
    session.__aexit__.assert_awaited_once()
    dispose.assert_awaited_once_with()
    if failure != "metadata":
        factory.assert_called_once_with()
        client.__aexit__.assert_awaited_once()
    else:
        factory.assert_not_called()


@pytest.mark.asyncio
async def test_main_reuses_snapshot_without_database_lookup(monkeypatch, tmp_path):
    cases_path = save_json(tmp_path / "cases.json", [case()])
    snapshot_path = save_json(tmp_path / "saved.json", {"cases": [record()]})
    session_factory = Mock(side_effect=AssertionError("Must not read database"))
    client = AsyncMock()
    client.__aenter__.return_value = client
    evaluate = AsyncMock(return_value={"cases": [], "summary": {}})
    dispose = AsyncMock()
    monkeypatch.setattr(script, "SessionLocal", session_factory)
    monkeypatch.setattr(script, "create_ollama_client", lambda: client)
    monkeypatch.setattr(script, "evaluate", evaluate)
    monkeypatch.setattr(script, "engine", Mock(dispose=dispose))
    await script.main(
        [
            "--cases",
            str(cases_path),
            "--snapshot",
            str(snapshot_path),
            "--output-dir",
            str(tmp_path / "results"),
            "--warmups",
            "0",
        ]
    )
    session_factory.assert_not_called()
    assert evaluate.await_args.args[1] == [record()]
    assert evaluate.await_args.kwargs["warmups"] == 0
    client.__aexit__.assert_awaited_once()
    dispose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_review_only_mode_never_opens_model_or_database(
    monkeypatch, tmp_path, capsys
):
    path = save_json(
        tmp_path / "review.json",
        {"cases": [{"generations": {"revised": generation()}}]},
    )
    forbidden = Mock(side_effect=AssertionError("Review must be offline"))
    dispose = AsyncMock()
    monkeypatch.setattr(script, "SessionLocal", forbidden)
    monkeypatch.setattr(script, "create_ollama_client", forbidden)
    monkeypatch.setattr(script, "engine", Mock(dispose=dispose))
    await script.main(["--review-file", str(path)])
    assert (
        json.loads(capsys.readouterr().out)["revised"]["reviewed_all_dimensions"] == 0
    )
    forbidden.assert_not_called()
    dispose.assert_awaited_once_with()
