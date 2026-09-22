from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.db import get_db
from gamerec.schemas.user_library import UserLibraryResponse
from gamerec.services.user_library import get_user_library

router = APIRouter(
    prefix="/users",
    tags=["users"],
)

@router.get(
        "/{steamid64}/library",
        response_model=UserLibraryResponse,
        summary="Get a user's game library",)

async def get_user_library_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    steamid64: str,
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
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

