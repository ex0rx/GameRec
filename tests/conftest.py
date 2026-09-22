"""Fake database sessions for unit tests; no PostgreSQL configuration needed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def fake_db():
    db = Mock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=list))
    db.commit = AsyncMock()
    transaction = AsyncMock()
    transaction.__aexit__.return_value = False
    db.begin = Mock(return_value=transaction)
    return db
