"""Read-only recommendations using stored library and preference evidence."""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from qdrant_client.http.exceptions import ApiException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.api.auth import get_current_user
from gamerec.core.auth_security import require_account_owner
from gamerec.db import get_db
from gamerec.models.user import User
from gamerec.schemas.recommendation import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendedGameResponse,
)
from gamerec.services.personalised_recommendation import get_user_recommendations
from gamerec.services.user_game_preferences import UserNotFoundError

router = APIRouter(prefix="/recommendations", tags=["recommendations"])
logger = logging.getLogger(__name__)


@router.post("", response_model=RecommendationResponse)
async def recommend_games(
    request: RecommendationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RecommendationResponse:
    require_account_owner(current_user.steamid64, request.steamid64)
    try:
        ranked = await get_user_recommendations(
            db, current_user.steamid64, limit=request.limit
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    except (SQLAlchemyError, ApiException, httpx.HTTPError, OSError) as exc:
        # Exception text/tracebacks may contain SQL parameters or account IDs.
        logger.warning(
            "Recommendation dependencies unavailable (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=503, detail="Recommendations temporarily unavailable"
        ) from exc

    games = [RecommendedGameResponse.model_validate(game) for game in ranked]
    return RecommendationResponse(total=len(games), limit=request.limit, games=games)
