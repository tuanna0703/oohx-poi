"""admin_wards: the 2025 two-level wards and communes, with their boundaries

Revision ID: 20261113_0019
Revises: 20261112_0018
Create Date: 2026-11-13

Since 2025-07-01 Vietnam has 34 provinces and 3,321 wards, communes and
special zones, and no districts. ``ward_code`` on processed and master POIs
is the ward's code in that list (the General Statistics Office's, as
provinces.open-api.vn v2 gives it: 20275 is Phường An Hải, Đà Nẵng), found
from the point, not from the address, which still names the old wards.

``admin_wards`` holds one row per ward: its code, names, its 2025
province's code and its boundary. ``admin_ward_parts`` holds the boundary
cut into small pieces (``ST_Subdivide``), so a point-in-polygon lookup
reads a handful of short rings instead of a commune's whole coastline.
``scripts/load_admin_wards.py`` fills both; the boundaries are not in the
repository.

``province_code`` elsewhere (admin_units, POIs) is still the 63-province
code of before 2025; ``admin_wards.province_code`` is the 2025 one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0019"
down_revision: str | Sequence[str] | None = "20261112_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_wards",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("province_code", sa.String(20), nullable=False),
        sa.Column("province_name", sa.String(200), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("ALTER TABLE admin_wards ADD COLUMN geom geometry(MultiPolygon, 4326) NOT NULL")
    op.create_index("ix_admin_wards_province", "admin_wards", ["province_code"])
    op.create_table(
        "admin_ward_parts",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(20), sa.ForeignKey("admin_wards.code", ondelete="CASCADE"), nullable=False),
    )
    op.execute("ALTER TABLE admin_ward_parts ADD COLUMN geom geometry(Polygon, 4326) NOT NULL")
    op.execute("CREATE INDEX ix_admin_ward_parts_geom ON admin_ward_parts USING gist (geom)")
    op.create_index("ix_admin_ward_parts_code", "admin_ward_parts", ["code"])


def downgrade() -> None:
    op.drop_table("admin_ward_parts")
    op.drop_table("admin_wards")
