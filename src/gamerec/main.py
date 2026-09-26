from fastapi import FastAPI

from gamerec.api.games import router as games_router
from gamerec.api.health import router as health_router
from gamerec.api.users import router as users_router

app = FastAPI()

app.include_router(health_router)
app.include_router(games_router)
app.include_router(users_router)
