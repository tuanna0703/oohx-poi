"""crawl_plan — coverage matrix for province × OpenOOH gosom crawl

Revision ID: 20260427_0003
Revises: 20260427_0002
Create Date: 2026-04-27

Tracks one row per (province × openooh_code) combo so the planner worker
can pick pending rows in priority order, throttle to gosom-friendly rates,
and the admin UI can render coverage as a heatmap.

Each plan row spawns N ingest jobs at planner tick time (cells × keywords);
those jobs reference the plan row via ``ingestion_jobs.params.crawl_plan_id``
so the on-completion hook can increment cells_done / cells_failed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260427_0003"
down_revision: str | Sequence[str] | None = "20260427_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crawl_plan",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "province_code",
            sa.String(20),
            sa.ForeignKey("admin_units.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("openooh_code", sa.String(50), nullable=False),
        sa.Column("cell_size_m", sa.Integer, nullable=False, server_default="5000"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("cells_total", sa.Integer, nullable=True),
        sa.Column("cells_done", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cells_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pois_raw", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pois_master", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "province_code", "openooh_code", name="uq_crawl_plan_prov_cat"
        ),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','done','failed','paused')",
            name="ck_crawl_plan_status",
        ),
    )
    op.create_index(
        "idx_crawl_plan_status_priority",
        "crawl_plan",
        ["status", "priority", "last_attempt_at"],
    )
    op.create_index(
        "idx_crawl_plan_completed_at",
        "crawl_plan",
        ["completed_at"],
        postgresql_where=sa.text("status = 'done'"),
    )


def downgrade() -> None:
    op.drop_index("idx_crawl_plan_completed_at", table_name="crawl_plan")
    op.drop_index("idx_crawl_plan_status_priority", table_name="crawl_plan")
    op.drop_table("crawl_plan")
