from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from gamerec.schemas.user_game_preference import PreferenceValue


class UserLibraryGameResponse(BaseModel):
    steam_app_id: int
    name: str
    playtime_forever_minutes: int | None
    playtime_2weeks_minutes: int | None
    preference: PreferenceValue | None = None


class UserLibraryResponse(BaseModel):
    steamid64: str
    sync_allowed: bool = False
    preference_writes_allowed: bool = False
    library_last_synced_at: datetime | None

    total_games: int
    limit: int
    offset: int

    games: list[UserLibraryGameResponse]


class LibrarySyncResponse(BaseModel):
    steamid64: str
    status: Literal["completed"] = "completed"
    processed_games: int
    total_games: int
    library_last_synced_at: datetime
    metadata_enrichment: Literal["complete", "partial"]
