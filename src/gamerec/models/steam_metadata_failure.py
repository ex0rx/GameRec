from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class SteamMetadataFailure(Base):
    __tablename__ = "steam_metadata_failures"

    steam_app_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )