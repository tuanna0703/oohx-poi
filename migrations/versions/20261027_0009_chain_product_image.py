"""chain_products.image_url — the product image's URL on the chain's CDN

Revision ID: 20261027_0009
Revises: 20261026_0008
Create Date: 2026-10-27

Only the URL: the page loads the image from the chain's own server, so we
never keep a copy of it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0009"
down_revision: str | Sequence[str] | None = "20261026_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chain_products", sa.Column("image_url", sa.Text))


def downgrade() -> None:
    op.drop_column("chain_products", "image_url")
