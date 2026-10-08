"""Natural-language search over the configured game collection."""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from qdrant_client.http.exceptions import ApiException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.db import get_db
from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.schemas.search import SearchGameResponse, SearchGamesResponse
from gamerec.services.hybrid_search import hybrid_search_games
from gamerec.services.query_embedding_client import (
    EmbeddingServiceUnavailable,
    get_query_embedding,
)
from gamerec.services.search_filters import SearchFilters

router = APIRouter(prefix="/search", tags=["search"])
logger = logging.getLogger(__name__)


@router.get("/games", response_model=SearchGamesResponse)
async def search_games(
    db: Annotated[AsyncSession, Depends(get_db)],
    query: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    genres: Annotated[list[str] | None, Query()] = None,
    categories: Annotated[list[str] | None, Query()] = None,
    release_year_from: Annotated[int | None, Query(ge=1, le=9999)] = None,
    release_year_to: Annotated[int | None, Query(ge=1, le=9999)] = None,
    min_reviews: Annotated[int | None, Query(ge=0)] = None,
) -> SearchGamesResponse:
    clean_query = query.strip()
    if not clean_query:
        raise HTTPException(status_code=422, detail="query must not be blank")
    try:
        filters = SearchFilters(
            genres=genres,
            categories=categories,
            release_year_from=release_year_from,
            release_year_to=release_year_to,
            min_reviews=min_reviews,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        embedding = await get_query_embedding(clean_query)
    except EmbeddingServiceUnavailable as exc:
        logger.warning("Search embedding service unavailable", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Search temporarily unavailable"
        ) from exc

    try:
        client = get_qdrant_client()
        try:
            result = await hybrid_search_games(
                db, client, embedding, top_k=limit, filters=filters
            )
        finally:
            await client.close()
    except (ApiException, httpx.HTTPError, SQLAlchemyError, OSError, ValueError) as exc:
        logger.warning("Search storage unavailable", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Search temporarily unavailable"
        ) from exc

    games = [SearchGameResponse.model_validate(game) for game in result.ranked]
    return SearchGamesResponse(
        query=clean_query, total=len(games), limit=limit, games=games
    )
