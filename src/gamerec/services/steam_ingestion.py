import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.integrations.steam import (
    fetch_steam_app_details,
    fetch_steam_app_reviews,
    fetch_steam_games,
    normalise_game_details,
)
from gamerec.models.game import Game, SteamMetadataFailure, SyncState


async def upsert_steam_games(
    db: AsyncSession,
    steam_games: list[dict],
) -> int:
    games = [
        {
            "steam_app_id": game["appid"],
            "name": game["name"],
            "last_modified": (
                datetime.fromtimestamp(game["last_modified"], tz=UTC)
                if game.get("last_modified") is not None
                else None
            ),
            "price_change_number": game.get("price_change_number"),
        }
        for game in steam_games
        if game.get("appid") and game.get("name")
    ]

    if not games:
        return 0

    statement = insert(Game).values(games)

    statement = statement.on_conflict_do_update(
        index_elements=[Game.steam_app_id],
        set_={
            "name": statement.excluded.name,
            "last_modified": statement.excluded.last_modified,
            "price_change_number": statement.excluded.price_change_number,
        },
    )


    await db.execute(statement)
    await db.commit()

    return len(games)


async def ingest_steam_catalogue(
    db: AsyncSession,
    page_size: int = 1000,
    max_pages: int | None = None,
    if_modified_since: int | None = None,
) -> int:
    last_appid = 0
    total_ingested = 0
    page = 0
    sync_started_at = datetime.now(tz=UTC)

    result = await db.execute(
        select(SyncState).where(SyncState.source == "steam")
    )
    sync_state = result.scalar_one_or_none()

    if if_modified_since is None and sync_state is not None and sync_state.last_synced_at is not None:
            if_modified_since = int(sync_state.last_synced_at.timestamp())

    while True:
        if max_pages is not None and page >= max_pages: # define number of pages to fetch, if max_pages is None, fetch all pages
            break

        steam_games = await fetch_steam_games( # fetch page_size number of games greater than last_appid
            last_appid=last_appid,
            max_results=page_size,
            if_modified_since=if_modified_since,
        )

        if not steam_games:
            break

        count = await upsert_steam_games( # update or insert the fetched games into the database
            db=db,
            steam_games=steam_games,
        )

        total_ingested += count
        page += 1

        new_last_appid = steam_games[-1]["appid"]

        # Safety check so we can never accidentally loop forever
        if new_last_appid <= last_appid:
            raise RuntimeError(
                "Steam pagination did not advance last_appid"
            )

        last_appid = new_last_appid

        print(
            f"Page {page}: ingested {count} games "
            f"(last_appid={last_appid})"
        )

        # Last partial page means we've reached the end
        if len(steam_games) < page_size:
            break

    if max_pages is None:       
        statement = insert(SyncState).values(
            {
                "source": "steam",
                "last_synced_at": sync_started_at,
            }
        ).on_conflict_do_update(
            index_elements=[SyncState.source],
            set_={"last_synced_at": sync_started_at},
        )

        await db.execute(statement)
        await db.commit()

    return total_ingested


async def get_steam_metadata(
    db: AsyncSession,
    client: httpx.AsyncClient,
    batch_size: int = 25,
    max_games: int | None = None,
    request_delay: float = 0.5,
    max_concurrent_requests: int = 2,
) -> int:
    if request_delay < 0:
        raise ValueError("request_delay must be non-negative")
    if max_concurrent_requests < 1:
        raise ValueError("max_concurrent_requests must be at least 1")

    semaphore = asyncio.Semaphore(max_concurrent_requests)
    last_seen_id = 0
    total_attempted = 0

    while True:
        if max_games is not None:
            remaining = max_games - total_attempted

            if remaining <= 0:
                break

            current_batch_size = min(batch_size, remaining)
        else:
            current_batch_size = batch_size

        result = await db.execute(
            select(Game)
            .where(
                Game.metadata_synced_at.is_(None),
                Game.id > last_seen_id,
            )
            .order_by(Game.id)
            .limit(current_batch_size)
        )

        games = result.scalars().all()

        if not games:
            break

        last_seen_id = games[-1].id if games else last_seen_id # move cursor

        results = await asyncio.gather(
            *(
                fetch_one_game(
                    client=client,
                    steam_app_id=game.steam_app_id,
                    semaphore=semaphore,
                    request_delay=request_delay,
                )
                for game in games
            )
        )

        total_attempted += len(games)

        # Update ORM objects sequentially.
        for game, (details, reviews, error) in zip(
            games,
            results,
            strict=True,
        ):
            if error is not None:
                print(
                    f"Failed to fetch metadata for "
                    f"{game.steam_app_id}: {error}"
                )

                await record_metadata_failure(
                    db=db,
                    steam_app_id=game.steam_app_id,
                    error=error,
                )

                # Leave metadata_synced_at as NULL for a future run.
                continue

            if details is None:
                game.metadata_available = False
                game.metadata_synced_at = datetime.now(tz=UTC)

                await clear_metadata_failure(
                    db=db,
                    steam_app_id=game.steam_app_id,
                )

                continue

            normalised_details = normalise_game_details(details)

            for key, value in normalised_details.items():
                setattr(game, key, value)

            if reviews is not None:
                game.review_score = reviews.get("review_score")
                game.review_score_desc = reviews.get("review_score_desc")
                game.total_positive = reviews.get("total_positive")
                game.total_negative = reviews.get("total_negative")
                game.total_reviews = reviews.get("total_reviews")

            game.metadata_available = True
            game.metadata_synced_at = datetime.now(tz=UTC)

            await clear_metadata_failure(
                db=db,
                steam_app_id=game.steam_app_id,
            )

        await db.commit()

    return total_attempted

async def fetch_one_game(
        client: httpx.AsyncClient,
        steam_app_id: int,
        semaphore: asyncio.Semaphore,
        request_delay: float,
       
) -> tuple[dict | None, dict | None, httpx.HTTPError | None]:
    async with semaphore:
        try:
            details = await fetch_steam_app_details(
                client,
                steam_app_id,
            )

            if details is None:
                return None, None, None

            reviews = await fetch_steam_app_reviews(
                client,
                steam_app_id,
            )

            return details, reviews, None

        except httpx.HTTPError as exc:
            return None, None, exc

        finally:
            await asyncio.sleep(request_delay)

async def record_metadata_failure(
    db: AsyncSession,
    steam_app_id: int,
    error: httpx.HTTPError,
) -> None:
    now = datetime.now(tz=UTC)
    error_message = f"{type(error).__name__}: {error}"[:1000]

    statement = (
        insert(SteamMetadataFailure)
        .values(
            steam_app_id=steam_app_id,
            attempt_count=1,
            last_error=error_message,
            last_failed_at=now,
        )
        .on_conflict_do_update(
            index_elements=[SteamMetadataFailure.steam_app_id],
            set_={
                "attempt_count": (
                    SteamMetadataFailure.attempt_count + 1
                ),
                "last_error": error_message,
                "last_failed_at": now,
            },
        )
    )

    await db.execute(statement)

async def clear_metadata_failure(
    db: AsyncSession,
    steam_app_id: int,
) -> None:
    await db.execute(
        delete(SteamMetadataFailure).where(
            SteamMetadataFailure.steam_app_id == steam_app_id
        )
    )