"""create Steam metadata failures table

Revision ID: 2bca173f1556
Revises: f070d6e856c7
Create Date: 2026-09-21 04:48:05.259597

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2bca173f1556"
down_revision: str | Sequence[str] | None = "f070d6e856c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_state",
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("source"),
    )

    op.create_table(
        "steam_metadata_failures",
        sa.Column("steam_app_id", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("steam_app_id"),
    )


def downgrade() -> None:
    op.drop_table("steam_metadata_failures")
    op.drop_table("sync_state")
