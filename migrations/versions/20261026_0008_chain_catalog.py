"""chain catalog — a chain's own store list and what each store sells

Revision ID: 20261026_0008
Revises: 20261025_0007
Create Date: 2026-10-26

``chain_stores`` is the list of stores the chain sells online from. That is
not the whole chain: winmart.vn lists 1,889 stores you can order from, while
poi-lake has 4,331 WinMart+ masters, most of them real stores that take no
online orders. ``master_id`` links a store to the nearest master of the
chain's brand, so that store's page can show its items.

``chain_store_products`` holds what a store lists online right now: a row
means "in stock at this store", and rows vanish when the store stops listing
the item. ``menu`` is the chain's top-level menu the row was read from, so a
crawl whose menu failed leaves that menu's rows alone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "20261026_0008"
down_revision: str | Sequence[str] | None = "20261025_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    now = sa.func.now()
    op.create_table(
        "chain_stores",
        sa.Column("chain", sa.String(30), primary_key=True),
        sa.Column("store_code", sa.String(30), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("store_type", sa.String(10)),
        sa.Column("group_code", sa.String(20)),
        sa.Column("address", sa.Text),
        sa.Column("region_code", sa.String(20)),
        sa.Column(
            "master_id",
            UUID(as_uuid=True),
            sa.ForeignKey("master_pois.id", ondelete="SET NULL"),
        ),
        sa.Column("match_m", sa.Float),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("raw", JSONB, nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("items_crawled_at", sa.DateTime(timezone=True)),
        sa.Column("items_count", sa.Integer),
    )
    # Geography via raw SQL, as in the initial schema.
    op.execute("ALTER TABLE chain_stores ADD COLUMN location GEOGRAPHY(POINT, 4326) NOT NULL")
    op.execute("CREATE INDEX ix_chain_stores_location ON chain_stores USING GIST(location)")
    op.create_index("ix_chain_stores_master", "chain_stores", ["master_id"])

    op.create_table(
        "chain_products",
        sa.Column("chain", sa.String(30), primary_key=True),
        sa.Column("sku", sa.String(60), primary_key=True),
        sa.Column("item_no", sa.String(40)),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("name_folded", sa.Text, nullable=False),
        sa.Column("brand", sa.String(120)),
        sa.Column("barcode", sa.String(40)),
        sa.Column("uom", sa.String(40)),
        sa.Column("category_path", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("seo_name", sa.Text),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )
    op.create_index("ix_chain_products_barcode", "chain_products", ["barcode"])

    op.create_table(
        "chain_store_products",
        sa.Column("chain", sa.String(30), primary_key=True),
        sa.Column("store_code", sa.String(30), primary_key=True),
        sa.Column("sku", sa.String(60), primary_key=True),
        sa.Column("menu", sa.String(120)),
        sa.Column("price", sa.Numeric(12, 0)),
        sa.Column("sale_price", sa.Numeric(12, 0)),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.ForeignKeyConstraint(
            ["chain", "store_code"],
            ["chain_stores.chain", "chain_stores.store_code"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chain", "sku"],
            ["chain_products.chain", "chain_products.sku"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_chain_store_products_sku", "chain_store_products", ["chain", "sku"])


def downgrade() -> None:
    op.drop_table("chain_store_products")
    op.drop_table("chain_products")
    op.drop_table("chain_stores")
