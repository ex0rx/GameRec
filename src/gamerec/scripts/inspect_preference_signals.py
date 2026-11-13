"""Read-only diagnostics for the explicitly requested live preference check.

Only public app IDs, scoring signals and aggregate catalogue guards are exported.
The configured account identifier is never included in output.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from math import fsum, hypot, isfinite, log1p
from typing import NamedTuple

from sqlalchemy import and_, func, select

from gamerec.core.config import settings
from gamerec.db import SessionLocal, engine
from gamerec.ml.similarity import cosine_similarity
from gamerec.models import Game, GameEmbedding, User, UserGame, UserGamePreference
from gamerec.services.hybrid_user_recommendation import (
    build_user_affinity_profile,
    get_global_genre_statistics,
    get_user_affinity_metadata,
    rank_candidates,
)
from gamerec.services.personalised_recommendation import (
    MIN_TOTAL_REVIEWS,
    PROFILE_CANDIDATE_K,
    SEED_CANDIDATE_K,
    SEED_GAME_COUNT,
)
from gamerec.services.user_profile import (
    MIN_PLAYED_GAMES,
    MIN_PLAYTIME_MINUTES,
    MIN_PROFILE_NORM,
    PREFERENCE_WEIGHTS,
    UNRATED_WEIGHT,
    apply_explicit_playtime_floor,
    build_user_profile_vector,
    fetch_game_vectors,
)


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class OwnedPreferenceGame(NamedTuple):
    steam_app_id: int
    playtime_forever_minutes: int | None
    preference: str | None
    name: str


def select_unrated_games(games: list[OwnedPreferenceGame]) -> list[int]:
    eligible = sorted(
        (game for game in games if game.preference is None),
        key=lambda game: game.steam_app_id,
    )
    if len(eligible) < 2:
        raise ValueError("Two unrated embedded owned games are required")
    liked = next(
        (game for game in eligible if game.steam_app_id == 582010), eligible[0]
    )
    disliked = max(
        (game for game in eligible if game.steam_app_id != liked.steam_app_id),
        key=lambda game: (game.playtime_forever_minutes or 0, -game.steam_app_id),
    )
    return [liked.steam_app_id, disliked.steam_app_id]


async def inspect(app_ids: list[int] | None = None) -> dict:
    identity = settings.steamid64_test
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    UserGame.steam_app_id,
                    UserGame.playtime_forever_minutes,
                    UserGamePreference.preference,
                    Game.name,
                )
                .join(Game, Game.steam_app_id == UserGame.steam_app_id)
                .outerjoin(
                    UserGamePreference,
                    and_(
                        UserGamePreference.steamid64 == UserGame.steamid64,
                        UserGamePreference.steam_app_id == UserGame.steam_app_id,
                    ),
                )
                .where(UserGame.steamid64 == identity)
                .order_by(UserGame.steam_app_id)
            )
        ).all()
        vectors = await fetch_game_vectors(db, [row.steam_app_id for row in rows]) or {}
        usable = [
            row
            for row in rows
            if (vector := vectors.get(row.steam_app_id)) is not None
            and len(vector) == settings.embeddings_vector_size
            and all(isfinite(value) for value in vector)
            and MIN_PROFILE_NORM < hypot(*vector) < float("inf")
        ]
        if app_ids is None:
            app_ids = select_unrated_games(
                [OwnedPreferenceGame(*row) for row in usable]
            )
        selected = [
            next(row for row in usable if row.steam_app_id == appid)
            for appid in app_ids
        ]
        profile_result = await build_user_profile_vector(db, identity)
        if profile_result is None:
            raise ValueError("A usable existing recommendation profile is required")
        vector, _ = profile_result
        sufficient = (
            sum(
                (row.playtime_forever_minutes or 0) >= MIN_PLAYTIME_MINUTES
                for row in usable
            )
            >= MIN_PLAYED_GAMES
        )
        has_preferences = any(row.preference in PREFERENCE_WEIGHTS for row in usable)
        transformed = {
            row.steam_app_id: apply_explicit_playtime_floor(
                log1p(max(0, row.playtime_forever_minutes or 0)), row.preference
            )
            for row in usable
        }
        total = fsum(transformed.values())
        user_metadata = await get_user_affinity_metadata(db, identity)
        total_games, genre_counts = await get_global_genre_statistics(db)
        affinity = build_user_affinity_profile(
            user_metadata, total_games=total_games, genre_counts=genre_counts
        )
        metadata = {game.steam_app_id: game for game in user_metadata}
        selected_signals = []
        ordered_ids = [row.steam_app_id for row in rows]
        for row in selected:
            preference_multiplier = (
                PREFERENCE_WEIGHTS.get(row.preference, UNRATED_WEIGHT)
                if sufficient or has_preferences
                else 1.0
            )
            multiplier = transformed[row.steam_app_id] if sufficient else 1.0
            game_metadata = metadata[row.steam_app_id]
            selected_signals.append(
                {
                    "steam_app_id": row.steam_app_id,
                    "name": row.name,
                    "page": ordered_ids.index(row.steam_app_id) // 50 + 1,
                    "playtime_minutes": row.playtime_forever_minutes,
                    "preference": row.preference,
                    "preference_multiplier": preference_multiplier,
                    "playtime_multiplier": multiplier,
                    "profile_coefficient": preference_multiplier
                    * multiplier
                    / (total if sufficient else 1),
                    "profile_similarity": cosine_similarity(
                        vector, vectors[row.steam_app_id]
                    ),
                    "genre_affinities": {
                        genre: affinity.genres_score.get(genre, 0.0)
                        for genre in game_metadata.genres or []
                    },
                    "category_affinities": {
                        category: affinity.categories_score.get(category, 0.0)
                        for category in game_metadata.categories or []
                    },
                }
            )
        catalogue = (
            await db.execute(select(func.count(), func.max(Game.updated_at)))
        ).one()
        embedding_count = await db.scalar(
            select(func.count())
            .select_from(GameEmbedding)
            .where(
                GameEmbedding.model_name == settings.embeddings_model_name,
                GameEmbedding.model_revision == settings.embeddings_model_revision,
            )
        )
        synced_at = await db.scalar(
            select(User.library_last_synced_at).where(User.steamid64 == identity)
        )
        return {
            "selected_games": selected_signals,
            "authentication_required": True,
            "owned_games": len(rows),
            "embedded_owned_games": len(usable),
            "library_last_synced_at": synced_at.isoformat() if synced_at else None,
            "profile_sha256": fingerprint(vector),
            "profile_norm": hypot(*vector),
            "sufficient_playtime": sufficient,
            "affinity_profile_sha256": fingerprint(
                {
                    "genres": affinity.genres_score,
                    "categories": affinity.categories_score,
                }
            ),
            "catalogue_guard": {
                "game_count": catalogue[0],
                "latest_game_update": catalogue[1].isoformat()
                if catalogue[1]
                else None,
                "embedding_count": embedding_count,
                "model": settings.embeddings_model_name,
                "revision": settings.embeddings_model_revision,
                "as_of_utc": datetime.now(UTC).date().isoformat(),
            },
            "ranking_configuration": {
                "limit": 20,
                "profile_candidates": PROFILE_CANDIDATE_K,
                "seed_games": SEED_GAME_COUNT,
                "candidates_per_seed": SEED_CANDIDATE_K,
                "minimum_reviews": MIN_TOTAL_REVIEWS,
                "weights": {
                    name.removesuffix("_weight"): value
                    for name, value in rank_candidates.__kwdefaults__.items()
                    if name.endswith("_weight")
                },
                "mmr": False,
            },
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-ids", help="Two public Steam app IDs, comma-separated")
    args = parser.parse_args()
    try:
        app_ids = (
            [int(value) for value in args.app_ids.split(",")] if args.app_ids else None
        )
        print(json.dumps(await inspect(app_ids)))
    except Exception as exc:  # noqa: BLE001 -- never print private exception details
        print(json.dumps({"diagnostic_error": type(exc).__name__}))
        raise SystemExit(1) from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
