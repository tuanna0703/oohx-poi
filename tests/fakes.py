"""Test-only fake adapter for IngestionService integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar

from poi_lake.adapters.base import (
    AdapterConfig,
    AdapterTransientError,
    RawPOIRecord,
    SourceAdapter,
)


class FakeAdapter(SourceAdapter):
    """Yields a deterministic list of records for testing.

    Reads the canned dataset from ``config.extra['records']`` (a list of
    dicts with ``source_poi_id``, ``raw_payload``, ``location``).
    """

    code: ClassVar[str] = "fake"
    name: ClassVar[str] = "Fake (test only)"

    async def fetch_by_area(
        self,
        lat: float,
        lng: float,
        radius_m: int,
        category: str | None = None,
    ) -> AsyncIterator[RawPOIRecord]:
        if self.config.extra.get("transient_fail"):
            raise AdapterTransientError("forced transient")
        for raw in self.config.extra.get("records", []):
            loc = raw.get("location")
            yield RawPOIRecord(
                source_poi_id=str(raw["source_poi_id"]),
                raw_payload=raw["raw_payload"],
                location=tuple(loc) if loc else None,
            )

    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        for raw in self.config.extra.get("records", []):
            if str(raw["source_poi_id"]) == source_poi_id:
                loc = raw.get("location")
                return RawPOIRecord(
                    source_poi_id=str(raw["source_poi_id"]),
                    raw_payload=raw["raw_payload"],
                    location=tuple(loc) if loc else None,
                )
        return None

    async def health_check(self) -> bool:
        return True
