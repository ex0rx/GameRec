"""Async client for the existing embeddings container's internal endpoint."""

from math import isfinite

import httpx
from pydantic import ValidationError

from gamerec.core.config import settings
from gamerec.schemas.search import SearchEmbeddingResponse


class EmbeddingServiceUnavailable(Exception):
    pass


async def get_query_embedding(query: str) -> list[float]:
    try:
        async with httpx.AsyncClient(
            base_url=settings.embedding_service_url, timeout=30.0
        ) as client:
            response = await client.post("/embed/search", json={"query": query})
            response.raise_for_status()
            payload = SearchEmbeddingResponse.model_validate(response.json())
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise EmbeddingServiceUnavailable from exc

    if len(payload.embedding) != settings.embeddings_vector_size or any(
        not isfinite(value) for value in payload.embedding
    ):
        raise EmbeddingServiceUnavailable
    return payload.embedding
