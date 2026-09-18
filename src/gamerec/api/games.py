from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
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
):
    result = await db.execute(select(Game))

    games = result.scalars().all()

    return games