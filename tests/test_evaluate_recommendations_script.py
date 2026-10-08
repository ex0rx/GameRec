"""Evaluation script labels, configuration output, and local JSON writing."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gamerec.scripts import evaluate_recommendations as evaluation
from gamerec.services.hybrid_user_recommendation import (
    CandidateRankingMetadata,
    RankedCandidate,
)
from gamerec.services.recommendation_evaluation import MMRSelection
from gamerec.services.user_recommendation import CandidateGenerationResult


def ranked(appid, score):
    return RankedCandidate(appid, f"Game {appid}", score, 0, 0, score, 0)


def metadata(appid):
    return CandidateRankingMetadata(appid, 1000, 900, 100, ["Action"], None)


def snapshot(ids, *, mmr=False):
    candidates = [ranked(appid, 1 - index / 10) for index, appid in enumerate(ids)]
    return {
        "ranked": candidates,
        "mmr": [MMRSelection(candidates[1], 0.7), MMRSelection(candidates[0], 0.6)]
        if mmr else [],
        "metadata": {appid: metadata(appid) for appid in ids},
        "affinities": {},
        "counts": {
            "profile_candidates": 3,
            "seeds_selected": 1 if mmr else 0,
            "seed_candidates_before_dedupe": 2 if mmr else 0,
            "merged_unique_candidates": 3,
            "after_owned_filter": 3,
            "with_stored_embeddings": 3,
            "after_eligibility_filter": 2,
        },
        "timings": {
            "profile_ms": 1.0,
            "retrieval_ms": 2.0,
            "metadata_affinity_ms": 3.0,
            "ranking_ms": 4.0,
            "total_ms": 10.0,
            **({"mmr_extra_ms": 2.0, "total_with_mmr_ms": 12.0} if mmr else {}),
        },
    }


def test_labels_flat_nested_and_invalid(tmp_path):
    path = tmp_path / "labels.json"
    key = evaluation.user_key("synthetic-user")
    path.write_text('{"10": 2, "20": 0}', encoding="utf-8")
    labels, fingerprint = evaluation.load_labels(path, key)
    assert labels == {10: 2, 20: 0}
    assert len(fingerprint) == 64

    path.write_text(json.dumps({"users": {key: {"30": 1}}}), encoding="utf-8")
    assert evaluation.load_labels(path, key)[0] == {30: 1}
    assert evaluation.load_labels(path, "another-user")[0] == {}

    path.write_text('{"10": 3}', encoding="utf-8")
    with pytest.raises(ValueError, match="grades"):
        evaluation.load_labels(path, key)

    path.write_text('{"10": 2, "10": 0}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        evaluation.load_labels(path, key)


@pytest.mark.asyncio
async def test_configuration_reuses_pipeline_and_filters_eligibility(monkeypatch):
    db = SimpleNamespace()
    client = SimpleNamespace()
    args = SimpleNamespace(
        profile_candidate_k=5000, seed_game_count=10, seed_candidate_k=100,
        min_reviews=500, mmr=True, mmr_top_n=100, mmr_lambda=0.85, top_k=20,
    )
    profile = AsyncMock(return_value=([1.0, 0.0], {9}))
    generation = AsyncMock(return_value=CandidateGenerationResult(
        candidates=[
            {"steam_app_id": 1, "name": "Eligible", "score": 0.9},
            {"steam_app_id": 2, "name": "Too few reviews", "score": 0.8},
        ],
        profile_count=3, seed_ids=[9], seed_candidate_count=2,
        merged_count=4, after_owned_count=2,
    ))
    metadata_lookup = AsyncMock(return_value={
        1: metadata(1),
        2: CandidateRankingMetadata(2, 5, 4, 1, ["Action"], None),
    })
    monkeypatch.setattr(evaluation, "build_user_profile_vector", profile)
    monkeypatch.setattr(evaluation, "get_multi_source_recommendation_candidates", generation)
    monkeypatch.setattr(evaluation, "get_candidate_ranking_metadata", metadata_lookup)
    monkeypatch.setattr(evaluation, "get_total_max_reviews", AsyncMock(return_value=1000))
    monkeypatch.setattr(evaluation, "get_user_affinity_metadata", AsyncMock(return_value=[]))
    monkeypatch.setattr(evaluation, "get_global_genre_statistics", AsyncMock(return_value=(0, {})))
    monkeypatch.setattr(evaluation, "fetch_game_vectors", AsyncMock(return_value={1: [1.0, 0.0]}))

    current = await evaluation.run_configuration(
        db, client, "synthetic-user", "C", args, date(2030, 1, 1)
    )
    assert generation.await_args.kwargs["owned_ids"] == {9}
    assert generation.await_args.kwargs["seed_game_count"] == 10
    assert current["counts"]["after_eligibility_filter"] == 1
    assert [item.steam_app_id for item in current["ranked"]] == [1]
    assert [item.candidate.steam_app_id for item in current["mmr"]] == [1]
    assert current["mmr"][0].candidate.hybrid_score == current["ranked"][0].hybrid_score

    await evaluation.run_configuration(
        db, client, "synthetic-user", "A", args, date(2030, 1, 1)
    )
    assert generation.await_args.kwargs["seed_game_count"] == 0


@pytest.mark.asyncio
async def test_evaluate_user_has_consistent_labels_counts_and_mmr_pool(monkeypatch, tmp_path):
    key = evaluation.user_key("synthetic-user")
    label_path = tmp_path / "labels.json"
    label_path.write_text(json.dumps({"users": {key: {"1": 2, "2": 1, "3": 0}}}), encoding="utf-8")
    args = SimpleNamespace(
        labels=label_path, warmups=1, runs=2, top_k=2, mmr=True,
        profile_candidate_k=1000, seed_game_count=8, seed_candidate_k=100,
        min_reviews=500, mmr_lambda=0.85, mmr_top_n=100,
    )
    configurations = {
        "A": snapshot([1, 2]),
        "B": snapshot([2, 3]),
        "C": snapshot([1, 3], mmr=True),
    }
    run = AsyncMock(side_effect=lambda db, client, steamid64, config, args, as_of: configurations[config])
    vectors = AsyncMock(return_value={
        1: [1.0, 0.0], 2: [1.0, 0.0], 3: [0.0, 1.0]
    })
    monkeypatch.setattr(evaluation, "run_configuration", run)
    monkeypatch.setattr(evaluation, "fetch_game_vectors", vectors)

    report = await evaluation.evaluate_user(
        SimpleNamespace(), SimpleNamespace(), "synthetic-user", args, date(2030, 1, 1)
    )

    assert run.await_count == 9  # Three configurations in one warm-up and two samples.
    assert report["user_id_hash"] == key
    assert "synthetic-user" not in json.dumps(report)
    assert report["configurations"]["A"]["relevance"]["complete"] is True
    assert report["configurations"]["B"]["ranking_weights"] == evaluation.ORIGINAL_HYBRID_WEIGHTS
    assert report["configurations"]["C"]["candidate_counts"]["seeds_selected"] == 1
    assert report["configurations"]["C_mmr"]["same_candidate_pool_as"] == "C"
    assert report["configurations"]["C_mmr"]["recommendations"][0]["mmr_selection_score"] == 0.7
    assert report["configurations"]["C_mmr"]["recommendations"][0]["hybrid_score"] == 0.9
    assert report["configurations"]["C"]["latency"]["total_ms"]["count"] == 2
    assert report["overlap"]["C_vs_C_mmr"]["shared_count"] == 2
    vectors.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_writes_new_machine_readable_file_without_raw_user_id(monkeypatch, tmp_path):
    class FakeSession:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    client = SimpleNamespace(close=AsyncMock())
    evaluate = AsyncMock(return_value={"user_id_hash": evaluation.user_key("synthetic-user"), "status": "evaluated"})
    monkeypatch.setattr(evaluation, "SessionLocal", FakeSession)
    monkeypatch.setattr(evaluation, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(evaluation, "evaluate_user", evaluate)

    await evaluation.main([
        "--steamid64", "synthetic-user", "--runs", "1", "--warmups", "0",
        "--output-dir", str(tmp_path),
    ])

    output = next(tmp_path.glob("evaluation_*.json"))
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["measured_runs"] == 1
    assert report["users"][0]["user_id_hash"] == evaluation.user_key("synthetic-user")
    assert "synthetic-user" not in output.read_text(encoding="utf-8")
    client.close.assert_awaited_once()
