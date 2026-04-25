"""OpenOOH venue taxonomy v1.1."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class OpenOOHCategory(Base):
    __tablename__ = "openooh_categories"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("openooh_categories.code", ondelete="RESTRICT")
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<OpenOOHCategory {self.code!r} level={self.level}>"
