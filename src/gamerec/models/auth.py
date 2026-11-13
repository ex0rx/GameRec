"""Opaque browser sessions and one-use Steam login transactions."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from gamerec.db import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    steamid64: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.steamid64", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class SteamLoginTransaction(Base):
    __tablename__ = "steam_login_transactions"
    __table_args__ = (
        UniqueConstraint(
            "browser_token_hash", name="uq_steam_login_browser_token_hash"
        ),
        UniqueConstraint("nonce_hash", name="uq_steam_login_nonce_hash"),
    )

    login_id: Mapped[str] = mapped_column(String(43), primary_key=True)
    browser_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_return_to: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nonce_hash: Mapped[str | None] = mapped_column(String(64))
    nonce_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
