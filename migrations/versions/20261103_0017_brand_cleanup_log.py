"""brand clean-up log: what each clean-up run changed, so it can be undone

Revision ID: 20261103_0017
Revises: 20261103_0016
Create Date: 2026-11-03

``services/brand_cleanup.apply`` untags stores that do not carry their
brand's name and drops aliases made of only some of the brand's words. Each
change is a row here with what was there before (``kind`` = brand, processed
or master; ``ref`` = brand name or row id).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261103_0017"
down_revision: str | Sequence[str] | None = "20261103_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_cleanup_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("before", JSONB, nullable=False),
        sa.CheckConstraint("kind IN ('brand', 'processed', 'master')", name="ck_brand_cleanup_log_kind"),
    )
    op.create_index("ix_brand_cleanup_log_run", "brand_cleanup_log", ["run_id", "kind"])


def downgrade() -> None:
    op.drop_table("brand_cleanup_log")
