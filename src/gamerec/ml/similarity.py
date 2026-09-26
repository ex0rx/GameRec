from math import sqrt


def cosine_similarity(
    a: list[float],
    b: list[float],
) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("Vectors must have matching, nonzero dimensions")

    dot_product = sum(x * y for x, y in zip(a, b, strict=True))

    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        raise ValueError("Cannot compare zero vectors")

    return dot_product / (norm_a * norm_b)
