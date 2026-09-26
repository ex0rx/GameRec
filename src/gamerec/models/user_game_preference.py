from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class UserGamePreference(Base):
    __tablename__ = "user_game_preferences"

    steamid64: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.steamid64"),
        nullable=False,
        primary_key=True,
    )

    steam_app_id: Mapped[int] = mapped_column(
        ForeignKey("games.steam_app_id"),
        nullable=False,
        primary_key=True,
    )

    preference: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    preference_last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
