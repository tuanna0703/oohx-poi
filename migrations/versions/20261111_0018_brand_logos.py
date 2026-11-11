"""brand logos, one per brand name, kept as files on the VPS

Revision ID: 20261111_0018
Revises: 20261103_0017
Create Date: 2026-11-11

``brand_logos`` holds one row per brand, keyed by the brand name's slug, so a
store brand (Highlands Coffee on the map) and a product brand of the same
name share one logo. ``file`` is the PNG under ``LOGOS_DIR``, served at
``/logos/<file>``; ``source`` says where it came from (manual, wikidata,
website) and ``source_url`` exactly which image. ``status`` 'missing' records
a search that found nothing, so it is not repeated for ``LOGOS_RETRY_DAYS``.
A ``manual`` logo is never replaced by the crawler.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0018"
down_revision: str | Sequence[str] | None = "20261103_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    now = sa.func.now()
    op.create_table(
        "brand_logos",
        sa.Column("slug", sa.String(120), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("file", sa.Text),
        sa.Column("source", sa.String(20)),
        sa.Column("source_url", sa.Text),
        sa.Column("license", sa.Text),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("sha", sa.String(64)),
        sa.Column("manual", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint("status IN ('ok', 'missing')", name="ck_brand_logos_status"),
    )
    op.create_index("ix_brand_logos_sha", "brand_logos", ["sha"])


def downgrade() -> None:
    op.drop_table("brand_logos")
