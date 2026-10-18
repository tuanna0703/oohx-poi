"""Crawl cell — one grid cell of a province, probed once to tell empty from populated."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class CrawlCellStatus(str, Enum):  # noqa: UP042 - StrEnum changes str(member)
    UNKNOWN = "unknown"
    PROBING = "probing"
    POPULATED = "populated"
    EMPTY = "empty"


class CrawlCell(Base):
    __tablename__ = "crawl_cells"
    __table_args__ = (
        UniqueConstraint(
            "province_code", "cell_size_m", "idx", name="uq_crawl_cells_prov_size_idx"
        ),
        CheckConstraint(
            "status IN ('unknown','probing','populated','empty')",
            name="ck_crawl_cells_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    province_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("admin_units.code", ondelete="CASCADE"), nullable=False
    )
    cell_size_m: Mapped[int] = mapped_column(Integer, nullable=False)
    # Position in _grid_centers(province bbox, cell_size_m) — the order the
    # planner walks with crawl_plan.cells_enqueued.
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")
    probe_job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
