"""On-demand explanations for an existing game and natural-language query."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.db import get_db
from gamerec.integrations.ollama import (
    OllamaResponseError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    create_ollama_client,
)
from gamerec.schemas.explanation import (
    SearchExplanationRequest,
    SearchExplanationResponse,
)
from gamerec.services.explanation_context import get_search_explanation_context
from gamerec.services.search_explanation import generate_search_explanation

router = APIRouter(prefix="/explanations", tags=["explanations"])
logger = logging.getLogger(__name__)


@router.post("/search", response_model=SearchExplanationResponse)
async def explain_search_game(
    request: SearchExplanationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SearchExplanationResponse:
    try:
        context = await get_search_explanation_context(
            db, request.steam_app_id, request.search_query
        )
    except SQLAlchemyError as exc:
        logger.warning("Explanation storage unavailable", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Explanation temporarily unavailable"
        ) from exc

    if context is None:
        raise HTTPException(status_code=404, detail="Game not found")

    try:
        async with create_ollama_client() as client:
            generated = await generate_search_explanation(context, client)
    except OllamaTimeoutError as exc:
        logger.warning("Explanation generation timed out", exc_info=True)
        raise HTTPException(
            status_code=504, detail="Explanation generation timed out"
        ) from exc
    except OllamaUnavailableError as exc:
        logger.warning("Explanation model unavailable", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Explanation temporarily unavailable"
        ) from exc
    except OllamaResponseError as exc:
        logger.warning("Explanation model returned invalid output", exc_info=True)
        raise HTTPException(
            status_code=502, detail="Invalid response from explanation service"
        ) from exc

    return SearchExplanationResponse(
        steam_app_id=context.steam_app_id,
        game_name=context.name,
        **generated.explanation.model_dump(),
    )
