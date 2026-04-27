"""Crawl plan — coverage matrix row for (province × OpenOOH code) gosom sweep."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class CrawlPlanStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    PAUSED = "paused"


class CrawlPlan(Base):
    __tablename__ = "crawl_plan"
    __table_args__ = (
        UniqueConstraint("province_code", "openooh_code", name="uq_crawl_plan_prov_cat"),
        CheckConstraint(
            "status IN ('pending','in_progress','done','failed','paused')",
            name="ck_crawl_plan_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    province_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("admin_units.code", ondelete="RESTRICT"),
        nullable=False,
    )
    openooh_code: Mapped[str] = mapped_column(String(50), nullable=False)
    cell_size_m: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    cells_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cells_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cells_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pois_raw: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pois_master: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<CrawlPlan id={self.id} {self.province_code}+{self.openooh_code} "
            f"status={self.status} {self.cells_done}/{self.cells_total}>"
        )
