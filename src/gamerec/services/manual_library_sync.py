"""Bounded, development-only sync. Coordination supports one local API worker."""

import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.core.development_access import is_development_test_account
from gamerec.integrations.request_pacer import RequestPacer
from gamerec.integrations.steam import fetch_steam_owned_games, normalise_owned_games
from gamerec.models.game import Game
from gamerec.models.user import User
from gamerec.models.user_game import UserGame
from gamerec.schemas.user_library import LibrarySyncResponse
from gamerec.services.steam_ingestion import get_steam_metadata
from gamerec.services.steam_library import save_steam_library

logger = logging.getLogger(__name__)
CORE_TIMEOUT_SECONDS = 120
METADATA_TIMEOUT_SECONDS = 30


def sync_allowed(steamid64: str) -> bool:
    return settings.manual_library_sync_enabled and is_development_test_account(
        steamid64
    )


class LibrarySyncError(Exception):
    def __init__(
        self, status: int, code: str, message: str, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class LibrarySyncCoordinator:
    def __init__(self, cooldown_seconds: int = 60) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.pending: set[str] = set()
        self.retry_at: dict[str, float] = {}

    def reserve(self, identity: str) -> None:
        # No await between checking and reserving on the single worker's event loop.
        if identity in self.pending:
            raise LibrarySyncError(409, "sync_in_progress", "Library sync is running.")
        self.pending.add(identity)

    def release(self, identity: str) -> None:
        self.pending.discard(identity)

    def begin_attempt(self, identity: str, last_synced: datetime | None) -> None:
        remaining = self.retry_at.get(identity, 0) - time.monotonic()
        if last_synced is not None:
            remaining = max(
                remaining,
                self.cooldown_seconds
                - (datetime.now(UTC) - last_synced).total_seconds(),
            )
        if remaining > 0:
            raise LibrarySyncError(
                429, "sync_cooldown", "Wait before syncing again.", math.ceil(remaining)
            )
        self.extend(identity, self.cooldown_seconds)

    def extend(self, identity: str, seconds: float) -> None:
        self.retry_at[identity] = max(
            self.retry_at.get(identity, 0), time.monotonic() + seconds
        )


def upstream_retry_seconds(value: str | None) -> float:
    if value is None:
        return 0
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except ValueError:
        try:
            return max(
                0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            )
        except (ValueError, TypeError, OverflowError):
            return 0


async def sync_library(
    db: AsyncSession, steamid64: str, coordinator: LibrarySyncCoordinator
) -> LibrarySyncResponse:
    if not sync_allowed(steamid64):
        raise LibrarySyncError(
            403, "sync_forbidden", "Manual library sync is not allowed."
        )
    coordinator.reserve(steamid64)
    try:

        async def observe_response(response: httpx.Response) -> None:
            if response.status_code == 429:
                coordinator.extend(
                    steamid64,
                    upstream_retry_seconds(response.headers.get("Retry-After")),
                )

        pacer = RequestPacer(min_interval=0.5)
        async with httpx.AsyncClient(
            timeout=30,
            event_hooks={"request": [pacer], "response": [observe_response]},
        ) as client:
            async with asyncio.timeout(CORE_TIMEOUT_SECONDS):
                try:
                    last_synced = await db.scalar(
                        select(User.library_last_synced_at).where(
                            User.steamid64 == steamid64
                        )
                    )
                    # save_steam_library owns its transaction; end this read first.
                    await db.rollback()
                except SQLAlchemyError:
                    coordinator.extend(steamid64, coordinator.cooldown_seconds)
                    raise
                coordinator.begin_attempt(steamid64, last_synced)
                payload = await fetch_steam_owned_games(
                    client=client,
                    steamid64=steamid64,
                    include_appinfo=True,
                    include_played_free_games=True,
                )
                try:
                    status, _, games = normalise_owned_games(payload)
                except (ValueError, TypeError, KeyError) as exc:
                    raise LibrarySyncError(
                        502,
                        "steam_upstream_error",
                        "Steam returned unusable library data.",
                    ) from exc
                if status != "available":
                    raise LibrarySyncError(
                        503,
                        "steam_unavailable",
                        "Steam library data is unavailable. Check game-details visibility.",
                    )
                # The existing ownership/app-ID columns are PostgreSQL INTEGER.
                # Reject unusable upstream integers before starting any writes.
                if any(
                    value is not None and value > 2**31 - 1
                    for game in games
                    for value in (
                        game["steam_app_id"],
                        game["playtime_forever_minutes"],
                        game["playtime_2weeks_minutes"],
                    )
                ):
                    raise LibrarySyncError(
                        502,
                        "steam_upstream_error",
                        "Steam returned unusable library data.",
                    )
                try:
                    processed = await save_steam_library(db, steamid64, games)
                except (ValueError, TypeError, KeyError) as exc:
                    raise LibrarySyncError(
                        502,
                        "steam_upstream_error",
                        "Steam returned unusable library data.",
                    ) from exc
                saved_at = await db.scalar(
                    select(User.library_last_synced_at).where(
                        User.steamid64 == steamid64
                    )
                )
                total = await db.scalar(
                    select(func.count())
                    .select_from(UserGame)
                    .where(UserGame.steamid64 == steamid64)
                )
                await db.rollback()
                result = LibrarySyncResponse(
                    steamid64=steamid64,
                    processed_games=processed,
                    total_games=total,
                    library_last_synced_at=saved_at,
                    metadata_enrichment="partial",
                )
            try:
                async with asyncio.timeout(METADATA_TIMEOUT_SECONDS):
                    ids = [game["steam_app_id"] for game in games]
                    await get_steam_metadata(
                        db=db,
                        client=client,
                        batch_size=10,
                        max_games=10,
                        request_delay=0.25,
                        max_concurrent_requests=2,
                        max_consecutive_rate_limits=3,
                        steam_app_ids=ids,
                    )
                    remaining = await db.scalar(
                        select(func.count())
                        .select_from(Game)
                        .where(
                            Game.steam_app_id.in_(ids),
                            Game.metadata_synced_at.is_(None),
                        )
                    )
                    await db.rollback()
                    if remaining == 0:
                        result.metadata_enrichment = "complete"
            except Exception as exc:  # noqa: BLE001 -- post-commit enrichment is best effort
                # Core ownership is committed. Enrichment must not turn that into
                # a failed import, even if its connection cannot be rolled back.
                logger.warning(
                    "Library imported; metadata enrichment incomplete (%s)",
                    type(exc).__name__,
                )
                try:
                    await db.rollback()
                except (SQLAlchemyError, OSError):
                    logger.warning("Metadata connection cleanup failed")
            return result
    finally:
        coordinator.release(steamid64)
