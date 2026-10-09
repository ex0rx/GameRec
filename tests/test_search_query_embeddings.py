"""Query embeddings use the game model without real model downloads."""

import hashlib
from array import array
from math import sqrt

import pytest

from gamerec.core.config import settings
from gamerec.ml.embedding_model import embed_search_query, load_embedding_model


class FakeModel:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    def encode(self, query: str, *, normalize_embeddings: bool):
        self.calls.append((query, normalize_embeddings))
        values = list(hashlib.sha256(query.encode()).digest()) * 12
        if normalize_embeddings:
            norm = sqrt(sum(value * value for value in values))
            values = [value / norm for value in values]
        return array("d", values)


@pytest.mark.parametrize("query", ["", "  \t\n  "])
def test_empty_query_rejected_without_encoding(query):
    model = FakeModel()
    with pytest.raises(ValueError, match="must not be empty"):
        embed_search_query(query, model)
    assert model.calls == []


def test_query_is_trimmed_normalized_and_deterministic():
    model = FakeModel()
    query = "Open-world RPG with character progression"
    first = embed_search_query(f"  {query}\n", model)
    second = embed_search_query(query, model)

    assert model.calls == [(query, True), (query, True)]
    assert first == second
    assert isinstance(first, list)
    assert len(first) == 384
    assert len(first) == settings.embeddings_vector_size
    assert all(isinstance(value, float) for value in first)
    assert sqrt(sum(value * value for value in first)) == pytest.approx(1.0)


def test_incompatible_vector_size_rejected():
    class WrongSizeModel:
        def encode(self, query: str, *, normalize_embeddings: bool):
            return [1.0, 0.0]

    with pytest.raises(ValueError, match="Expected 384 dimensions"):
        embed_search_query("RPG", WrongSizeModel())


def test_loader_uses_pinned_game_model_configuration(monkeypatch):
    calls = []

    def fake_sentence_transformer(name, *, revision):
        calls.append((name, revision))
        return FakeModel()

    monkeypatch.setattr(
        "gamerec.ml.embedding_model.SentenceTransformer", fake_sentence_transformer
    )
    model = load_embedding_model()
    embed_search_query("Cooperative survival game", model)
    embed_search_query("Fast-paced shooter", model)

    assert calls == [
        (settings.embeddings_model_name, settings.embeddings_model_revision)
    ]
