from fastapi import FastAPI

from gamerec.api.health import router as health_router

app = FastAPI(
    title="GameRec API",
    version="0.1.0",
    description="A recommendation system for games",
)

app.include_router(health_router)