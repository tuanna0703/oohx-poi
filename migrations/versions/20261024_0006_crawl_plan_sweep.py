"""crawl_plan.sweep — rare categories are swept per district, not per cell

Revision ID: 20261024_0006
Revises: 20261018_0005
Create Date: 2026-10-24

After cell probing, 84% of crawl jobs still returned nothing: the planner had
moved on to shopping malls, which a 5 km cell almost never holds (0.46 raw
POI per cell over 14k cells). Codes that rare are now swept once per district
(``admin_units`` level 2) with the district's name in the query.

``sweep`` is 'grid' (cells, as before) or 'district'. This migration sets
'district' on the rare codes — a frozen copy of
``openooh_keywords.DISTRICT_SWEEP_CODES`` as of this revision — and restarts
their unfinished rows from zero (cells counted districts from now on;
``pois_raw`` is kept). Rows already done by grid are left done.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_0006"
down_revision: str | Sequence[str] | None = "20261018_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISTRICT_CODES = [
    "retail.shopping_malls",
    "entertainment",
    "entertainment.cinema",
    "entertainment.theaters",
    "entertainment.casinos",
    "transit.airports",
    "transit.subway",
    "transit.rail",
    "transit.ferry_terminals",
    "transit.taxi_ride_share",
    "sports_and_fitness.stadiums",
    "sports_and_fitness.golf_courses",
    "leisure.bowling_alleys",
    "office_buildings",
    "office_buildings.office_towers",
    "office_buildings.coworking",
    "outdoor.beaches",
    "outdoor.plazas",
    "residential.condominiums",
    "government.libraries",
    "farm.dairy_farms",
    "industrial",
    "roadside.billboards",
    "travel.resorts",
]


def upgrade() -> None:
    op.add_column(
        "crawl_plan",
        sa.Column("sweep", sa.String(10), nullable=False, server_default="grid"),
    )
    op.create_check_constraint(
        "ck_crawl_plan_sweep", "crawl_plan", "sweep IN ('grid', 'district')"
    )
    op.execute(
        sa.text("UPDATE crawl_plan SET sweep = 'district' WHERE openooh_code = ANY(:codes)")
        .bindparams(codes=_DISTRICT_CODES)
    )
    op.execute(
        """
        UPDATE crawl_plan SET
            cells_total = NULL, cells_enqueued = 0, cells_done = 0,
            cells_failed = 0, cells_skipped = 0, completed_at = NULL,
            status = CASE WHEN status = 'in_progress' THEN 'pending' ELSE status END
        WHERE sweep = 'district' AND status IN ('pending', 'in_progress', 'paused')
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_crawl_plan_sweep", "crawl_plan", type_="check")
    op.drop_column("crawl_plan", "sweep")
