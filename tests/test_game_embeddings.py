"""Boundary validation that requires neither PostgreSQL nor real inference."""

import pytest

from gamerec.services.game_embeddings import (
    generate_game_embeddings,
    process_game_embeddings,
    save_game_embeddings,
)

pytestmark = pytest.mark.asyncio


async def test_empty_generation_and_save_do_no_work(fake_db, fake_embedding_model):
    assert await generate_game_embeddings(fake_db, [], fake_embedding_model) == ({}, {})
    assert await save_game_embeddings(fake_db, {}, {}, "fake", "v1") == 0
    fake_db.execute.assert_not_awaited()
    assert fake_embedding_model.calls == []


@pytest.mark.parametrize("dimension", [0, 383, 385])
async def test_save_rejects_invalid_dimensions_before_writes(fake_db, dimension):
    with pytest.raises(ValueError, match="Invalid embedding dimensions for 20"):
        await save_game_embeddings(
            fake_db,
            {10: [1.0] * 384, 20: [1.0] * dimension},
            {10: "a", 20: "b"},
            "fake",
            "v1",
        )
    fake_db.execute.assert_not_awaited()


async def test_save_missing_hash_does_not_write(fake_db):
    with pytest.raises(KeyError):
        await save_game_embeddings(fake_db, {10: [1.0] * 384}, {}, "fake", "v1")
    fake_db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs",
    [{"batch_size": 0}, {"batch_size": -1}, {"max_games": 0}, {"max_games": -1}],
)
async def test_invalid_pipeline_limits_do_no_work(
    fake_db, fake_embedding_model, kwargs
):
    with pytest.raises(ValueError):
        await process_game_embeddings(fake_db, fake_embedding_model, **kwargs)
    fake_db.begin.assert_not_called()
    assert fake_embedding_model.calls == []


@pytest.mark.parametrize("result_count", [0, 2])
async def test_generation_rejects_wrong_number_of_vectors(
    fake_db, fake_embedding_model, monkeypatch, result_count
):
    from array import array
    from types import SimpleNamespace

    from gamerec.models.game import Game

    game = Game(id=1, steam_app_id=100, name="Example")
    fake_db.execute.side_effect = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [game])),
        SimpleNamespace(all=list),
    ]
    monkeypatch.setattr(
        fake_embedding_model,
        "encode",
        lambda *args, **kwargs: [array("d", [1.0] * 384)] * result_count,
    )
    with pytest.raises(ValueError, match="zip"):
        await generate_game_embeddings(fake_db, [100], fake_embedding_model)
