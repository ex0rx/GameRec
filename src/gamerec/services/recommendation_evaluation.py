"""Pure metrics and optional diversity selection for recommendation experiments."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import ceil, isfinite, log2
from statistics import median

from gamerec.ml.similarity import cosine_similarity
from gamerec.services.hybrid_user_recommendation import (
    CandidateRankingMetadata,
    RankedCandidate,
    filter_affinity_genres,
)


def unique_ids(app_ids: Sequence[int], k: int) -> list[int]:
    """Keep first occurrences, then limit to K distinct recommendations."""
    if k < 0:
        raise ValueError("k must be non-negative")
    return list(dict.fromkeys(app_ids))[:k]


def relevance_metrics(
    app_ids: Sequence[int], labels: Mapping[int, int], k: int
) -> dict:
    ids = unique_ids(app_ids, k)
    missing = [appid for appid in ids if appid not in labels]
    labelled = len(ids) - len(missing)
    result = {
        "requested_k": k,
        "recommended_count": len(ids),
        "labelled_count": labelled,
        "labelled_coverage": labelled / len(ids) if ids else 0.0,
        "missing_label_ids": missing,
        "complete": bool(ids) and not missing,
        "average_relevance_at_k": None,
        "relevant_at_k": None,
        "strong_at_k": None,
        "ndcg_at_k": None,
    }
    if not result["complete"]:
        return result

    grades = [labels[appid] for appid in ids]
    if any(grade not in (0, 1, 2) for grade in labels.values()):
        raise ValueError("Relevance grades must be 0, 1 or 2")
    dcg = sum((2**grade - 1) / log2(rank + 2) for rank, grade in enumerate(grades))
    ideal = sorted(labels.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / log2(rank + 2) for rank, grade in enumerate(ideal))
    result.update({
        "average_relevance_at_k": sum(grades) / len(ids),
        "relevant_at_k": sum(grade >= 1 for grade in grades),
        "strong_at_k": sum(grade == 2 for grade in grades),
        "ndcg_at_k": dcg / idcg if idcg else 0.0,
    })
    return result


def _usable_cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if a is None or b is None:
        return None
    try:
        score = cosine_similarity(a, b)
    except ValueError:
        return None
    return score if isfinite(score) else None


def diversity_metrics(
    app_ids: Sequence[int],
    metadata: Mapping[int, CandidateRankingMetadata],
    vectors: Mapping[int, list[float]],
    k: int,
) -> dict:
    ids = unique_ids(app_ids, k)
    genres = sorted({
        genre
        for appid in ids
        if appid in metadata
        for genre in filter_affinity_genres(metadata[appid].genres)
    })
    similarities = [
        score
        for left, right in combinations(ids, 2)
        if (score := _usable_cosine(vectors.get(left), vectors.get(right))) is not None
    ]
    return {
        "unique_recommended_ids": len(ids),
        "genre_coverage": len(genres),
        "genres": genres,
        "embedded_recommendations": sum(
            _usable_cosine(vectors.get(appid), vectors.get(appid)) is not None
            for appid in ids
        ),
        "pairwise_comparisons": len(similarities),
        "average_pairwise_cosine": (
            sum(similarities) / len(similarities) if similarities else None
        ),
    }


def list_overlap(left: Sequence[int], right: Sequence[int], k: int) -> dict:
    left_ids = set(unique_ids(left, k))
    right_ids = set(unique_ids(right, k))
    shared = left_ids & right_ids
    union = left_ids | right_ids
    return {
        "shared_count": len(shared),
        "shared_ids": sorted(shared),
        "jaccard": len(shared) / len(union) if union else None,
    }


def latency_summary(samples_ms: Sequence[float]) -> dict:
    if any(not isfinite(sample) or sample < 0 for sample in samples_ms):
        raise ValueError("Latency samples must be finite and non-negative")
    if not samples_ms:
        return {"count": 0, "median_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None}
    ordered = sorted(samples_ms)
    return {
        "count": len(ordered),
        "median_ms": median(ordered),
        "p95_ms": ordered[ceil(0.95 * len(ordered)) - 1],
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
    }


@dataclass(frozen=True)
class MMRSelection:
    candidate: RankedCandidate
    selection_score: float


def mmr_rerank(
    ranked: Sequence[RankedCandidate],
    vectors: Mapping[int, list[float]],
    *,
    top_k: int,
    diversity_lambda: float = 0.85,
) -> list[MMRSelection]:
    """Greedily select from an already ranked, eligible candidate pool."""
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    if not isfinite(diversity_lambda) or not 0 <= diversity_lambda <= 1:
        raise ValueError("diversity_lambda must be within [0, 1]")

    remaining = {
        candidate.steam_app_id: candidate
        for candidate in reversed(ranked)
        if _usable_cosine(vectors.get(candidate.steam_app_id), vectors.get(candidate.steam_app_id))
        is not None
    }
    selected: list[MMRSelection] = []
    max_similarity: dict[int, float] = {}
    while remaining and len(selected) < top_k:
        scored = []
        for appid, candidate in remaining.items():
            score = (
                diversity_lambda * candidate.hybrid_score
                - (1 - diversity_lambda) * max_similarity.get(appid, 0.0)
            )
            scored.append((score, -appid, appid))
        score, _, chosen_id = max(scored)
        selected.append(MMRSelection(remaining.pop(chosen_id), score))
        for appid in remaining:
            similarity = _usable_cosine(vectors[appid], vectors[chosen_id])
            if similarity is not None:
                max_similarity[appid] = (
                    max(max_similarity[appid], similarity)
                    if appid in max_similarity else similarity
                )
    return selected
