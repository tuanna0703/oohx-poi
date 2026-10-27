"""Three more Vietnam categories: courier, telecom stores, EV charging

Revision ID: 20261102_0015
Revises: 20261101_0014
Create Date: 2026-11-02

28% of active masters had no subcategory on 2026-10-27, VinaPhone (92% of
its stores), Viettel Store, Ninja Van and VinFast's charging stations among
them: OpenOOH has no code for a telecom shop, a parcel point or a charging
station. A frozen copy of the rows added to ``seeds/vn_retail_taxonomy`` in
this revision; existing data is reclassified by
``scripts/reclassify_categories.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261102_0015"
down_revision: str | Sequence[str] | None = "20261101_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROWS = [
    ("retail.courier", "Courier & Post Offices"),
    ("retail.telecom", "Telecom Stores (viễn thông)"),
    ("transit.ev_charging", "EV Charging & Battery Swap"),
]


def upgrade() -> None:
    insert = sa.text(
        """
        INSERT INTO openooh_categories (code, name, parent_code, level)
        SELECT :code, :name, :parent, 2
        WHERE EXISTS (SELECT 1 FROM openooh_categories WHERE code = :parent)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        """
    )
    for code, name in _ROWS:
        op.execute(insert.bindparams(code=code, name=name, parent=code.split(".", 1)[0]))


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM openooh_categories WHERE code = ANY(:codes)").bindparams(
            sa.bindparam("codes", [c for c, _ in _ROWS], type_=sa.ARRAY(sa.String))
        )
    )
