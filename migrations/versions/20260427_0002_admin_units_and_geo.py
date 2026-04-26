"""admin_units (province/district/ward) + master_pois geo columns

Revision ID: 20260427_0002
Revises: 20260423_0001
Create Date: 2026-04-27

Adds Vietnamese administrative-division reference data and stamps each
processed_poi / master_poi with province / district / ward codes so the
consumer API and admin UI can filter by administrative boundary instead
of raw lat/lng.

Schema highlights:
  * ``admin_units`` is a single self-referencing table covering all three
    levels (province=1, district=2, ward=3). Each row has the official
    code (province "01" = Hà Nội per QCVN 01:2008), the name, the parent
    code, and a bounding box stored as four numeric columns. We use bbox
    instead of polygon so spatial lookup is just an index range scan.
  * ``processed_pois`` / ``master_pois`` get nullable ``province_code``,
    ``district_code``, ``ward_code`` columns + indexes. The normalize
    pipeline fills them via a bbox lookup; the dedupe service propagates.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260427_0002"
down_revision: str | Sequence[str] | None = "20260423_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_units",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "parent_code",
            sa.String(20),
            sa.ForeignKey("admin_units.code", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("level", sa.Integer, nullable=False),  # 1=province, 2=district, 3=ward
        # bbox in WGS84. Stored as separate numerics so plain ``BETWEEN``
        # queries get an index scan without invoking PostGIS.
        sa.Column("lng_min", sa.Numeric(10, 6), nullable=False),
        sa.Column("lat_min", sa.Numeric(10, 6), nullable=False),
        sa.Column("lng_max", sa.Numeric(10, 6), nullable=False),
        sa.Column("lat_max", sa.Numeric(10, 6), nullable=False),
        sa.CheckConstraint("level IN (1, 2, 3)", name="ck_admin_units_level"),
        sa.CheckConstraint("lng_min < lng_max", name="ck_admin_units_lng_range"),
        sa.CheckConstraint("lat_min < lat_max", name="ck_admin_units_lat_range"),
    )
    op.create_index("idx_admin_parent_level", "admin_units", ["parent_code", "level"])
    op.create_index("idx_admin_level", "admin_units", ["level"])
    # Composite index on bbox columns for the lookup-by-point query.
    op.create_index(
        "idx_admin_bbox",
        "admin_units",
        ["level", "lng_min", "lng_max", "lat_min", "lat_max"],
    )

    # processed_pois: stamp at normalize time
    op.add_column("processed_pois", sa.Column("province_code", sa.String(20)))
    op.add_column("processed_pois", sa.Column("district_code", sa.String(20)))
    op.add_column("processed_pois", sa.Column("ward_code", sa.String(20)))
    op.create_index(
        "idx_proc_admin",
        "processed_pois",
        ["province_code", "district_code", "ward_code"],
    )

    # master_pois: propagate from members at merge time
    op.add_column("master_pois", sa.Column("province_code", sa.String(20)))
    op.add_column("master_pois", sa.Column("district_code", sa.String(20)))
    op.add_column("master_pois", sa.Column("ward_code", sa.String(20)))
    op.create_index(
        "idx_master_admin",
        "master_pois",
        ["province_code", "district_code", "ward_code"],
    )


def downgrade() -> None:
    op.drop_index("idx_master_admin", table_name="master_pois")
    op.drop_column("master_pois", "ward_code")
    op.drop_column("master_pois", "district_code")
    op.drop_column("master_pois", "province_code")

    op.drop_index("idx_proc_admin", table_name="processed_pois")
    op.drop_column("processed_pois", "ward_code")
    op.drop_column("processed_pois", "district_code")
    op.drop_column("processed_pois", "province_code")

    op.drop_index("idx_admin_bbox", table_name="admin_units")
    op.drop_index("idx_admin_level", table_name="admin_units")
    op.drop_index("idx_admin_parent_level", table_name="admin_units")
    op.drop_table("admin_units")
