"""Embedding lifecycle against the existing disposable PostgreSQL fixture."""

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gamerec.core.config import settings
from gamerec.ml.game_text import build_game_embedding_text
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.services import game_embeddings as service
from gamerec.services.game_similarity import find_similar_games

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def vector(x=1.0, y=0.0):
    return [x, y] + [0.0] * 382


async def seed(pg_sessions, count=5):
    # Deliberately different PK/app-ID ordering to detect cursor/key confusion.
    games = [
        Game(
            id=(i + 1) * 3,
            steam_app_id=100 - i,
            name=f"Game {i}",
            metadata_available=True,
            short_description=f"Adventure {i}",
        )
        for i in range(count)
    ]
    async with pg_sessions() as db:
        db.add_all(games)
        await db.commit()
    return games


async def saved_rows(pg_sessions):
    async with pg_sessions() as db:
        return (await db.scalars(select(GameEmbedding))).all()


async def save(db, vectors, hashes=None, *, model=None, revision=None):
    return await service.save_game_embeddings(
        db,
        vectors,
        hashes or {appid: "a" * 64 for appid in vectors},
        model or settings.embeddings_model_name,
        revision or settings.embeddings_model_revision,
    )


async def test_generation_ids_hashes_and_incremental_changes(
    pg_sessions, fake_embedding_model
):
    games = await seed(pg_sessions, 2)
    ids = [game.steam_app_id for game in games]
    async with pg_sessions() as db, db.begin():
        vectors, hashes = await service.generate_game_embeddings(
            db, ids, fake_embedding_model
        )
        assert set(vectors) == set(hashes) == set(ids)
        for game in games:
            text = build_game_embedding_text(game)
            assert (
                hashes[game.steam_app_id] == hashlib.sha256(text.encode()).hexdigest()
            )
            assert len(vectors[game.steam_app_id]) == 384
            assert sum(x * x for x in vectors[game.steam_app_id]) == pytest.approx(1)
            assert (
                vectors[game.steam_app_id]
                == fake_embedding_model.encode(
                    [text], normalize_embeddings=True, batch_size=32
                )[0].tolist()
            )
        assert await save(db, vectors, hashes) == 2
    fake_embedding_model.calls.clear()
    async with pg_sessions() as db:
        assert await service.generate_game_embeddings(
            db, ids, fake_embedding_model
        ) == ({}, {})
    assert fake_embedding_model.calls == []
    async with pg_sessions() as db:
        game = await db.get(Game, games[0].id)
        game.short_description = "A changed adventure"
        await db.commit()
    async with pg_sessions() as db:
        changed_vectors, changed_hashes = await service.generate_game_embeddings(
            db, ids, fake_embedding_model
        )
    assert set(changed_vectors) == set(changed_hashes) == {ids[0]}
    assert changed_vectors[ids[0]] != vectors[ids[0]]
    assert changed_hashes[ids[0]] != hashes[ids[0]]
    assert len(fake_embedding_model.calls) == 1


async def test_generation_missing_and_blank_games(pg_sessions, fake_embedding_model):
    async with pg_sessions() as db:
        db.add(
            Game(
                steam_app_id=10,
                name=" \t\n",
                short_description=" ",
                genres=[],
                categories=[],
            )
        )
        await db.commit()
    async with pg_sessions() as db:
        for ids in ([999], [10, 999]):
            assert await service.generate_game_embeddings(
                db, ids, fake_embedding_model
            ) == ({}, {})
    assert fake_embedding_model.calls == []


async def test_pipeline_model_and_revision_changes_regenerate(
    pg_sessions, fake_embedding_model, monkeypatch
):
    await seed(pg_sessions, 1)
    for model, revision in (("fake-a", "v1"), ("fake-a", "v2"), ("fake-b", "v2")):
        monkeypatch.setattr(settings, "embeddings_model_name", model)
        monkeypatch.setattr(settings, "embeddings_model_revision", revision)
        async with pg_sessions() as db:
            assert await service.process_game_embeddings(db, fake_embedding_model) == {
                "processed": 1,
                "generated": 1,
                "skipped": 0,
            }
        async with pg_sessions() as db:
            assert await service.process_game_embeddings(db, fake_embedding_model) == {
                "processed": 1,
                "generated": 0,
                "skipped": 1,
            }
    rows = await saved_rows(pg_sessions)
    assert {(row.model_name, row.model_revision) for row in rows} == {
        ("fake-a", "v1"),
        ("fake-a", "v2"),
        ("fake-b", "v2"),
    }
    assert len({row.input_hash for row in rows}) == 1
    assert len(fake_embedding_model.calls) == 3


async def test_persistence_upsert_and_revision_isolation(pg_sessions):
    await seed(pg_sessions, 1)
    old = datetime(2000, 1, 1, tzinfo=UTC)
    async with pg_sessions() as db:
        async with db.begin():
            assert await save(db, {100: vector()}, revision="v1") == 1
            assert await save(db, {100: vector(0, 1)}, revision="v2") == 1
        row = await db.get(GameEmbedding, (100, settings.embeddings_model_name, "v1"))
        row.created_at = old
        await db.commit()
    async with pg_sessions() as db, db.begin():
        assert await save(db, {100: vector(-1)}, {100: "b" * 64}, revision="v1") == 1
    rows = {row.model_revision: row for row in await saved_rows(pg_sessions)}
    assert set(rows) == {"v1", "v2"}
    assert rows["v1"].embedding == vector(-1)
    assert rows["v1"].input_hash == "b" * 64
    assert rows["v1"].created_at > old
    assert rows["v2"].embedding == vector(0, 1)
    assert rows["v2"].input_hash == "a" * 64


async def test_persistence_failure_rolls_back_prior_write(pg_sessions):
    await seed(pg_sessions, 1)
    async with pg_sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                await save(db, {100: vector()})
                await save(db, {999: vector()})
        assert not db.in_transaction()
    assert await saved_rows(pg_sessions) == []


async def test_batch_eligibility_and_cursor(pg_sessions):
    games = await seed(pg_sessions, 8)
    async with pg_sessions() as db:
        for game, changes in zip(
            games[:5],
            [
                {"metadata_available": None},
                {"metadata_available": False},
                {"short_description": None},
                {"short_description": ""},
                {"short_description": "   "},
            ],
            strict=True,
        ):
            row = await db.get(Game, game.id)
            for key, value in changes.items():
                setattr(row, key, value)
        await db.commit()
    async with pg_sessions() as db:
        first = await service.get_next_embedding_batch(db, batch_size=2)
        assert [g.id for g in first] == [g.id for g in games[5:7]]
        second = await service.get_next_embedding_batch(db, first[-1].id, batch_size=2)
        assert [g.id for g in second] == [games[7].id]
        assert await service.get_next_embedding_batch(db, second[-1].id) == []


async def test_pipeline_limits_repeat_runs_and_statistics(
    pg_sessions, fake_embedding_model
):
    games = await seed(pg_sessions)
    async with pg_sessions() as db:
        assert await service.process_game_embeddings(
            db, fake_embedding_model, batch_size=2, max_games=3
        ) == {"processed": 3, "generated": 3, "skipped": 0}
    assert [len(call) for call in fake_embedding_model.calls] == [2, 1]
    assert {row.steam_app_id for row in await saved_rows(pg_sessions)} == {
        g.steam_app_id for g in games[:3]
    }
    async with pg_sessions() as db:
        assert await service.process_game_embeddings(
            db, fake_embedding_model, batch_size=2, max_games=3
        ) == {"processed": 3, "generated": 0, "skipped": 3}
    assert [len(call) for call in fake_embedding_model.calls] == [2, 1]
    async with pg_sessions() as db:
        assert await service.process_game_embeddings(
            db, fake_embedding_model, batch_size=2, max_games=99
        ) == {"processed": 5, "generated": 2, "skipped": 3}
    assert [len(call) for call in fake_embedding_model.calls] == [2, 1, 1, 1]
    assert len(await saved_rows(pg_sessions)) == 5


async def test_empty_pipeline(pg_sessions, fake_embedding_model):
    async with pg_sessions() as db:
        assert await service.process_game_embeddings(db, fake_embedding_model) == {
            "processed": 0,
            "generated": 0,
            "skipped": 0,
        }
    assert fake_embedding_model.calls == []


@pytest.mark.parametrize("failure", ["inference", "after-save"])
async def test_failed_batch_preserves_previous_batch_and_recovers(
    pg_sessions,
    fake_embedding_model,
    monkeypatch,
    failure,
):
    games = await seed(pg_sessions)
    original_encode = fake_embedding_model.encode
    original_save = service.save_game_embeddings
    calls = 0

    def fail_encode(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected inference failure")
        return original_encode(*args, **kwargs)

    async def fail_save(*args, **kwargs):
        nonlocal calls
        result = await original_save(*args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("injected persistence failure")
        return result

    with monkeypatch.context() as patch:
        if failure == "inference":
            patch.setattr(fake_embedding_model, "encode", fail_encode)
        else:
            patch.setattr(service, "save_game_embeddings", fail_save)
        async with pg_sessions() as db:
            with pytest.raises(RuntimeError, match="injected"):
                await service.process_game_embeddings(
                    db, fake_embedding_model, batch_size=2
                )
            assert not db.in_transaction()
    assert {row.steam_app_id for row in await saved_rows(pg_sessions)} == {
        g.steam_app_id for g in games[:2]
    }
    async with pg_sessions() as db:
        assert await service.process_game_embeddings(
            db, fake_embedding_model, batch_size=2
        ) == {"processed": 5, "generated": 3, "skipped": 2}
    assert len(await saved_rows(pg_sessions)) == 5


async def test_similarity_order_limits_and_isolation(pg_sessions):
    await seed(pg_sessions, 6)
    async with pg_sessions() as db, db.begin():
        await save(
            db, {100: vector(), 99: vector(-1), 98: vector(0, 1), 97: vector(3, 4)}
        )
        await save(db, {100: vector(-1), 96: vector()}, revision="other-revision")
        await save(db, {100: vector(-1), 95: vector()}, model="other-model")
    async with pg_sessions() as db:
        for top_k, ids, scores in (
            (1, [97], [0.6]),
            (2, [97, 98], [0.6, 0]),
            (20, [97, 98, 99], [0.6, 0, -1]),
            (0, [], []),
        ):
            results = await find_similar_games(db, 100, top_k)
            assert [appid for appid, _ in results] == ids
            assert [score for _, score in results] == pytest.approx(scores)
        for missing in (999, 96, 95):
            assert await find_similar_games(db, missing) == []


async def test_similarity_target_without_candidates(pg_sessions):
    await seed(pg_sessions, 1)
    async with pg_sessions() as db:
        async with db.begin():
            await save(db, {100: vector()})
        assert await find_similar_games(db, 100) == []
