from datetime import datetime

from pydantic import BaseModel


class UserLibraryGameResponse(BaseModel):
    steam_app_id: int
    name: str
    playtime_forever_minutes: int | None
    playtime_2weeks_minutes: int | None

class UserLibraryResponse(BaseModel):
    steamid64: str
    library_last_synced_at: datetime | None

    total_games: int
    limit: int
    offset: int

    games: list[UserLibraryGameResponse]
