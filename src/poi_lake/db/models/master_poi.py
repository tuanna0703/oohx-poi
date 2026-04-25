"""Gold layer — curated master POI records.

Heavy fields (canonical_name_embedding, dooh_score_factors) are present in the
schema but only populated by the dedupe pipeline (Phase 4). Phase 2 doesn't
write here — it stops at raw_pois.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class MasterPOIStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    MERGED_AWAY = "merged_away"


class MasterPOI(Base):
    __tablename__ = "master_pois"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name_embedding: Mapped[Any] = mapped_column(Vector(384), nullable=False)
    canonical_address: Mapped[str | None] = mapped_column(Text)
    canonical_address_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    canonical_phone: Mapped[str | None] = mapped_column(String(20))
    canonical_website: Mapped[str | None] = mapped_column(Text)
    location: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)
    openooh_category: Mapped[str | None] = mapped_column(String(50))
    openooh_subcategory: Mapped[str | None] = mapped_column(String(100))
    brand: Mapped[str | None] = mapped_column(String(100))
    source_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    merged_processed_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default="{}"
    )
    sources_count: Mapped[int] = mapped_column(
        Integer, Computed("jsonb_array_length(source_refs)", persisted=True)
    )
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    dooh_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    dooh_score_factors: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="'active'")
    archived_reason: Mapped[str | None] = mapped_column(Text)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("master_pois.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<MasterPOI {self.id} {self.canonical_name!r}>"
