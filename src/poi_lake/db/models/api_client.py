"""API consumers (rate-limited, permissioned)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from poi_lake.db.base import Base


class APIClient(Base):
    __tablename__ = "api_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1000"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<APIClient {self.name!r} enabled={self.enabled}>"
