"""Cosine scores are independent of vector magnitude."""

import pytest

from gamerec.ml.similarity import cosine_similarity


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ([1, 0], [3, 0], 1),
        ([1, 0], [0, 2], 0),
        ([1, 0], [-2, 0], -1),
        ([1, 0], [3, 4], 0.6),
    ],
)
def test_cosine_scores(a, b, expected):
    assert cosine_similarity(a, b) == pytest.approx(expected)
    assert cosine_similarity(b, a) == pytest.approx(expected)


@pytest.mark.parametrize(
    "a,b", [([], []), ([1], [1, 2]), ([0, 0], [1, 0]), ([1, 0], [0, 0])]
)
def test_invalid_vectors(a, b):
    with pytest.raises(ValueError):
        cosine_similarity(a, b)
