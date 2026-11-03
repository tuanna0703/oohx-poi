"""brand quality: is each tagged brand a chain, its industry, its twins

Revision ID: 20261103_0016
Revises: 20261102_0015
Create Date: 2026-11-03

One row per tagged brand, written by ``services/brand_quality.run``: keep or
hide (hide = few of its stores carry its name, a detector mistake), the
industry its store names point to with a confidence, and the brand it should
be merged into ("MB Smartbank" → "MB ATM"). Recorded only: tags are not
changed by it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261103_0016"
down_revision: str | Sequence[str] | None = "20261102_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_quality",
        sa.Column("brand", sa.String(100), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("industry", sa.String(24), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("name_share", sa.Float, nullable=False),
        sa.Column("vote", JSONB, nullable=False, server_default="{}"),
        sa.Column("lake_code", sa.String(80)),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("merge_into", sa.String(100)),
        sa.Column("stores", sa.Integer, nullable=False, server_default="0"),
        sa.Column("matching_stores", sa.Integer, nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("verdict IN ('keep', 'hide')", name="ck_brand_quality_verdict"),
        sa.CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_brand_quality_confidence"),
    )


def downgrade() -> None:
    op.drop_table("brand_quality")
