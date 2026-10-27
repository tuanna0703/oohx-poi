"""brand discovery: candidates found in the data, settings, AI usage

Revision ID: 20261101_0014
Revises: 20261031_0013
Create Date: 2026-11-01

``brand_candidates`` holds the chains the weekly discovery pass finds among
untagged place names, with the evidence it scored them on and what became of
them: added (by the rules or by an admin), waiting for review, or rejected.
``app_settings`` keeps the knobs an admin sets from bizmap (AI providers with
their keys encrypted, thresholds, the brand lane's share). ``ai_usage`` logs
every AI call so a monthly budget can be enforced.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261101_0014"
down_revision: str | Sequence[str] | None = "20261031_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "brand_candidates",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(120), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50)),
        sa.Column("aliases", JSONB, nullable=False, server_default="[]"),
        sa.Column("search_term", sa.String(40), nullable=False),
        sa.Column("stores", sa.Integer, nullable=False, server_default="0"),
        sa.Column("provinces", sa.Integer, nullable=False, server_default="0"),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("signals", JSONB, nullable=False, server_default="[]"),
        sa.Column("samples", JSONB, nullable=False, server_default="[]"),
        sa.Column("ai", JSONB),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("batch", sa.String(32)),
        sa.Column("tagged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sweep_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('new', 'pending', 'added', 'auto_added', 'rejected', 'auto_rejected', 'removed')",
            name="ck_brand_candidates_status",
        ),
    )
    op.create_index("ix_brand_candidates_status", "brand_candidates", ["status"])
    op.create_index("ix_brand_candidates_batch", "brand_candidates", ["batch"])
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("ok", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_ai_usage_at", "ai_usage", ["at"])


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("brand_candidates")
    op.drop_table("app_settings")
