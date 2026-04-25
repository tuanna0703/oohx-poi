"""Adapter contract — every source adapter implements this ABC.

Design notes:
  * ``fetch_by_area`` is an async generator so adapters that paginate (Google,
    Vietmap) can stream results to the IngestionService without buffering an
    entire batch in memory.
  * ``fetch_by_id`` is for detail enrichment (e.g. gosom, Google place details)
    and must return a single record or ``None`` when the source has no data.
  * ``health_check`` is a cheap probe used by ``/health/ready`` and by the
    ingestion service before it starts a job. It must not consume quota.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class RawPOIRecord(BaseModel):
    """Single record yielded by an adapter, before normalization."""

    model_config = ConfigDict(frozen=True)

    source_poi_id: str = Field(min_length=1, max_length=255)
    raw_payload: dict[str, Any]
    location: tuple[float, float] | None = None  # (lat, lng)


class AdapterConfig(BaseModel):
    """Per-source runtime config.

    Loaded from ``sources.config`` (JSONB) plus secrets injected from env
    (e.g. ``GOOGLE_PLACES_API_KEY``). Each adapter declares its own subclass
    if it needs typed extras; this base covers the universal fields.
    """

    api_key: str | None = None
    rate_limit_per_second: float = 1.0
    timeout_seconds: int = 30
    extra: dict[str, Any] = Field(default_factory=dict)


class AdapterError(Exception):
    """Base class for adapter failures the IngestionService should record."""


class AdapterTransientError(AdapterError):
    """Recoverable failure — the worker may retry the job."""


class SourceAdapter(ABC):
    """Base class for all POI source adapters."""

    code: ClassVar[str]
    name: ClassVar[str]

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config

    async def __aenter__(self) -> "SourceAdapter":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Release HTTP clients / sessions. Override if needed."""

    @abstractmethod
    def fetch_by_area(
        self,
        lat: float,
        lng: float,
        radius_m: int,
        category: str | None = None,
    ) -> AsyncIterator[RawPOIRecord]:
        """Fetch POIs within a circular area around (lat, lng).

        Implementations are async generators. The IngestionService consumes
        the iterator and applies content-hash dedup before insert.
        """

    @abstractmethod
    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        """Fetch a single POI by its source-native ID. Used for detail enrichment."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Cheap connectivity probe. Must not consume quota / writes."""
