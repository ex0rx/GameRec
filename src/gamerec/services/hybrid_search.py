"""Search-specific ranking of a single Qdrant candidate pool."""

from dataclasses import dataclass
from math import isfinite

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.services.hybrid_user_recommendation import (
    calculate_popularity_scores,
    calculate_review_quality,
    filter_eligible_candidates,
    get_candidate_ranking_metadata,
    get_total_max_reviews,
)
from gamerec.services.search_filters import (
    SearchFilters,
    matches_search_filters,
    validate_search_filters,
)
from gamerec.services.semantic_search import (
    MAX_SEARCH_RESULTS,
    search_games_by_embedding,
)
from gamerec.services.vector_store import SimilarGame


@dataclass(frozen=True)
class RankedSearchGame:
    steam_app_id: int
    name: str
    similarity_score: float
    popularity_score: float
    review_quality: float
    hybrid_score: float


@dataclass(frozen=True)
class HybridSearchResult:
    raw_candidates: list[SimilarGame]
    eligible_count: int
    eligible_ids: frozenset[int]
    missing_metadata_ids: frozenset[int]
    ranked: list[RankedSearchGame]
    structured_count: int = 0

    @property
    def retrieved_count(self) -> int:
        return len(self.raw_candidates)

    @property
    def metadata_count(self) -> int:
        return self.retrieved_count - len(self.missing_metadata_ids)

    @property
    def returned_count(self) -> int:
        return len(self.ranked)


async def hybrid_search_games(
    db: AsyncSession,
    client: AsyncQdrantClient,
    query_embedding: list[float],
    *,
    candidate_k: int = 1000,
    top_k: int = 20,
    min_total_reviews: int = 100,
    similarity_weight: float = 0.70,
    popularity_weight: float = 0.15,
    review_quality_weight: float = 0.15,
    filters: SearchFilters | None = None,
) -> HybridSearchResult:
    """Fetch once, score eligible games, and return the top K ranked results."""
    if type(candidate_k) is not int or not 1 <= candidate_k <= MAX_SEARCH_RESULTS:
        raise ValueError(f"candidate_k must be between 1 and {MAX_SEARCH_RESULTS}")
    if type(top_k) is not int or not 1 <= top_k <= MAX_SEARCH_RESULTS:
        raise ValueError(f"top_k must be between 1 and {MAX_SEARCH_RESULTS}")
    if type(min_total_reviews) is not int or min_total_reviews < 0:
        raise ValueError("min_total_reviews must be a non-negative integer")
    weights = (similarity_weight, popularity_weight, review_quality_weight)
    if any(not isfinite(weight) or weight < 0 for weight in weights):
        raise ValueError("Search weights must be finite and non-negative")
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("Search weights must sum to 1")
    if filters is not None:
        validate_search_filters(filters)

    candidates = await search_games_by_embedding(client, query_embedding, candidate_k)
    if not candidates:
        return HybridSearchResult([], 0, frozenset(), frozenset(), [])

    metadata = await get_candidate_ranking_metadata(
        db, [candidate["steam_app_id"] for candidate in candidates]
    )
    missing_metadata_ids = frozenset(
        candidate["steam_app_id"]
        for candidate in candidates
        if candidate["steam_app_id"] not in metadata
    )
    eligible = filter_eligible_candidates(
        candidates, metadata, min_total_reviews=min_total_reviews
    )
    eligible_ids = frozenset(candidate["steam_app_id"] for candidate in eligible)
    if not eligible:
        return HybridSearchResult(candidates, 0, eligible_ids, missing_metadata_ids, [])

    structured = [
        candidate
        for candidate in eligible
        if filters is None
        or matches_search_filters(metadata[candidate["steam_app_id"]], filters)
    ]
    if not structured:
        return HybridSearchResult(
            candidates, len(eligible), eligible_ids, missing_metadata_ids, [], 0
        )

    structured_ids = {candidate["steam_app_id"] for candidate in structured}
    structured_metadata = {app_id: metadata[app_id] for app_id in structured_ids}
    max_reviews = await get_total_max_reviews(db)
    popularity_scores = calculate_popularity_scores(structured_metadata, max_reviews)
    ranked = []
    for candidate in structured:
        app_id = candidate["steam_app_id"]
        item = metadata[app_id]
        popularity = popularity_scores[app_id]
        quality = calculate_review_quality(
            item.total_positive, item.total_negative, item.total_reviews
        )
        similarity = candidate["score"]
        ranked.append(
            RankedSearchGame(
                steam_app_id=app_id,
                name=candidate["name"],
                similarity_score=similarity,
                popularity_score=popularity,
                review_quality=quality,
                hybrid_score=(
                    similarity_weight * similarity
                    + popularity_weight * popularity
                    + review_quality_weight * quality
                ),
            )
        )

    ranked.sort(
        key=lambda game: (-game.hybrid_score, -game.similarity_score, game.steam_app_id)
    )
    return HybridSearchResult(
        candidates,
        len(eligible),
        eligible_ids,
        missing_metadata_ids,
        ranked[:top_k],
        len(structured),
    )
