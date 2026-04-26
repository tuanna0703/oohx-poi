"""Silver layer — normalized POIs awaiting dedup/merge."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class MergeStatus(StrEnum):
    PENDING = "pending"
    MERGED = "merged"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class ProcessedPOI(Base):
    __tablename__ = "processed_pois"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_poi_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_pois.id", ondelete="RESTRICT"), nullable=False
    )
    name_original: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    name_embedding: Mapped[Any] = mapped_column(Vector(384), nullable=False)
    address_original: Mapped[str | None] = mapped_column(Text)
    address_normalized: Mapped[str | None] = mapped_column(Text)
    address_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    phone_original: Mapped[str | None] = mapped_column(Text)
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    website: Mapped[str | None] = mapped_column(Text)
    website_domain: Mapped[str | None] = mapped_column(String(255))
    openooh_category: Mapped[str | None] = mapped_column(String(50))
    openooh_subcategory: Mapped[str | None] = mapped_column(String(100))
    raw_category: Mapped[str | None] = mapped_column(String(100))
    brand: Mapped[str | None] = mapped_column(String(100))
    brand_confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    location: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    quality_factors: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    province_code: Mapped[str | None] = mapped_column(String(20))
    district_code: Mapped[str | None] = mapped_column(String(20))
    ward_code: Mapped[str | None] = mapped_column(String(20))
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("master_pois.id", ondelete="SET NULL")
    )
    merge_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="'pending'"
    )
    merge_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
