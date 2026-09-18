from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)

    steam_app_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )

    last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    price_change_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    short_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_free: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    release_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

class SyncState(Base): # source and last successful sync timestamp for each source (e.g., Steam)
    __tablename__ = "sync_state"

    source: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        primary_key=True,
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
