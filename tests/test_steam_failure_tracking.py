import httpx
import pytest
from sqlalchemy import select

from gamerec.db import SessionLocal
from gamerec.models.game import SteamMetadataFailure
from gamerec.services.steam_ingestion import (
    clear_metadata_failure,
    record_metadata_failure,
)


@pytest.mark.asyncio
async def test_record_increment_and_clear_metadata_failure():
    # Use a test app ID that does not have an existing failure record.
    test_app_id = 2_147_483_647

    request = httpx.Request(
        "GET",
        f"https://example.test/appdetails?appids={test_app_id}",
    )

    error = httpx.ReadTimeout(
        "Simulated Steam timeout",
        request=request,
    )

    async with SessionLocal() as db:
        try:
            # Confirm the test starts without an existing failure record.
            existing = await db.get(
                SteamMetadataFailure,
                test_app_id,
            )
            assert existing is None

            # First failed enrichment attempt.
            await record_metadata_failure(
                db=db,
                steam_app_id=test_app_id,
                error=error,
            )

            count = await db.scalar(
                select(SteamMetadataFailure.attempt_count)
                .where(
                    SteamMetadataFailure.steam_app_id == test_app_id
                )
            )

            assert count == 1

            # The same game fails again on a later attempt.
            await record_metadata_failure(
                db=db,
                steam_app_id=test_app_id,
                error=error,
            )

            count = await db.scalar(
                select(SteamMetadataFailure.attempt_count)
                .where(
                    SteamMetadataFailure.steam_app_id == test_app_id
                )
            )

            assert count == 2

            # Simulate a later successful enrichment:
            # the service should clear the previous failure.
            await clear_metadata_failure(
                db=db,
                steam_app_id=test_app_id,
            )

            remaining = await db.scalar(
                select(SteamMetadataFailure.steam_app_id)
                .where(
                    SteamMetadataFailure.steam_app_id == test_app_id
                )
            )

            assert remaining is None

        finally:
            # Undo every database change made by this test.
            await db.rollback()