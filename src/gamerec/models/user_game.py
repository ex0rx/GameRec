from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class UserGame(Base):
    __tablename__ = "user_games"

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

    playtime_forever_minutes: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    playtime_2weeks_minutes: Mapped[int | None] = mapped_column(
        nullable=True,
    )
