"""Deterministic relevance, diversity, latency and MMR checks."""

from math import log2

import pytest

from gamerec.services.hybrid_user_recommendation import (
    CandidateRankingMetadata,
    RankedCandidate,
)
from gamerec.services.recommendation_evaluation import (
    diversity_metrics,
    latency_summary,
    list_overlap,
    mmr_rerank,
    relevance_metrics,
)


def ranked(appid, score):
    return RankedCandidate(appid, f"Game {appid}", score, 0, 0, score, 0)


def metadata(appid, genres):
    return CandidateRankingMetadata(appid, 100, 90, 10, genres, None)


def test_relevance_grades_ndcg_and_duplicates():
    labels = {1: 2, 2: 1, 3: 0, 4: 2}
    result = relevance_metrics([1, 1, 2, 3], labels, 3)
    ideal = 3 + 3 / log2(3) + 1 / log2(4)
    actual = 3 + 1 / log2(3)

    assert result["recommended_count"] == 3
    assert result["labelled_coverage"] == 1
    assert result["average_relevance_at_k"] == 1
    assert result["relevant_at_k"] == 2
    assert result["strong_at_k"] == 1
    assert result["ndcg_at_k"] == pytest.approx(actual / ideal)
    assert relevance_metrics([1, 4], labels, 2)["ndcg_at_k"] == 1


def test_relevance_missing_and_empty_are_explicit():
    partial = relevance_metrics([1, 2, 2], {1: 2}, 3)
    assert partial["labelled_count"] == 1
    assert partial["labelled_coverage"] == 0.5
    assert partial["missing_label_ids"] == [2]
    assert partial["complete"] is False
    assert partial["average_relevance_at_k"] is None
    assert partial["ndcg_at_k"] is None
    empty = relevance_metrics([], {}, 20)
    assert empty["recommended_count"] == 0
    assert empty["complete"] is False
    assert empty["ndcg_at_k"] is None
    assert relevance_metrics([1], {1: 0}, 1)["ndcg_at_k"] == 0


def test_diversity_pairwise_cosine_genres_and_missing_vectors():
    games = {
        1: metadata(1, ["Action", "Free To Play"]),
        2: metadata(2, ["Action"]),
        3: metadata(3, ["Puzzle"]),
    }
    vectors = {1: [1.0, 0.0], 2: [1.0, 0.0], 3: [0.0, 1.0]}
    result = diversity_metrics([1, 1, 2, 3], games, vectors, 3)
    assert result["unique_recommended_ids"] == 3
    assert result["genres"] == ["Action", "Puzzle"]
    assert result["genre_coverage"] == 2
    assert result["pairwise_comparisons"] == 3
    assert result["average_pairwise_cosine"] == pytest.approx(1 / 3)

    missing = diversity_metrics([1, 2, 3], games, {1: [1.0, 0.0], 3: [0.0, 1.0]}, 3)
    assert missing["pairwise_comparisons"] == 1
    assert missing["average_pairwise_cosine"] == 0
    assert diversity_metrics([1], games, vectors, 10)["average_pairwise_cosine"] is None
    assert diversity_metrics([], games, vectors, 10)["average_pairwise_cosine"] is None


def test_overlap_and_latency_statistics():
    assert list_overlap([1, 1, 2], [2, 3], 2) == {
        "shared_count": 1, "shared_ids": [2], "jaccard": pytest.approx(1 / 3)
    }
    assert list_overlap([], [], 20)["jaccard"] is None
    summary = latency_summary(list(range(1, 21)))
    assert summary == {
        "count": 20, "median_ms": 10.5, "p95_ms": 19, "min_ms": 1, "max_ms": 20
    }
    assert latency_summary([])["median_ms"] is None
    with pytest.raises(ValueError, match="Latency"):
        latency_summary([float("nan")])


def test_mmr_lambda_one_keeps_relevance_and_lower_lambda_selects_diverse_game():
    candidates = [ranked(1, 1.0), ranked(2, 0.95), ranked(3, 0.90)]
    original = list(candidates)
    vectors = {1: [1.0, 0.0], 2: [1.0, 0.0], 3: [0.0, 1.0]}

    assert [item.candidate.steam_app_id for item in mmr_rerank(
        candidates, vectors, top_k=3, diversity_lambda=1
    )] == [1, 2, 3]
    result = mmr_rerank(candidates, vectors, top_k=2, diversity_lambda=0.5)
    assert [item.candidate.steam_app_id for item in result] == [1, 3]
    assert result[1].selection_score == pytest.approx(0.45)
    assert result[1].candidate.hybrid_score == 0.90
    assert candidates == original


def test_mmr_ties_duplicates_missing_vectors_and_short_pools():
    candidates = [ranked(2, 0.9), ranked(1, 0.9), ranked(1, 0.9), ranked(3, 0.8)]
    vectors = {1: [1.0, 0.0], 2: [0.0, 1.0]}
    first = mmr_rerank(candidates, vectors, top_k=5)
    second = mmr_rerank(candidates, vectors, top_k=5)
    assert [item.candidate.steam_app_id for item in first] == [1, 2]
    assert first == second
    assert mmr_rerank(candidates, vectors, top_k=0) == []
    assert mmr_rerank([], vectors, top_k=5) == []
    with pytest.raises(ValueError, match="diversity_lambda"):
        mmr_rerank(candidates, vectors, top_k=2, diversity_lambda=1.1)
