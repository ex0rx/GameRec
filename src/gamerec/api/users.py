from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.db import get_db
from gamerec.schemas.user_game_preference import (
    UserGamePreferenceRequest,
    UserGamePreferenceResponse,
)
from gamerec.schemas.user_library import UserLibraryResponse
from gamerec.services.user_game_preferences import (
    GameNotFoundError,
    UserNotFoundError,
    delete_user_game_preference,
    get_user_game_preference,
    upsert_user_game_preference,
)
from gamerec.services.user_library import get_user_library

router = APIRouter(
    prefix="/users",
    tags=["users"],
)


@router.get(
    "/{steamid64}/library",
    response_model=UserLibraryResponse,
    summary="Get a user's game library",
)
async def get_user_library_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: str,
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserLibraryResponse:
    user_game_library = await get_user_library(
        db=db,
        steamid64=steamid64,
        limit=limit,
        offset=offset,
    )

    if user_game_library is None:
        raise HTTPException(
            status_code=404,
            detail=f"User with steamid64 {steamid64} not found",
        )

    return user_game_library


@router.put(
    "/{steamid64}/preferences/{steam_app_id}",
    summary="Update a user's game preference",
    response_model=UserGamePreferenceResponse,
)
async def update_user_game_preference(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: str,
    steam_app_id: int,
    preference_request: UserGamePreferenceRequest,
) -> UserGamePreferenceResponse:
    try:
        async with db.begin():
            saved_preference = await upsert_user_game_preference(
                db=db,
                steamid64=steamid64,
                steam_app_id=steam_app_id,
                preference=preference_request.preference.value,
            )

    except UserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"User with steamid64 {steamid64} not found",
        )

    except GameNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Game with steam_app_id {steam_app_id} not found",
        )

    response = UserGamePreferenceResponse.model_validate(saved_preference)

    return response


@router.get(
    "/{steamid64}/preferences",
    summary="Get all game preferences from a user",
    response_model=list[UserGamePreferenceResponse],
)
async def list_user_game_preferences(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: str,
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserGamePreferenceResponse]:
    try:
        async with db.begin():
            user_game_preferences = await get_user_game_preference(
                db=db,
                steamid64=steamid64,
                limit=limit,
                offset=offset,
            )

    except UserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"User with steamid64 {steamid64} not found",
        )

    return user_game_preferences


@router.delete(
    "/{steamid64}/preferences/{steam_app_id}",
    status_code=204,
    summary="Delete a game preference from a user",
)
async def remove_user_game_preference(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: str,
    steam_app_id: int,
) -> None:
    try:
        async with db.begin():
            await delete_user_game_preference(
                db=db,
                steamid64=steamid64,
                steam_app_id=steam_app_id,
            )

    except UserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"User with steamid64 {steamid64} not found",
        )

    except GameNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Game with steam_app_id {steam_app_id} not found",
        )
