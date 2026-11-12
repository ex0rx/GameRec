"""Cache identity, storage policy and application resource ownership."""

from datetime import date
from importlib import import_module
from unittest.mock import Mock

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError

from gamerec.core.config import settings
from gamerec.integrations import redis as redis_integration
from gamerec.integrations.ollama import OLLAMA_GENERATION_OPTIONS
from gamerec.schemas.explanation import SearchExplanation, SearchExplanationContext
from gamerec.services import search_explanation
from gamerec.services.explanation_cache import ExplanationCache

main = import_module("gamerec.main")


@pytest.fixture
def context():
    return SearchExplanationContext(
        steam_app_id=10,
        name="Synthetic game",
        search_query="Co-op crafting",
        description="Build and craft together.",
        genres=["RPG"],
        categories=["Co-op"],
        release_date=date(2020, 1, 1),
    )


@pytest.fixture
def cache(fake_redis):
    return ExplanationCache(fake_redis, 3600, "test-v1")


def test_key_normalizes_whitespace_preserves_case_and_hides_inputs(cache, context):
    other = SearchExplanationContext(
        **{
            **context.model_dump(exclude={"insufficient_descriptive_evidence"}),
            "search_query": "  Co-op \n crafting  ",
        }
    )
    assert cache.key(context) == cache.key(other)
    assert cache.key(context).startswith("gamerec:explanations:test-v1:10:")
    assert len(cache.key(context).rsplit(":", 1)[-1]) == 64
    assert context.search_query not in cache.key(context)
    assert context.description not in cache.key(context)
    assert cache.key(context) != cache.key(
        context.model_copy(update={"search_query": "co-op crafting"})
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("steam_app_id", 20),
        ("name", "New name"),
        ("description", "New facts"),
        ("genres", ["Strategy"]),
        ("categories", ["Single-player"]),
        ("release_date", date(2021, 1, 1)),
        ("search_query", "Different query"),
    ],
)
def test_metadata_and_query_changes_invalidate(cache, context, field, value):
    assert cache.key(context) != cache.key(context.model_copy(update={field: value}))


@pytest.mark.parametrize("setting", ["ollama_model", "ollama_base_url"])
def test_model_and_server_changes_invalidate(cache, context, monkeypatch, setting):
    original = cache.key(context)
    monkeypatch.setattr(settings, setting, "different")
    assert cache.key(context) != original


def test_prompt_schema_options_and_namespace_changes_invalidate(
    cache, context, monkeypatch
):
    original = cache.key(context)
    with monkeypatch.context() as patch:
        patch.setattr(search_explanation, "SYSTEM_PROMPT", "New prompt")
        assert cache.key(context) != original
    with monkeypatch.context() as patch:
        patch.setattr(
            SearchExplanation,
            "model_json_schema",
            classmethod(lambda cls: {"new": "schema"}),
        )
        assert cache.key(context) != original
    with monkeypatch.context() as patch:
        patch.setitem(OLLAMA_GENERATION_OPTIONS, "num_predict", 100)
        assert cache.key(context) != original
    assert ExplanationCache(cache.client, 3600, "test-v2").key(context) != original


@pytest.mark.asyncio
async def test_validated_json_ttl_hit_and_expiry(cache, context, fake_redis):
    key = cache.key(context)
    assert await cache.get(key) == (None, "MISS")
    explanation = SearchExplanation(
        explanation="Craft together.", matching_features=["Co-op"]
    )
    await cache.put(key, explanation)
    fake_redis.set.assert_awaited_once_with(key, explanation.model_dump_json(), ex=3600)
    assert await cache.get(key) == (explanation, "HIT")
    fake_redis.now = 3600
    assert await cache.get(key) == (None, "MISS")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "not JSON",
        "{}",
        '{"explanation":"","matching_features":[]}',
        '{"explanation":"Text","matching_features":[],"diagnostics":1}',
    ],
)
async def test_corrupt_entry_is_a_miss(cache, fake_redis, value):
    fake_redis.values["key"] = (value, 3600)
    assert await cache.get("key") == (None, "MISS")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [ConnectionError("private URL"), TimeoutError("private timeout")]
)
async def test_read_failures_bypass_and_writes_do_not_raise(
    cache, fake_redis, error, caplog
):
    fake_redis.get.side_effect = error
    assert await cache.get("key") == (None, "BYPASS")
    fake_redis.set.side_effect = error
    await cache.put("key", SearchExplanation(explanation="Text", matching_features=[]))
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_bad_encoding_is_a_repairable_miss(cache, fake_redis):
    fake_redis.get.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad data")
    assert await cache.get("key") == (None, "MISS")


@pytest.mark.asyncio
async def test_redis_factory_is_lazy_has_short_timeouts_and_no_retries(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://unused:6379/0")
    monkeypatch.setattr(settings, "redis_timeout_seconds", 0.1)
    client = redis_integration.create_redis_client()
    try:
        assert isinstance(client, Redis)
        connection = client.connection_pool.connection_kwargs
        assert connection["socket_timeout"] == 0.1
        assert connection["socket_connect_timeout"] == 0.1
        assert connection["retry"].get_retries() == 0
        assert not client.connection_pool._in_use_connections
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown_error", [False, True])
async def test_lifespan_never_pings_and_closes_even_on_failure(
    monkeypatch, fake_redis, shutdown_error
):
    monkeypatch.setattr(settings, "explanation_cache_enabled", True)
    factory = Mock(return_value=fake_redis)
    monkeypatch.setattr(main, "create_redis_client", factory)
    if shutdown_error:
        fake_redis.aclose.side_effect = ConnectionError("private close failure")
    with pytest.raises(RuntimeError, match="test exit"):
        async with main.lifespan(main.app):
            assert main.app.state.explanation_cache.client is fake_redis
            fake_redis.get.assert_not_awaited()
            fake_redis.set.assert_not_awaited()
            raise RuntimeError("test exit")
    factory.assert_called_once_with()
    fake_redis.aclose.assert_awaited_once_with()
    assert main.app.state.explanation_cache is None


@pytest.mark.asyncio
async def test_disabled_lifespan_does_not_create_client(monkeypatch):
    monkeypatch.setattr(settings, "explanation_cache_enabled", False)
    factory = Mock(side_effect=AssertionError("Cache is disabled"))
    monkeypatch.setattr(main, "create_redis_client", factory)
    async with main.lifespan(main.app):
        assert main.app.state.explanation_cache is None
    factory.assert_not_called()
