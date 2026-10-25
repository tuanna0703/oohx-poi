"""Vietnam retail & services categories under the OpenOOH parents

Revision ID: 20261025_0007
Revises: 20261024_0006
Create Date: 2026-10-25

bizmap.live covers the Vietnamese retail market, which OpenOOH's venue
taxonomy only half describes: no tạp hóa, karaoke, tiệm sửa xe, gara, tiệm
vàng … This adds 25 level-2 codes under existing level-1 parents, so readers
of ``openooh_category`` alone (the Data Engine) see nothing new.

A frozen copy of ``seeds/vn_retail_taxonomy.VN_CATEGORIES`` as of this
revision. Crawl-plan rows for the new codes are created afterwards with
``POST /admin/crawl-plan/initialize`` (it applies priorities and the
province boost); existing data is reclassified by
``scripts/reclassify_categories.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261025_0007"
down_revision: str | Sequence[str] | None = "20261024_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROWS = [
    ("retail.traditional_grocery", "Traditional Grocery (tạp hóa)"),
    ("retail.markets", "Wet Markets (chợ)"),
    ("retail.mother_baby", "Mother & Baby"),
    ("retail.cosmetics", "Cosmetics & Personal Care"),
    ("retail.jewelry", "Gold & Jewelry"),
    ("retail.eyewear", "Eyewear & Opticians"),
    ("retail.bookstores", "Bookstores & Stationery"),
    ("retail.furniture", "Furniture & Home"),
    ("retail.building_materials", "Building Materials & Hardware"),
    ("retail.agri_supplies", "Agricultural Supplies"),
    ("retail.motorbike_dealers", "Motorbike Dealers"),
    ("retail.auto_repair", "Car Repair & Garages"),
    ("retail.motorbike_repair", "Motorbike Repair"),
    ("retail.car_wash", "Car & Motorbike Wash"),
    ("retail.pet_stores", "Pet Shops"),
    ("retail.florists", "Florists"),
    ("hospitality.milk_tea", "Milk Tea & Desserts"),
    ("hospitality.beer_halls", "Beer Halls (quán nhậu, bia hơi)"),
    ("health_and_beauty.nail_salons", "Nail Salons"),
    ("entertainment.karaoke", "Karaoke"),
    ("entertainment.billiards", "Billiards"),
    ("entertainment.gaming", "Internet & Game Centers"),
    ("financial.pawn_shops", "Pawn Shops"),
    ("education.training_centers", "Language & Training Centers"),
    ("sports_and_fitness.sports_courts", "Sports Courts & Pitches"),
]


def upgrade() -> None:
    insert = sa.text(
        """
        INSERT INTO openooh_categories (code, name, parent_code, level)
        VALUES (:code, :name, :parent, 2)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        """
    )
    for code, name in _ROWS:
        op.execute(insert.bindparams(code=code, name=name, parent=code.split(".", 1)[0]))


def downgrade() -> None:
    # Refused by the FK while any crawl_plan row still uses a code — drop
    # those rows first if this really has to go.
    op.execute(
        sa.text("DELETE FROM openooh_categories WHERE code = ANY(:codes)").bindparams(
            sa.bindparam("codes", [c for c, _ in _ROWS], type_=sa.ARRAY(sa.String))
        )
    )
