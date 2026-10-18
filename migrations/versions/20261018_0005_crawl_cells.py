"""crawl_cells — probe each grid cell once, skip the empty ones

Revision ID: 20261018_0005
Revises: 20260709_0004
Create Date: 2026-10-18

88% of crawl jobs returned nothing (measured 2026-10-18). The grid covers
each province's bounding box, so most cells are sea, mountain, a neighbouring
province or Laos — and every one of them was crawled once per OpenOOH code
(78 codes). A cell is now probed once with broad keywords; if that finds
nothing, every category job for the cell is skipped.

Cells are keyed by (province_code, cell_size_m, idx), idx being the position
in ``_grid_centers(province bbox, cell_size_m)`` — the same order the planner
walks with ``crawl_plan.cells_enqueued``. lat/lng are kept for matching
historical jobs and for maps.

``crawl_plan.cells_skipped`` counts cells passed over as empty. They are also
added to ``cells_done``, so every completion check keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261018_0005"
down_revision: str | Sequence[str] | None = "20260709_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crawl_cells",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "province_code",
            sa.String(20),
            sa.ForeignKey("admin_units.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cell_size_m", sa.Integer, nullable=False),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lng", sa.Numeric(9, 6), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="unknown"),
        sa.Column("probe_job_id", sa.BigInteger, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fetched", sa.Integer, nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "province_code", "cell_size_m", "idx", name="uq_crawl_cells_prov_size_idx"
        ),
        sa.CheckConstraint(
            "status IN ('unknown','probing','populated','empty')",
            name="ck_crawl_cells_status",
        ),
    )
    op.create_index("ix_crawl_cells_status", "crawl_cells", ["status"])
    op.add_column(
        "crawl_plan",
        sa.Column("cells_skipped", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("crawl_plan", "cells_skipped")
    op.drop_index("ix_crawl_cells_status", table_name="crawl_cells")
    op.drop_table("crawl_cells")
