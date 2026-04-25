"""Audit log for master_poi changes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class MasterPOIHistory(Base):
    __tablename__ = "master_poi_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_poi_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("master_pois.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    previous_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(String(100))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
