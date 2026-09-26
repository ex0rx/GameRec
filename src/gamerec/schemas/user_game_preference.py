from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class PreferenceValue(str, Enum):
    LIKED = "liked"
    DISLIKED = "disliked"
    NEUTRAL = "neutral"


class UserGamePreferenceResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    steamid64: str
    steam_app_id: int
    preference: PreferenceValue
    preference_last_updated_at: datetime


class UserGamePreferenceRequest(BaseModel):
    preference: PreferenceValue
