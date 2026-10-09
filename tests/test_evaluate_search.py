"""Search export, manual labels, and HTTP benchmark checks."""

import httpx
import pytest

from gamerec.schemas.search import SearchGameResponse, SearchGamesResponse
from gamerec.scripts.evaluate_search import (
    BENCHMARK_QUERY_IDS,
    SEARCH_QUERIES,
    benchmark_query,
    evaluate,
    evaluate_result,
    load_labels,
    parse_args,
)


def response(count=10):
    return SearchGamesResponse(
        query="test query",
        total=count,
        limit=10,
        games=[
            SearchGameResponse(
                steam_app_id=appid,
                name=f"Game {appid}",
                similarity_score=0.8,
                popularity_score=0.7,
                review_quality=0.6,
                hybrid_score=0.7,
            )
            for appid in range(1, count + 1)
        ],
    )


def test_query_benchmark_is_fixed_and_covers_requested_topics():
    assert len(SEARCH_QUERIES) == 12
    assert len({query_id for query_id, _ in SEARCH_QUERIES}) == 12
    assert set(BENCHMARK_QUERY_IDS) <= {query_id for query_id, _ in SEARCH_QUERIES}


def test_labels_validate_grades_ids_and_duplicate_keys(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text('{"open_world_rpg": {"1": 2, "2": 0}}')
    assert load_labels(path) == {"open_world_rpg": {1: 2, 2: 0}}

    for bad in (
        '{"open_world_rpg": {"1": 3}}',
        '{"open_world_rpg": {"0": 1}}',
        '{"open_world_rpg": {"1": 1, "1": 2}}',
        '{"unknown": {"1": 2}}',
    ):
        path.write_text(bad)
        with pytest.raises(ValueError):
            load_labels(path)


def test_metrics_require_all_ten_manual_labels_and_document_precision_threshold():
    labels = {
        appid: (2 if appid <= 3 else 1 if appid <= 5 else 0) for appid in range(1, 11)
    }
    rated = evaluate_result("open_world_rpg", response(), labels)
    assert rated["sufficient_labels"] is True
    assert rated["precision_at_10"] == 0.5  # Grades 1 and 2 are relevant.
    assert rated["average_graded_relevance_at_10"] == 0.8
    assert 0 <= rated["ndcg_at_10"] <= 1
    assert rated["missing_label_ids"] == []
    assert rated["games"][0]["relevance_grade"] == 2
    assert (
        evaluate_result("open_world_rpg", response(), {**labels, 999: 2})["ndcg_at_10"]
        == rated["ndcg_at_10"]
    )

    partial = evaluate_result("open_world_rpg", response(), {1: 2})
    assert partial["labelled_count"] == 1
    assert partial["labelled_coverage"] == 0.1
    assert partial["missing_label_ids"] == list(range(2, 11))
    assert partial["precision_at_10"] is None
    assert partial["ndcg_at_10"] is None
    assert partial["average_graded_relevance_at_10"] is None
    assert partial["games"][1]["relevance_grade"] is None

    short = evaluate_result("open_world_rpg", response(2), {1: 2, 2: 1})
    assert short["unfilled_top_10_slots"] == 8
    assert short["sufficient_labels"] is False
    assert short["precision_at_10"] is None


@pytest.mark.asyncio
async def test_evaluation_uses_endpoint_and_preserves_query_order():
    requests = []

    def handler(request):
        requests.append(request)
        query = request.url.params["query"]
        return httpx.Response(
            200,
            json={**response().model_dump(), "query": query},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        report = await evaluate(client, {}, warmups=2, runs=20)

    assert [item["query_id"] for item in report["queries"]] == [
        query_id for query_id, _ in SEARCH_QUERIES
    ]
    assert all(item["returned_count"] == 10 for item in report["queries"])
    assert all(item["precision_at_10"] is None for item in report["queries"])
    assert [item["query_id"] for item in report["performance"]] == list(
        BENCHMARK_QUERY_IDS
    )
    assert len(requests) == len(SEARCH_QUERIES) + 3 * 22
    assert all(request.url.path == "/search/games" for request in requests)
    assert all(request.url.params["limit"] == "10" for request in requests)


@pytest.mark.asyncio
async def test_http_benchmark_counts_errors_and_excludes_warmups():
    attempts = 0

    def handler(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 5:
            return httpx.Response(503, json={"detail": "Unavailable"})
        return httpx.Response(200, json=response().model_dump())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await benchmark_query(client, "test query", warmups=2, runs=20)

    assert attempts == 22
    assert result["warmups"] == 2
    assert result["measured_requests"] == 20
    assert result["error_count"] == 1
    assert result["successful_latency_ms"]["count"] == 19
    assert (
        result["successful_latency_ms"]["min_ms"]
        <= result["successful_latency_ms"]["median_ms"]
        <= result["successful_latency_ms"]["p95_ms"]
    )


def test_benchmark_request_counts_are_fixed():
    assert parse_args([]).warmups == 2
    assert parse_args([]).runs == 20
    with pytest.raises(SystemExit):
        parse_args(["--runs", "19"])
