"""Raw-POI DTOs — bronze layer rows surfaced to the admin UI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class RawPOIOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    source_code: str | None = None         # joined from sources
    source_poi_id: str
    raw_payload: dict[str, Any]
    content_hash: str
    lat: float | None = None
    lng: float | None = None
    fetched_at: datetime
    ingestion_job_id: int | None = None
    processed_at: datetime | None = None


class RawPOIList(BaseModel):
    items: list[RawPOIOut]
    total: int
    page: int
    per_page: int
