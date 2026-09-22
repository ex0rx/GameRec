"""Unit checks for failure-tracking SQL; no database is contacted."""

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from gamerec.services.steam_ingestion import (
    clear_metadata_failure,
    record_metadata_failure,
)


@pytest.mark.asyncio
async def test_record_builds_increment_upsert_and_clear_builds_delete(fake_db):
    error = httpx.ReadTimeout("Simulated Steam timeout")
    await record_metadata_failure(fake_db, 10, error)
    statement = fake_db.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "ON CONFLICT (steam_app_id) DO UPDATE" in str(compiled)
    assert "attempt_count = (steam_metadata_failures.attempt_count +" in str(compiled)
    assert compiled.params["steam_app_id"] == 10
    assert compiled.params["attempt_count"] == 1
    assert compiled.params["attempt_count_1"] == 1
    assert compiled.params["last_error"] == "ReadTimeout: Simulated Steam timeout"
    assert compiled.params["last_failed_at"] is not None

    await clear_metadata_failure(fake_db, 10)
    statement = fake_db.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "DELETE FROM steam_metadata_failures" in sql
    assert "WHERE steam_metadata_failures.steam_app_id = 10" in sql
    assert fake_db.execute.await_count == 2
    fake_db.commit.assert_not_awaited()
