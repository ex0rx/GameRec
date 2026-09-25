"""Focused checks for the retrieval benchmark helpers."""

from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient

from gamerec.scripts.benchmark_retrieval import (
    benchmark_corpus,
    latency_stats,
    select_queries,
    top_k_overlap,
)


def test_latency_stats_uses_median_and_nearest_rank_p95():
    assert latency_stats([0.004, 0.001, 0.002, 0.003]) == {
        "median_ms": 2.5,
        "p95_ms": 4.0,
    }


def test_latency_stats_rejects_empty_samples():
    with pytest.raises(ValueError, match="At least one timing sample"):
        latency_stats([])


@pytest.mark.parametrize(
    "exact,approximate,k,expected",
    [
        ([1, 2, 3], [1, 2, 3], 3, 1.0),
        ([1, 2, 3], [3, 4, 5], 3, 1 / 3),
        ([1, 2, 3, 4], [4, 3, 2, 1], 2, 0.0),
        ([1, 2, 3], [2, 2, 2], 3, 1 / 3),
    ],
)
def test_top_k_overlap(exact, approximate, k, expected):
    assert top_k_overlap(exact, approximate, k) == pytest.approx(expected)


def test_top_k_overlap_rejects_nonpositive_k():
    with pytest.raises(ValueError, match="k must be positive"):
        top_k_overlap([1], [1], 0)


def test_select_queries_deduplicates_and_reports_missing_ids():
    found, missing = select_queries([20, 10, 20, 99, 30, 99], {10: [], 20: []})

    assert found == [20, 10]
    assert missing == [99, 30]


@pytest.mark.asyncio
async def test_benchmark_corpus_compares_rankings_and_uses_effective_k():
    corpus = {
        1: [1.0, 0.0],
        2: [0.8, 0.6],
        3: [0.0, 1.0],
    }
    client = AsyncQdrantClient(":memory:")
    try:
        report = await benchmark_corpus(
            client,
            corpus,
            {1: corpus[1], 99: [0.0, 1.0]},
            top_k=5,
            warmups=1,
            repeats=2,
        )
        assert not (await client.get_collections()).collections
    finally:
        await client.close()

    assert report["corpus_size"] == 3
    assert report["query_count"] == 2
    assert report["average_overlap"] == pytest.approx(1.0)
    details = {query["steam_app_id"]: query for query in report["queries"]}
    assert details[1]["effective_k"] == 2
    assert details[99]["effective_k"] == 3
    assert details[1]["results_last_repeat"]["brute_force"] == [2, 3]
    assert details[1]["results_last_repeat"]["qdrant"] == [2, 3]
    assert 1 not in details[1]["results_last_repeat"]["qdrant"]
    assert details[99]["results_last_repeat"]["brute_force"] == [3, 2, 1]
    assert details[99]["results_last_repeat"]["qdrant"] == [3, 2, 1]
    assert len(details[1]["overlap_per_repeat"]) == 2
    assert set(report["latency"]) == {"brute_force", "qdrant"}
    for method in report["latency"]:
        samples = [
            sample
            for detail in report["queries"]
            for sample in detail["samples_seconds"][method]
        ]
        assert len(samples) == 4  # Two queries x two repeats; warmups excluded.
        assert report["latency"][method] == latency_stats(samples)


@pytest.mark.asyncio
async def test_benchmark_corpus_reports_queries_without_candidates():
    client = AsyncQdrantClient(":memory:")
    try:
        report = await benchmark_corpus(
            client,
            {1: [1.0, 0.0]},
            {1: [1.0, 0.0]},
            top_k=3,
            warmups=1,
            repeats=1,
        )
    finally:
        await client.close()

    assert report["query_count"] == 0
    assert report["average_overlap"] is None
    assert report["queries_without_candidates"] == [1]
    assert report["latency"] == {}


@pytest.mark.asyncio
async def test_benchmark_corpus_deletes_collection_after_query_failure(monkeypatch):
    client = AsyncQdrantClient(":memory:")
    delete = AsyncMock(wraps=client.delete_collection)
    monkeypatch.setattr(client, "delete_collection", delete)
    monkeypatch.setattr(
        client, "query_points", AsyncMock(side_effect=RuntimeError("boom"))
    )

    try:
        with pytest.raises(RuntimeError, match="boom"):
            await benchmark_corpus(
                client,
                {1: [1.0, 0.0], 2: [0.0, 1.0]},
                {1: [1.0, 0.0]},
                top_k=1,
                warmups=1,
                repeats=1,
            )
    finally:
        await client.close()

    delete.assert_awaited_once()
    assert delete.await_args.kwargs == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corpus,queries,top_k,warmups,repeats",
    [
        ({}, {1: [1.0]}, 1, 1, 1),
        ({1: [1.0]}, {}, 1, 1, 1),
        ({1: [1.0]}, {1: [1.0]}, 0, 1, 1),
        ({1: [1.0]}, {1: [1.0]}, 1, 0, 1),
        ({1: [1.0]}, {1: [1.0]}, 1, 1, 0),
    ],
)
async def test_benchmark_corpus_rejects_empty_or_nonpositive_inputs(
    corpus, queries, top_k, warmups, repeats
):
    client = AsyncQdrantClient(":memory:")
    try:
        with pytest.raises(ValueError, match="Nonempty corpus"):
            await benchmark_corpus(
                client,
                corpus,
                queries,
                top_k=top_k,
                warmups=warmups,
                repeats=repeats,
            )
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corpus,queries",
    [
        ({1: [1.0, 0.0], 2: [0.0]}, {1: [1.0, 0.0]}),
        ({1: [1.0, 0.0]}, {1: [float("inf"), 0.0]}),
        ({1: [0.0, 0.0]}, {1: [1.0, 0.0]}),
    ],
)
async def test_benchmark_corpus_rejects_invalid_vectors(corpus, queries):
    client = AsyncQdrantClient(":memory:")
    try:
        with pytest.raises(ValueError):
            await benchmark_corpus(
                client,
                corpus,
                queries,
                top_k=1,
                warmups=1,
                repeats=1,
            )
    finally:
        await client.close()
