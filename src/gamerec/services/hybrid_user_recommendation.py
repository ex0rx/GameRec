from dataclasses import dataclass
from math import log1p, sqrt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.models.game import Game


@dataclass(frozen=True)
class CandidateRankingMetadata:
    steam_app_id: int
    total_reviews: int | None
    total_positive: int | None
    total_negative: int | None

@dataclass(frozen=True)
class CandidateRankingFeatures:
    steam_app_id: int
    popularity_score: float
    review_quality: float

@dataclass(frozen=True)
class RankedCandidate:
    steam_app_id: int
    name: str | None
    similarity_score: float
    popularity_score: float
    review_quality: float
    hybrid_score: float

async def get_candidate_ranking_metadata(
    db: AsyncSession,
    steam_app_ids: list[int],
) -> dict[int, CandidateRankingMetadata]:
    if not steam_app_ids:
        return {}

    stmt = (
        select(
            Game.steam_app_id,
            Game.total_reviews,
            Game.total_positive,
            Game.total_negative,
        )
        .where(Game.steam_app_id.in_(steam_app_ids))
        .order_by(Game.steam_app_id)
    )

    result = await db.execute(stmt)
    rows = result.fetchall()

    return {
        row.steam_app_id: CandidateRankingMetadata(
            steam_app_id=row.steam_app_id,
            total_reviews=row.total_reviews,
            total_positive=row.total_positive,
            total_negative=row.total_negative,
        )
        for row in rows
    }

def calculate_popularity_scores(
    metadata: dict[int, CandidateRankingMetadata],
    max_total_reviews: int,
) -> dict[int, float]:
    
    if max_total_reviews <= 0:
        return {
            appid: 0.0
            for appid in metadata
        }

    max_log_reviews = log1p(max_total_reviews)

    return {
        appid: (
            log1p(max(0, item.total_reviews or 0))
            / max_log_reviews
        )
        for appid, item in metadata.items()
    }

def calculate_review_quality(
    total_positive: int | None,
    total_negative: int | None,
    total_reviews: int | None,
    z: float = 1.96,
) -> float:
    total_positive = total_positive or 0
    total_negative = total_negative or 0
    total_reviews = total_reviews or 0

    if total_reviews <= 0:
        return 0.0

    total_positive = max(0, min(total_positive, total_reviews))

    positive_ratio = total_positive / total_reviews

    denominator = 1 + (z * z / total_reviews)

    centre = (
        positive_ratio
        + (z * z / (2 * total_reviews))
    )

    margin = z * sqrt(
        (
            positive_ratio * (1 - positive_ratio)
            + z * z / (4 * total_reviews)
        )
        / total_reviews
    )

    return (centre - margin) / denominator

def build_ranking_features(
    metadata: dict[int, CandidateRankingMetadata],
    max_total_reviews: int,
) -> dict[int, CandidateRankingFeatures]:

    popularity_scores = calculate_popularity_scores(
        metadata,
        max_total_reviews,
    )

    return {
        steam_app_id: CandidateRankingFeatures(
            steam_app_id=steam_app_id,
            popularity_score=popularity_scores.get(steam_app_id, 0.0),
            review_quality=calculate_review_quality(
                total_positive=item.total_positive,
                total_negative=item.total_negative,
                total_reviews=item.total_reviews,
            ),
        )
        for steam_app_id, item in metadata.items()
    }

def rank_candidates(
    candidates: list[dict],
    features: dict[int, CandidateRankingFeatures],
    *,
    similarity_weight: float = 0.70,
    popularity_weight: float = 0.15,
    review_quality_weight: float = 0.15,
) -> list[RankedCandidate]:
    ranked = []

    total_weight = (
    similarity_weight
    + popularity_weight
    + review_quality_weight
    )

    if abs(total_weight - 1.0) > 1e-9:
        raise ValueError("Ranking weights must sum to 1.")

    if any(
        weight < 0
        for weight in (
            similarity_weight,
            popularity_weight,
            review_quality_weight,
        )
    ):
        raise ValueError("Ranking weights cannot be negative.")

    for candidate in candidates:
        appid = candidate["steam_app_id"]
        feature = features.get(appid)

        popularity = feature.popularity_score if feature else 0.0
        review_quality = feature.review_quality if feature else 0.0

        hybrid_score = (
            similarity_weight * candidate["score"]
            + popularity_weight * popularity
            + review_quality_weight * review_quality
        )

        ranked.append(
            RankedCandidate(
                steam_app_id=appid,
                name=candidate["name"],
                similarity_score=candidate["score"],
                popularity_score=popularity,
                review_quality=review_quality,
                hybrid_score=hybrid_score,
            )
        )

    return sorted(
        ranked,
        key=lambda candidate: candidate.hybrid_score,
        reverse=True,
    )

async def get_total_max_reviews(
    db: AsyncSession,
) -> int:
    statement = (select(
        func.max(Game.total_reviews))
        .where(Game.total_reviews.is_not(None))
    )
    

    result = await db.execute(statement)

    return result.scalar_one() or 0

def filter_eligible_candidates(
    candidates: list[dict],
    metadata: dict[int, CandidateRankingMetadata],
    *,
    min_total_reviews: int = 100,
) -> list[dict]:
    return [
        candidate
        for candidate in candidates
        if (
            candidate["steam_app_id"] in metadata
            and (metadata[candidate["steam_app_id"]].total_reviews or 0) >= min_total_reviews
        )
    ]