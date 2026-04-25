"""Ingestion-job DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


JobType = Literal["area_sweep", "category_search", "detail_enrich", "backfill"]


class CreateIngestionJob(BaseModel):
    """Payload for ``POST /api/v1/admin/ingestion-jobs``."""

    source_code: str = Field(min_length=1, description="sources.code (e.g. 'google_places')")
    job_type: JobType = "area_sweep"
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_params(self) -> "CreateIngestionJob":
        if self.job_type in ("area_sweep", "category_search"):
            for key in ("lat", "lng", "radius_m"):
                if key not in self.params:
                    raise ValueError(f"params.{key} required for {self.job_type}")
            if self.job_type == "category_search" and "category" not in self.params:
                raise ValueError("params.category required for category_search")
        elif self.job_type == "detail_enrich":
            ids = self.params.get("source_poi_ids")
            if not isinstance(ids, list) or not ids:
                raise ValueError(
                    "params.source_poi_ids must be a non-empty list for detail_enrich"
                )
        return self


class IngestionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    source_code: str | None = None       # joined from Source for convenience
    job_type: str
    params: dict[str, Any]
    status: str
    stats: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime


class IngestionJobsList(BaseModel):
    items: list[IngestionJobOut]
    total: int
