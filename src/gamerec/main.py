from fastapi import FastAPI

from gamerec.api.games import router as games_router
from gamerec.api.health import router as health_router

app = FastAPI()

app.include_router(health_router)
app.include_router(games_router)