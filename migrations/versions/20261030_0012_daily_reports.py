"""daily reports: what changed in the lake each day

Revision ID: 20261030_0012
Revises: 20261029_0011
Create Date: 2026-10-30

One row per Vietnam calendar day, written at 07:00 by the catalog worker's
scheduler: crawl and dedupe activity over the past 24 hours, active stores
per brand, and each chain catalog's stores, products and price moves. Day
over day changes are worked out when the report is read, from consecutive
rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261030_0012"
down_revision: str | Sequence[str] | None = "20261029_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_reports",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metrics", JSONB, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_reports")
