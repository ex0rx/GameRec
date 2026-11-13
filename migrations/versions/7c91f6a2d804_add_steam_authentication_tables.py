"""Add Steam authentication sessions and login transactions.

Revision ID: 7c91f6a2d804
Revises: 2f86da100328
Create Date: 2026-11-13

This revision is handwritten and additive. Existing application tables are not
modified; only the authentication tables created here are removed on downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c91f6a2d804"
down_revision: str | Sequence[str] | None = "2f86da100328"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("steamid64", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["steamid64"], ["users.steamid64"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "steam_login_transactions",
        sa.Column("login_id", sa.String(43), nullable=False),
        sa.Column("browser_token_hash", sa.String(64), nullable=False),
        sa.Column("expected_return_to", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nonce_hash", sa.String(64), nullable=True),
        sa.Column("nonce_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("login_id"),
        sa.UniqueConstraint(
            "browser_token_hash", name="uq_steam_login_browser_token_hash"
        ),
        sa.UniqueConstraint("nonce_hash", name="uq_steam_login_nonce_hash"),
    )
    op.create_index(
        "ix_steam_login_transactions_expires_at",
        "steam_login_transactions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_steam_login_transactions_expires_at", table_name="steam_login_transactions"
    )
    op.drop_table("steam_login_transactions")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
