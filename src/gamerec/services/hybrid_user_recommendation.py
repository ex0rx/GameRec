from dataclasses import dataclass
from datetime import date
from math import copysign, isfinite, log, log1p, sqrt

from sqlalchemy import and_, bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.models.game import Game
from gamerec.models.user_game import UserGame
from gamerec.models.user_game_preference import UserGamePreference
from gamerec.services.user_profile import PREFERENCE_WEIGHTS, UNRATED_WEIGHT

RECENCY_HALF_LIFE_YEARS = 10.0
DAYS_PER_YEAR = 365.25
EXCLUDED_AFFINITY_GENRES = frozenset({"Free To Play", "Early Access"})


@dataclass(frozen=True)
class CandidateRankingMetadata:
    steam_app_id: int
    total_reviews: int | None
    total_positive: int | None
    total_negative: int | None
    genres: list[str] | None
    categories: list[str] | None
    release_date: date | None = None


@dataclass(frozen=True)
class CandidateRankingFeatures:
    steam_app_id: int
    popularity_score: float
    review_quality: float
    recency_score: float = 0.0


@dataclass(frozen=True)
class RankedCandidate:
    steam_app_id: int
    name: str | None
    similarity_score: float
    popularity_score: float
    review_quality: float
    hybrid_score: float
    affinity_score: float
    recency_score: float = 0.0


@dataclass(frozen=True)
class CandidateAffinityScores:
    steam_app_id: int
    genres_affinity: float
    categories_affinity: float
    affinity_score: float


@dataclass(frozen=True)
class UserGameAffinityMetadata:
    steam_app_id: int
    playtime_minutes: int | None
    preference: str | None
    genres: list[str] | None
    categories: list[str] | None


@dataclass(frozen=True)
class UserAffinityProfile:
    genres_score: dict[str, float]
    categories_score: dict[str, float]


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
            Game.genres,
            Game.categories,
            Game.release_date,
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
            genres=row.genres,
            categories=row.categories,
            release_date=row.release_date,
        )
        for row in rows
    }


def calculate_popularity_scores(
    metadata: dict[int, CandidateRankingMetadata],
    max_total_reviews: int,
) -> dict[int, float]:

    if max_total_reviews <= 0:
        return {appid: 0.0 for appid in metadata}

    max_log_reviews = log1p(max_total_reviews)

    return {
        appid: (log1p(max(0, item.total_reviews or 0)) / max_log_reviews)
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

    centre = positive_ratio + (z * z / (2 * total_reviews))

    margin = z * sqrt(
        (positive_ratio * (1 - positive_ratio) + z * z / (4 * total_reviews))
        / total_reviews
    )

    return (centre - margin) / denominator


def calculate_release_recency(
    release_date: date | None,
    *,
    as_of: date,
    half_life_years: float = RECENCY_HALF_LIFE_YEARS,
) -> float:
    """Smooth release-age preference; the caller supplies the current UTC date."""
    if not isfinite(half_life_years) or half_life_years <= 0:
        raise ValueError("half_life_years must be finite and positive.")
    if release_date is None:
        return 0.0

    age_years = max(0, (as_of - release_date).days) / DAYS_PER_YEAR
    return 0.5 ** (age_years / half_life_years)


def build_ranking_features(
    metadata: dict[int, CandidateRankingMetadata],
    max_total_reviews: int,
    *,
    as_of: date,
    half_life_years: float = RECENCY_HALF_LIFE_YEARS,
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
            recency_score=calculate_release_recency(
                item.release_date,
                as_of=as_of,
                half_life_years=half_life_years,
            ),
        )
        for steam_app_id, item in metadata.items()
    }


def rank_candidates(
    candidates: list[dict],
    features: dict[int, CandidateRankingFeatures],
    user_affinity_scores: dict[int, CandidateAffinityScores],
    *,
    similarity_weight: float = 0.45,
    popularity_weight: float = 0.30,
    review_quality_weight: float = 0.10,
    affinity_weight: float = 0.10,
    recency_weight: float = 0.05,
) -> list[RankedCandidate]:

    weights = (
        similarity_weight,
        popularity_weight,
        review_quality_weight,
        affinity_weight,
        recency_weight,
    )

    if any(not isfinite(weight) or weight < 0 for weight in weights):
        raise ValueError("Ranking weights must be finite and non-negative.")

    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("Ranking weights must sum to 1.")

    ranked = []

    for candidate in candidates:
        appid = candidate["steam_app_id"]

        feature = features.get(appid)
        affinity = user_affinity_scores.get(appid)

        popularity = feature.popularity_score if feature is not None else 0.0

        review_quality = feature.review_quality if feature is not None else 0.0

        affinity_score = affinity.affinity_score if affinity is not None else 0.0

        recency_score = feature.recency_score if feature is not None else 0.0

        hybrid_score = (
            similarity_weight * candidate["score"]
            + popularity_weight * popularity
            + review_quality_weight * review_quality
            + affinity_weight * affinity_score
            + recency_weight * recency_score
        )

        ranked.append(
            RankedCandidate(
                steam_app_id=appid,
                name=candidate["name"],
                similarity_score=candidate["score"],
                popularity_score=popularity,
                review_quality=review_quality,
                affinity_score=affinity_score,
                hybrid_score=hybrid_score,
                recency_score=recency_score,
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
    statement = select(func.max(Game.total_reviews)).where(
        Game.total_reviews.is_not(None)
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
            and (metadata[candidate["steam_app_id"]].total_reviews or 0)
            >= min_total_reviews
        )
    ]


async def get_user_affinity_metadata(
    db: AsyncSession,
    steamid64: str,
) -> list[UserGameAffinityMetadata]:
    statement = (
        select(
            UserGame.steam_app_id,
            UserGame.playtime_forever_minutes,
            UserGamePreference.preference,
            Game.genres,
            Game.categories,
        )
        .outerjoin(
            UserGamePreference,
            and_(
                UserGamePreference.steamid64 == UserGame.steamid64,
                UserGamePreference.steam_app_id == UserGame.steam_app_id,
            ),
        )
        .join(Game, Game.steam_app_id == UserGame.steam_app_id)
        .where(UserGame.steamid64 == steamid64)
        .order_by(UserGame.steam_app_id)
    )

    result = await db.execute(statement)
    rows = result.fetchall()

    return [
        UserGameAffinityMetadata(
            steam_app_id=row.steam_app_id,
            playtime_minutes=max(0, row.playtime_forever_minutes or 0),
            preference=row.preference,
            genres=row.genres,
            categories=row.categories,
        )
        for row in rows
    ]


async def get_global_genre_statistics(
    db: AsyncSession,
) -> tuple[int, dict[str, int]]:
    """Return corpus size and per-genre game counts in one read-only query.

    Only metadata-available games with usable gameplay genres enter the corpus.
    DISTINCT prevents repeated labels within a game from inflating frequency.
    """
    statement = text("""
        WITH game_genres AS (
            SELECT DISTINCT games.id, genre.value #>> '{}' AS genre
            FROM games
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(games.genres) = 'array'
                     THEN games.genres ELSE '[]'::jsonb END
            ) AS genre(value)
            WHERE games.metadata_available IS TRUE
              AND jsonb_typeof(genre.value) = 'string'
              AND btrim(genre.value #>> '{}') <> ''
              AND (genre.value #>> '{}') NOT IN :excluded_genres
        )
        SELECT genre, count(*) AS game_count,
               (SELECT count(DISTINCT id) FROM game_genres) AS total_games
        FROM game_genres
        GROUP BY genre
    """).bindparams(bindparam("excluded_genres", expanding=True))
    result = await db.execute(
        statement, {"excluded_genres": sorted(EXCLUDED_AFFINITY_GENRES)}
    )
    rows = result.all()
    if not rows:
        return 0, {}
    return rows[0].total_games, {row.genre: row.game_count for row in rows}


def calculate_genre_scores(
    weighted_frequencies: dict[str, float],
    *,
    total_games: int,
    genre_counts: dict[str, int],
) -> dict[str, float]:
    """Signed, saturated TF times smoothed IDF, before profile normalisation.

    With an empty corpus, IDF is 1. Unseen genres use document frequency zero.
    Positive and negative game evidence cancel before saturation.
    """
    if total_games < 0 or any(
        count < 0 or count > total_games for count in genre_counts.values()
    ):
        raise ValueError("Genre counts must be between zero and total_games.")

    return {
        genre: (
            copysign(log1p(abs(frequency)), frequency)
            * (log((total_games + 1) / (genre_counts.get(genre, 0) + 1)) + 1)
        )
        for genre, frequency in weighted_frequencies.items()
    }


def filter_affinity_genres(genres: list[str] | None) -> list[str]:
    """Keep distinct gameplay labels for both user and candidate affinity."""
    return sorted(
        {
            genre
            for genre in genres or []
            if isinstance(genre, str)
            and genre.strip()
            and genre not in EXCLUDED_AFFINITY_GENRES
        }
    )


def build_user_affinity_profile(
    games: list[UserGameAffinityMetadata],
    *,
    total_games: int,
    genre_counts: dict[str, int],
) -> UserAffinityProfile:
    genre_scores: dict[str, float] = {}
    category_scores: dict[str, float] = {}

    for game in games:
        if game.playtime_minutes is None or game.playtime_minutes <= 0:
            continue

        preference_weight = PREFERENCE_WEIGHTS.get(game.preference, UNRATED_WEIGHT)

        playtime_weight = log1p(max(0, game.playtime_minutes))

        weight = preference_weight * playtime_weight

        for genre in filter_affinity_genres(game.genres):
            genre_scores[genre] = genre_scores.get(genre, 0.0) + weight

        if game.categories:
            for category in game.categories:
                category_scores[category] = category_scores.get(category, 0.0) + weight

    genre_scores = calculate_genre_scores(
        genre_scores,
        total_games=total_games,
        genre_counts=genre_counts,
    )

    return UserAffinityProfile(
        genres_score=normalise_scores(genre_scores),
        categories_score=normalise_scores(category_scores),
    )


def normalise_scores(
    scores: dict[str, float],
) -> dict[str, float]:
    if not scores:
        return {}

    max_abs_score = max(abs(score) for score in scores.values())

    if max_abs_score == 0:
        return {key: 0.0 for key in scores}

    return {key: score / max_abs_score for key, score in scores.items()}


def calculate_feature_affinity(
    features: list[str] | None,
    affinity_scores: dict[str, float],
) -> float:
    if not features:
        return 0.0

    matched_scores = [affinity_scores.get(feature, 0.0) for feature in features]

    if not matched_scores:
        return 0.0

    return sum(matched_scores) / len(matched_scores)


def calculate_candidate_affinity(
    metadata: CandidateRankingMetadata,
    user_profile: UserAffinityProfile,
) -> CandidateAffinityScores:
    genre_affinity = calculate_feature_affinity(
        filter_affinity_genres(metadata.genres),
        user_profile.genres_score,
    )

    category_affinity = calculate_feature_affinity(
        metadata.categories,
        user_profile.categories_score,
    )

    affinity_score = 0.7 * genre_affinity + 0.3 * category_affinity

    return CandidateAffinityScores(
        steam_app_id=metadata.steam_app_id,
        genres_affinity=genre_affinity,
        categories_affinity=category_affinity,
        affinity_score=affinity_score,
    )


def build_candidate_affinity_scores(
    metadata: dict[int, CandidateRankingMetadata],
    user_affinity_profile: UserAffinityProfile,
) -> dict[int, CandidateAffinityScores]:
    return {
        appid: calculate_candidate_affinity(
            candidate_metadata,
            user_affinity_profile,
        )
        for appid, candidate_metadata in metadata.items()
    }
