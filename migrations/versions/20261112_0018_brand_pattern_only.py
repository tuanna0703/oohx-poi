"""brands.pattern_only: a brand only its regex tags; Nguyễn Kim becomes one

Revision ID: 20261112_0018
Revises: 20261103_0017
Create Date: 2026-11-12

"Nguyễn Kim" is also a person's name and, in any word order, "Kim Nguyên":
the alias rule tagged 288 pharmacies, gold shops and building-supply stores
with the brand (cleaned up by hand on 2026-11-12, run nkdfe3a1e813). From
now on only its regex, written for its shops' names ("Trung Tâm Mua Sắm
Điện Máy Nguyễn Kim Gò Vấp", "Điện máy Nguyễn Kim Biên Hòa", "Nguyễn Kim Dĩ
An"), tags a store.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0018"
down_revision: str | Sequence[str] | None = "20261103_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NGUYEN_KIM = "Nguyen Kim"
OLD_PATTERN = r"\bnguy[eễ]n\s*kim\b"
# Kept in step with seeds/vn_brands.py.
NEW_PATTERN = (
    r"^\s*(?:(?:trung\s*t[âa]m(?:\s*mua\s*s[ắa]m)?|tttm|ttms(?:\s*&\s*gi[ảa]i\s*tr[íi])?"
    r"|si[êe]u\s*th[ịi]|sis)\s*)?(?:sbs\s*)?(?:đi[ệe]n\s*m[áa]y\s*)?[-–:]?\s*"
    r"(?:nguy[ễe]n\s*kim|nguyenkim)\b"
)


def upgrade() -> None:
    op.add_column(
        "brands", sa.Column("pattern_only", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.execute(
        sa.text("UPDATE brands SET match_pattern = :p, pattern_only = true WHERE name = :n").bindparams(
            p=NEW_PATTERN, n=NGUYEN_KIM
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE brands SET match_pattern = :p WHERE name = :n").bindparams(p=OLD_PATTERN, n=NGUYEN_KIM)
    )
    op.drop_column("brands", "pattern_only")
