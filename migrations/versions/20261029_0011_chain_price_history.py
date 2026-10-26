"""chain prices over time: every change per store, and a daily summary per product

Revision ID: 20261029_0011
Revises: 20261028_0010
Create Date: 2026-10-29

``chain_store_products`` holds each store's current price only; each crawl
overwrote the last. ``chain_price_changes`` gets a row whenever a store's
price or sale price for a product differs from what was last seen, or the
product leaves the store's listing (both prices NULL), so a store's price on
any day is its latest row up to that day. ``chain_price_daily`` is one row per
product per day across the chain's stores (lowest, most common and highest
price, how many stores list it and how many have it on sale): what a price
chart reads.

The prices on hand when this runs become each listing's first change, dated
when they were seen.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0011"
down_revision: str | Sequence[str] | None = "20261028_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chain_price_changes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("chain", sa.String(30), nullable=False),
        sa.Column("store_code", sa.String(30), nullable=False),
        sa.Column("sku", sa.String(60), nullable=False),
        sa.Column("price", sa.Numeric(12, 0)),
        sa.Column("sale_price", sa.Numeric(12, 0)),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chain_price_changes_sku", "chain_price_changes", ["chain", "sku", "seen_at"])
    op.create_index(
        "ix_chain_price_changes_store", "chain_price_changes", ["chain", "store_code", "sku", "seen_at"]
    )
    op.create_table(
        "chain_price_daily",
        sa.Column("chain", sa.String(30), primary_key=True),
        sa.Column("sku", sa.String(60), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("stores", sa.Integer, nullable=False),
        sa.Column("on_sale", sa.Integer, nullable=False),
        sa.Column("min_price", sa.Numeric(12, 0)),
        sa.Column("mode_price", sa.Numeric(12, 0)),
        sa.Column("max_price", sa.Numeric(12, 0)),
        sa.Column("regular_price", sa.Numeric(12, 0)),
    )
    op.create_index("ix_chain_price_daily_day", "chain_price_daily", ["chain", "day"])
    op.execute(
        """
        INSERT INTO chain_price_changes (chain, store_code, sku, price, sale_price, seen_at)
        SELECT chain, store_code, sku, price, sale_price, seen_at FROM chain_store_products
        """
    )


def downgrade() -> None:
    op.drop_table("chain_price_daily")
    op.drop_table("chain_price_changes")
