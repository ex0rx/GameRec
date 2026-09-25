from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class GameEmbedding(Base):
    __tablename__ = "game_embeddings"

    steam_app_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("games.steam_app_id"),
        primary_key=True,
    )

    model_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        primary_key=True,
    )

    model_revision: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        primary_key=True,
    )

    input_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    embedding: Mapped[list[float]] = mapped_column(
        ARRAY(Float),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
