"""Master POI DTOs — what consumers (AdTRUE, TapON, oohx) see."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MasterPOIOut(BaseModel):
    """Read shape for ``GET /api/v1/master-pois`` and friends."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    canonical_name: str
    canonical_address: str | None = None
    canonical_address_components: dict[str, Any] | None = None
    canonical_phone: str | None = None
    canonical_website: str | None = None

    # Geography column → (lat, lng) read by SQL ST_Y/ST_X.
    lat: float
    lng: float

    openooh_category: str | None = None
    openooh_subcategory: str | None = None
    brand: str | None = None

    sources_count: int
    confidence: float
    quality_score: float | None = None
    dooh_score: float | None = None

    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class MasterPOIList(BaseModel):
    items: list[MasterPOIOut]
    total: int
    page: int
    per_page: int


class SourceRefOut(BaseModel):
    """One row in ``master_pois.source_refs``."""

    source: str
    source_poi_id: str
    raw_poi_id: int


class HistoryEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    changed_fields: list[str]
    previous_values: dict[str, Any]
    new_values: dict[str, Any]
    change_reason: str | None
    changed_at: datetime


class SearchRequest(BaseModel):
    """Body for ``POST /api/v1/master-pois/search``."""

    query: str | None = Field(default=None, max_length=500, description="fuzzy text query")
    bbox: list[float] | None = Field(
        default=None,
        description="[lng_min, lat_min, lng_max, lat_max]",
        min_length=4, max_length=4,
    )
    category: str | None = None
    brand: str | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)
