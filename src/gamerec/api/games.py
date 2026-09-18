from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.db import get_db
from gamerec.models.game import Game

router = APIRouter(
    prefix="/games",
    tags=["games"],
)


@router.get("/")
async def get_games(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,        
):
    result = await db.execute(
        select(Game)
        .order_by(Game.id)
        .limit(limit)
        .offset(offset)
        )

    games = result.scalars().all()

    total = await db.scalar(select(func.count()).select_from(Game))

    return {
        "items": games,
        "limit": limit,
        "offset": offset,
        "total": total,
    }