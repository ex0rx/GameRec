from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class SyncState(Base): # source and last successful sync timestamp for each source (e.g., Steam)
    __tablename__ = "sync_state"

    source: Mapped[str | None] = mapped_column(
        String(255),
        nullable=False,
        primary_key=True,
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
