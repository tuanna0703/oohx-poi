"""crawl_plan.cells_enqueued — resume cursor for partially-enqueued rows

Revision ID: 20260709_0004
Revises: 20260427_0003
Create Date: 2026-07-09

The planner slices a plan row's grid to the per-tick budget
(``centers[:allowance]``) and then marked the row ``in_progress``, which the
planner's own SELECT excludes. Rows therefore received exactly one slice and
were never revisited: on the production DB all 4,914 rows sat at
``cells_done = 33`` against an average ``cells_total`` of 563 — 5.9% coverage,
frozen since 2026-05-31.

``cells_done`` cannot serve as the resume cursor: it counts *completed* jobs,
so a tick firing while jobs are still in flight would re-enqueue cells that
were already dispatched. This column tracks what has been *enqueued* instead.

Backfill uses ``cells_done + cells_failed`` — for every existing row those are
exactly the cells the single historical slice dispatched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260709_0004"
down_revision: str | Sequence[str] | None = "20260427_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawl_plan",
        sa.Column(
            "cells_enqueued",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    # Every historical row got exactly one slice, so what was dispatched is
    # what has since completed or failed.
    op.execute(
        "UPDATE crawl_plan SET cells_enqueued = cells_done + cells_failed"
    )
    # Planner scans for rows with cells left to dispatch.
    op.create_index(
        "ix_crawl_plan_status_enqueued",
        "crawl_plan",
        ["status", "cells_enqueued"],
    )


def downgrade() -> None:
    op.drop_index("ix_crawl_plan_status_enqueued", table_name="crawl_plan")
    op.drop_column("crawl_plan", "cells_enqueued")
