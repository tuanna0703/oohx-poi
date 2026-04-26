"""Vietnamese administrative-division reference table.

Single self-referencing table covering all three levels. ``code`` follows
the standard VN postal / statistical code (province "01" = Hà Nội) so it's
shareable with other systems.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class AdminUnit(Base):
    __tablename__ = "admin_units"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("admin_units.code", ondelete="RESTRICT")
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    lng_min: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    lat_min: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    lng_max: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    lat_max: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)

    def __repr__(self) -> str:
        return f"<AdminUnit {self.code} {self.name!r} L{self.level}>"

    @property
    def bbox(self) -> list[float]:
        """[lng_min, lat_min, lng_max, lat_max]."""
        return [
            float(self.lng_min), float(self.lat_min),
            float(self.lng_max), float(self.lat_max),
        ]
