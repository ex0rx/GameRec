"""Index setup contract; local Qdrant does not implement payload indexes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PayloadIndexInfo, PayloadSchemaType

from gamerec.core.config import settings
from gamerec.services.vector_store import ensure_game_payload_indexes

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "existing_fields", [[], ["genres"], ["categories"], ["genres", "categories"]]
)
async def test_index_setup_is_repeat_safe(existing_fields, monkeypatch):
    monkeypatch.setattr(settings, "qdrant_game_collection", "isolated-indexes")
    schema = {
        field: PayloadIndexInfo(data_type=PayloadSchemaType.KEYWORD, points=0)
        for field in existing_fields
    }
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.side_effect = lambda _: SimpleNamespace(
        payload_schema=dict(schema)
    )

    async def create(**kwargs):
        schema[kwargs["field_name"]] = PayloadIndexInfo(
            data_type=kwargs["field_schema"], points=0
        )

    client.create_payload_index.side_effect = create
    await ensure_game_payload_indexes(client)
    await ensure_game_payload_indexes(client)
    assert set(schema) == {"genres", "categories"}
    assert client.create_payload_index.await_args_list == [
        call(
            collection_name="isolated-indexes",
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
            wait=True,
        )
        for field in ("genres", "categories")
        if field not in existing_fields
    ]
    client.close.assert_not_awaited()


async def test_incompatible_index_is_reported_without_mutation():
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.return_value = SimpleNamespace(
        payload_schema={
            "categories": PayloadIndexInfo(
                data_type=PayloadSchemaType.INTEGER, points=0
            ),
        }
    )
    with pytest.raises(ValueError, match="categories.*keyword"):
        await ensure_game_payload_indexes(client)
    client.create_payload_index.assert_not_awaited()
    client.delete_payload_index.assert_not_awaited()


async def test_partial_setup_failure_can_be_retried():
    schema = {}
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.side_effect = lambda _: SimpleNamespace(
        payload_schema=dict(schema)
    )
    fail = True

    async def create(**kwargs):
        if fail and kwargs["field_name"] == "categories":
            raise RuntimeError("unavailable")
        schema[kwargs["field_name"]] = PayloadIndexInfo(
            data_type=PayloadSchemaType.KEYWORD, points=0
        )

    client.create_payload_index.side_effect = create
    with pytest.raises(RuntimeError, match="unavailable"):
        await ensure_game_payload_indexes(client)
    assert set(schema) == {"genres"}
    fail = False
    await ensure_game_payload_indexes(client)
    assert set(schema) == {"genres", "categories"}
    assert [
        c.kwargs["field_name"] for c in client.create_payload_index.await_args_list
    ] == ["genres", "categories", "categories"]
