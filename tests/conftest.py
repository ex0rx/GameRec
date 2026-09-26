"""Safe test defaults and reusable unit fixtures (no application DB access)."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# Set before application imports. Integration tests use a separate explicit URL.
os.environ.update(
    DATABASE_URL="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
    STEAM_API_KEY="synthetic-key",
    EMBEDDINGS_MODEL_NAME="synthetic-model",
    EMBEDDINGS_MODEL_REVISION="synthetic-revision",
    HF_HUB_OFFLINE="1",
    TRANSFORMERS_OFFLINE="1",
    STEAMID64_TEST="synthetic-user",
)


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run PostgreSQL tests; requires TEST_POSTGRES_ADMIN_URL",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip = pytest.mark.skip(
            reason="Pass --run-integration for isolated PostgreSQL tests"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail(
            "Live HTTP is forbidden in tests; use MockTransport or ASGITransport"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


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


@pytest.fixture
def fake_embedding_model(monkeypatch):
    """Text-dependent, normalized vectors without loading any model weights."""
    import hashlib
    from array import array
    from math import sqrt

    from sentence_transformers import SentenceTransformer

    def forbidden(*args, **kwargs):
        pytest.fail("Embedding tests must not construct a real model")

    monkeypatch.setattr(SentenceTransformer, "__init__", forbidden)

    class FakeModel:
        def __init__(self):
            self.calls = []

        def encode(self, texts, *, normalize_embeddings, batch_size):
            assert normalize_embeddings is True
            assert batch_size == 32
            self.calls.append(list(texts))
            result = []
            for text in texts:
                values = list(hashlib.sha256(text.encode()).digest()) * 12
                norm = sqrt(sum(value * value for value in values))
                result.append(array("d", (value / norm for value in values)))
            return result

    return FakeModel()


@pytest.fixture
def compatible_collection_info():
    from qdrant_client.models import Distance, VectorParams

    from gamerec.core.config import settings
    from gamerec.services.vector_store import embedding_collection_metadata

    return SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=VectorParams(
                    size=settings.embeddings_vector_size, distance=Distance.COSINE
                )
            ),
            metadata=embedding_collection_metadata(),
        )
    )
