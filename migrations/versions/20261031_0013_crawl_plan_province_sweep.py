"""crawl_plan.sweep may be 'province': a brand searched once per province first

A brand sweep used to search every district of every province — 526 jobs per
chain, most of them empty for a chain that lives in two cities. It now
searches the province as a whole; only a province that returns a full page
of results is split into districts (the row then turns 'district').

Revision ID: 20261031_0013
Revises: 20261030_0012
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261031_0013"
down_revision: str | Sequence[str] | None = "20261030_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_crawl_plan_sweep", "crawl_plan", type_="check")
    op.create_check_constraint(
        "ck_crawl_plan_sweep", "crawl_plan", "sweep IN ('grid', 'district', 'province')"
    )


def downgrade() -> None:
    op.execute("UPDATE crawl_plan SET sweep = 'district' WHERE sweep = 'province'")
    op.drop_constraint("ck_crawl_plan_sweep", "crawl_plan", type_="check")
    op.create_check_constraint(
        "ck_crawl_plan_sweep", "crawl_plan", "sweep IN ('grid', 'district')"
    )
