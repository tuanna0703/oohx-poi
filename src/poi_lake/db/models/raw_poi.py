"""Bronze layer — raw payloads from each source, append-only."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class RawPOI(Base):
    __tablename__ = "raw_pois"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "source_poi_id", "content_hash", name="uq_raw_pois_source_id_hash"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_poi_id: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    location: Mapped[Any | None] = mapped_column(Geography("POINT", srid=4326), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id", ondelete="SET NULL")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<RawPOI id={self.id} source_id={self.source_id} src_id={self.source_poi_id!r}>"
