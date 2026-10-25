"""chain stores: phone and opening hours; the chain_websites source

Revision ID: 20261028_0010
Revises: 20261027_0009
Create Date: 2026-10-28

``phone`` and ``open_hours`` are what the chain's own list says about a store
(WinMart gives a phone, Bách Hoá Xanh its hours).

``chain_websites`` is the source a store enters the lake through when the
chain lists it and no master of the chain's brand is near: its raw row is
built from ``chain_stores``, then normalize and dedupe take it like any other.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0010"
down_revision: str | Sequence[str] | None = "20261027_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chain_stores", sa.Column("phone", sa.String(30)))
    op.add_column("chain_stores", sa.Column("open_hours", sa.Text))
    op.execute(
        """
        INSERT INTO sources (code, name, adapter_class, config, enabled, priority)
        VALUES ('chain_websites', 'Chains'' own store lists',
                'poi_lake.adapters.chain_websites:ChainWebsitesAdapter',
                '{"rate_limit_per_second": 100}', true, 25)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM sources WHERE code = 'chain_websites'")
    op.drop_column("chain_stores", "open_hours")
    op.drop_column("chain_stores", "phone")
