"""Read-only, local evaluation of GameRec's recommendation configurations."""

import argparse
import asyncio
import hashlib
import inspect
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.db import SessionLocal
from gamerec.scripts.get_user_game_recommendation import (
    MIN_TOTAL_REVIEWS,
    PROFILE_CANDIDATE_K,
    SEED_CANDIDATE_K,
    SEED_GAME_COUNT,
    TOP_K,
)
from gamerec.services.hybrid_user_recommendation import (
    CandidateAffinityScores,
    RankedCandidate,
    build_candidate_affinity_scores,
    build_ranking_features,
    build_user_affinity_profile,
    filter_eligible_candidates,
    get_candidate_ranking_metadata,
    get_global_genre_statistics,
    get_total_max_reviews,
    get_user_affinity_metadata,
    rank_candidates,
)
from gamerec.services.recommendation_evaluation import (
    diversity_metrics,
    latency_summary,
    list_overlap,
    mmr_rerank,
    relevance_metrics,
)
from gamerec.services.user_profile import build_user_profile_vector, fetch_game_vectors
from gamerec.services.user_recommendation import (
    get_multi_source_recommendation_candidates,
)
from gamerec.services.vector_store import get_qdrant_client

WEIGHT_NAMES = (
    "similarity_weight", "popularity_weight", "review_quality_weight",
    "affinity_weight", "recency_weight",
)
CONTENT_WEIGHTS = dict(zip(WEIGHT_NAMES, (1.0, 0.0, 0.0, 0.0, 0.0), strict=True))
# Historical semantic/popularity/review preset used by the existing hybrid tests.
ORIGINAL_HYBRID_WEIGHTS = dict(
    zip(WEIGHT_NAMES, (0.70, 0.15, 0.15, 0.0, 0.0), strict=True)
)
CURRENT_WEIGHTS = {
    name: inspect.signature(rank_candidates).parameters[name].default
    for name in WEIGHT_NAMES
}
WEIGHTS = {"A": CONTENT_WEIGHTS, "B": ORIGINAL_HYBRID_WEIGHTS, "C": CURRENT_WEIGHTS}
CONFIG_NAMES = {
    "A": "profile_content_only",
    "B": "profile_original_hybrid",
    "C": "multi_vector_current_hybrid",
}


def user_key(steamid64: str) -> str:
    """Stable local identifier that does not write the Steam ID to results."""
    return hashlib.sha256(steamid64.encode()).hexdigest()[:16]


def load_labels(path: Path | None, key: str) -> tuple[dict[int, int], str | None]:
    """Accept a flat app-ID mapping or {'users': {hashed_user_id: mapping}}."""
    if path is None:
        return {}, None
    raw = path.read_bytes()

    def unique_object(pairs):
        values = {}
        for name, value in pairs:
            if name in values:
                raise ValueError(f"Duplicate label key: {name}")
            values[name] = value
        return values

    data = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise TypeError("Labels must be a JSON object")
    if "users" in data:
        users = data["users"]
        if not isinstance(users, dict):
            raise ValueError("Labels 'users' must be an object")
        data = users.get(key, {})
    if not isinstance(data, dict):
        raise TypeError("User labels must be an app-ID mapping")
    labels = {}
    for appid, grade in data.items():
        if (
            not isinstance(appid, str)
            or not appid.isdecimal()
            or int(appid) <= 0
            or str(int(appid)) != appid
        ):
            raise ValueError("Label keys must be positive Steam app IDs")
        if type(grade) is not int or grade not in (0, 1, 2):
            raise ValueError("Relevance grades must be 0, 1 or 2")
        labels[int(appid)] = grade
    return labels, hashlib.sha256(raw).hexdigest()


def candidate_record(
    candidate: RankedCandidate,
    affinity: CandidateAffinityScores | None = None,
    selection_score: float | None = None,
) -> dict:
    return {
        "steam_app_id": candidate.steam_app_id,
        "name": candidate.name,
        "similarity_score": candidate.similarity_score,
        "popularity_score": candidate.popularity_score,
        "review_quality": candidate.review_quality,
        "genre_affinity": affinity.genres_affinity if affinity else None,
        "category_affinity": affinity.categories_affinity if affinity else None,
        "affinity_score": candidate.affinity_score,
        "recency_score": candidate.recency_score,
        "hybrid_score": candidate.hybrid_score,
        "mmr_selection_score": selection_score,
    }


async def run_configuration(
    db: AsyncSession,
    client: AsyncQdrantClient,
    steamid64: str,
    config: str,
    args: argparse.Namespace,
    as_of: date,
) -> dict | None:
    """Time the same start/end boundary: profile through ranked recommendations."""
    start = time.perf_counter()
    profile_result = await build_user_profile_vector(db, steamid64)
    profile_end = time.perf_counter()
    if profile_result is None:
        return None
    profile_vector, owned_ids = profile_result

    generation = await get_multi_source_recommendation_candidates(
        db=db,
        client=client,
        steamid64=steamid64,
        user_profile_vector=profile_vector,
        owned_ids=owned_ids,
        profile_candidate_k=args.profile_candidate_k,
        seed_game_count=args.seed_game_count if config == "C" else 0,
        seed_candidate_k=args.seed_candidate_k,
    )
    retrieval_end = time.perf_counter()

    metadata = await get_candidate_ranking_metadata(
        db, [candidate["steam_app_id"] for candidate in generation.candidates]
    )
    eligible = filter_eligible_candidates(
        generation.candidates, metadata, min_total_reviews=args.min_reviews
    )
    eligible_metadata = {
        candidate["steam_app_id"]: metadata[candidate["steam_app_id"]]
        for candidate in eligible
    }
    features = {}
    affinities = {}
    if config in ("B", "C"):
        max_reviews = await get_total_max_reviews(db)
        features = build_ranking_features(eligible_metadata, max_reviews, as_of=as_of)
    if config == "C":
        user_games = await get_user_affinity_metadata(db, steamid64)
        total_games, genre_counts = await get_global_genre_statistics(db)
        affinity_profile = build_user_affinity_profile(
            user_games, total_games=total_games, genre_counts=genre_counts
        )
        affinities = build_candidate_affinity_scores(eligible_metadata, affinity_profile)
    metadata_end = time.perf_counter()

    ranked = rank_candidates(eligible, features, affinities, **WEIGHTS[config])
    ranking_end = time.perf_counter()
    timings = {
        "profile_ms": (profile_end - start) * 1000,
        "retrieval_ms": (retrieval_end - profile_end) * 1000,
        "metadata_affinity_ms": (metadata_end - retrieval_end) * 1000,
        "ranking_ms": (ranking_end - metadata_end) * 1000,
        "total_ms": (ranking_end - start) * 1000,
    }
    selections = []
    if config == "C" and args.mmr:
        mmr_start = time.perf_counter()
        pool = ranked[:args.mmr_top_n]
        vectors = await fetch_game_vectors(db, [item.steam_app_id for item in pool]) or {}
        selections = mmr_rerank(
            pool, vectors, top_k=args.top_k, diversity_lambda=args.mmr_lambda
        )
        timings["mmr_extra_ms"] = (time.perf_counter() - mmr_start) * 1000
        timings["total_with_mmr_ms"] = timings["total_ms"] + timings["mmr_extra_ms"]

    counts = {
        "profile_candidates": generation.profile_count,
        "seeds_selected": len(generation.seed_ids),
        "seed_candidates_before_dedupe": generation.seed_candidate_count,
        "merged_unique_candidates": generation.merged_count,
        "after_owned_filter": generation.after_owned_count,
        "with_stored_embeddings": len(generation.candidates),
        "after_eligibility_filter": len(eligible),
    }
    return {
        "ranked": ranked,
        "mmr": selections,
        "metadata": eligible_metadata,
        "affinities": affinities,
        "counts": counts,
        "timings": timings,
    }


async def evaluate_user(
    db: AsyncSession,
    client: AsyncQdrantClient,
    steamid64: str,
    args: argparse.Namespace,
    as_of: date,
) -> dict:
    key = user_key(steamid64)
    labels, label_hash = load_labels(args.labels, key)
    samples = {config: {} for config in CONFIG_NAMES}
    snapshots = {}
    for iteration in range(args.warmups + args.runs):
        for config in CONFIG_NAMES:
            result = await run_configuration(db, client, steamid64, config, args, as_of)
            if result is None:
                return {"user_id_hash": key, "status": "no_usable_profile"}
            if iteration >= args.warmups:
                snapshots.setdefault(config, result)
                for phase, duration in result["timings"].items():
                    samples[config].setdefault(phase, []).append(duration)

    top_ids = {
        config: [item.steam_app_id for item in snapshot["ranked"][:args.top_k]]
        for config, snapshot in snapshots.items()
    }
    if args.mmr:
        top_ids["C_mmr"] = [
            selection.candidate.steam_app_id for selection in snapshots["C"]["mmr"]
        ]
    all_top_ids = sorted({appid for ids in top_ids.values() for appid in ids})
    vectors = await fetch_game_vectors(db, all_top_ids) or {}
    output = {
        "user_id_hash": key,
        "status": "evaluated",
        "labels_sha256": label_hash,
        "labelled_games": len(labels),
        "configurations": {},
        "overlap": {},
    }
    for config, snapshot in snapshots.items():
        ids = top_ids[config]
        output["configurations"][config] = {
            "name": CONFIG_NAMES[config],
            "retrieval": {
                "strategy": "multi_vector" if config == "C" else "profile_only",
                "profile_candidate_k": args.profile_candidate_k,
                "seed_game_count": args.seed_game_count if config == "C" else 0,
                "seed_candidate_k": args.seed_candidate_k if config == "C" else 0,
            },
            "ranking_weights": WEIGHTS[config],
            "affinity_playtime_percentile": 90 if config == "C" else None,
            "eligibility_min_reviews": args.min_reviews,
            "candidate_counts": snapshot["counts"],
            "recommendations": [
                candidate_record(item, snapshot["affinities"].get(item.steam_app_id))
                for item in snapshot["ranked"][:args.top_k]
            ],
            "relevance": relevance_metrics(ids, labels, args.top_k),
            "diversity": diversity_metrics(ids, snapshot["metadata"], vectors, args.top_k),
            "latency": {
                phase: latency_summary(values)
                for phase, values in samples[config].items()
            },
        }
    if args.mmr:
        snapshot = snapshots["C"]
        ids = top_ids["C_mmr"]
        output["configurations"]["C_mmr"] = {
            "name": "current_hybrid_with_mmr",
            "same_candidate_pool_as": "C",
            "retrieval": output["configurations"]["C"]["retrieval"],
            "ranking_weights": CURRENT_WEIGHTS,
            "mmr_lambda": args.mmr_lambda,
            "mmr_top_n": args.mmr_top_n,
            "eligibility_min_reviews": args.min_reviews,
            "candidate_counts": snapshot["counts"],
            "recommendations": [
                candidate_record(
                    item.candidate,
                    snapshot["affinities"].get(item.candidate.steam_app_id),
                    item.selection_score,
                )
                for item in snapshot["mmr"]
            ],
            "relevance": relevance_metrics(ids, labels, args.top_k),
            "diversity": diversity_metrics(ids, snapshot["metadata"], vectors, args.top_k),
            "latency": {
                "mmr_extra_ms": latency_summary(samples["C"]["mmr_extra_ms"]),
                "total_with_mmr_ms": latency_summary(samples["C"]["total_with_mmr_ms"]),
            },
        }
    for left, right in (("A", "B"), ("A", "C"), ("B", "C"), ("C", "C_mmr")):
        if left in top_ids and right in top_ids:
            output["overlap"][f"{left}_vs_{right}"] = list_overlap(
                top_ids[left], top_ids[right], args.top_k
            )
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steamid64", action="append", help="May be repeated for multiple users")
    parser.add_argument("--labels", type=Path, help="JSON relevance labels; optional")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/recommendations"))
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--profile-candidate-k", type=int, default=PROFILE_CANDIDATE_K)
    parser.add_argument("--seed-game-count", type=int, default=SEED_GAME_COUNT)
    parser.add_argument("--seed-candidate-k", type=int, default=SEED_CANDIDATE_K)
    parser.add_argument("--min-reviews", type=int, default=MIN_TOTAL_REVIEWS)
    parser.add_argument("--mmr", action="store_true")
    parser.add_argument("--mmr-top-n", type=int, default=100)
    parser.add_argument("--mmr-lambda", type=float, default=0.85)
    args = parser.parse_args(argv)
    if args.warmups < 0 or args.runs <= 0 or args.top_k <= 0:
        parser.error("warmups must be non-negative; runs and top-k must be positive")
    if args.profile_candidate_k <= 0 or args.seed_game_count < 0 or args.seed_candidate_k <= 0:
        parser.error("candidate limits must be positive and seed-game-count non-negative")
    if args.min_reviews < 0 or args.mmr_top_n < args.top_k:
        parser.error("min-reviews must be non-negative and mmr-top-n must cover top-k")
    if not 0 <= args.mmr_lambda <= 1:
        parser.error("mmr-lambda must be within [0, 1]")
    args.steamid64 = args.steamid64 or ([settings.steamid64_test] if settings.steamid64_test else [])
    if not args.steamid64:
        parser.error("provide --steamid64 or configure steamid64_test")
    return args


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    as_of = datetime.now(UTC)
    async with SessionLocal() as db:
        client = get_qdrant_client()
        try:
            users = [
                await evaluate_user(db, client, steamid64, args, as_of.date())
                for steamid64 in dict.fromkeys(args.steamid64)
            ]
        finally:
            await client.close()
    report = {
        "evaluated_at_utc": as_of.isoformat(),
        "top_k": args.top_k,
        "warmup_runs": args.warmups,
        "measured_runs": args.runs,
        "as_of_date": as_of.date().isoformat(),
        "embedding_model": settings.embeddings_model_name,
        "embedding_revision": settings.embeddings_model_revision,
        "qdrant_collection": settings.qdrant_game_collection,
        "users": users,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"evaluation_{as_of.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(f"Wrote {output} for {len(users)} user(s)")


if __name__ == "__main__":
    asyncio.run(main())
