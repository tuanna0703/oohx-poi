"""OpenStreetMap Overpass API adapter.

Queries Overpass QL for nodes/ways/relations carrying any of the common POI
tags (amenity, shop, tourism, office, leisure) within a circular area.

Reference: https://wiki.openstreetmap.org/wiki/Overpass_API
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from poi_lake.adapters.base import (
    AdapterConfig,
    AdapterError,
    AdapterTransientError,
    RawPOIRecord,
    SourceAdapter,
)
from poi_lake.config import get_settings

logger = logging.getLogger(__name__)


# Tag keys we consider POI-bearing. Overpass returns elements that match any.
_POI_TAG_KEYS: tuple[str, ...] = ("amenity", "shop", "tourism", "office", "leisure")


class OSMOverpassAdapter(SourceAdapter):
    """Wraps the Overpass API."""

    code: ClassVar[str] = "osm_overpass"
    name: ClassVar[str] = "OpenStreetMap (Overpass API)"

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        # base_url is taken from settings (env override) so dev can hit the
        # public instance and prod can point at a self-hosted Overpass.
        self._base_url = config.extra.get("base_url") or get_settings().osm_overpass_url
        self._client = httpx.AsyncClient(
            timeout=config.timeout_seconds,
            headers={"User-Agent": "poi-lake/0.1 (oohx-matrix)"},
        )
        self._min_interval_s = 1.0 / max(config.rate_limit_per_second, 0.001)
        self._next_allowed_at: float = 0.0
        self._rate_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ public

    async def fetch_by_area(
        self,
        lat: float,
        lng: float,
        radius_m: int,
        category: str | None = None,
    ) -> AsyncIterator[RawPOIRecord]:
        query = self._build_area_query(lat, lng, radius_m, category)
        data = await self._post(query)
        for element in data.get("elements", []) or []:
            record = self._element_to_record(element)
            if record is not None:
                yield record

    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        # source_poi_id format: "osm-{type}-{id}", e.g. "osm-node-12345"
        try:
            _, kind, raw_id = source_poi_id.split("-", 2)
            osm_id = int(raw_id)
        except ValueError as exc:
            raise AdapterError(
                f"invalid OSM source_poi_id {source_poi_id!r}; expected 'osm-<type>-<id>'"
            ) from exc

        if kind not in {"node", "way", "relation"}:
            raise AdapterError(f"unknown OSM type {kind!r}")

        query = f"[out:json][timeout:30];{kind}({osm_id});out body center tags;"
        data = await self._post(query)
        elements = data.get("elements") or []
        if not elements:
            return None
        return self._element_to_record(elements[0])

    async def health_check(self) -> bool:
        # Overpass exposes /api/status returning plain text.
        status_url = self._base_url.replace("/interpreter", "/status")
        try:
            resp = await self._client.get(status_url)
        except httpx.HTTPError as exc:
            logger.warning("osm health_check transport failed: %s", exc)
            return False
        return resp.status_code == 200

    # ----------------------------------------------------------------- private

    @staticmethod
    def _build_area_query(
        lat: float, lng: float, radius_m: int, category: str | None
    ) -> str:
        if category:
            # Allow "key=value" or just "value" (assume amenity= prefix).
            if "=" in category:
                key, value = category.split("=", 1)
                tag_filters = [(key.strip(), value.strip())]
            else:
                tag_filters = [(k, category) for k in _POI_TAG_KEYS]
        else:
            tag_filters = [(k, None) for k in _POI_TAG_KEYS]

        parts = []
        for kind in ("node", "way", "relation"):
            for key, val in tag_filters:
                selector = f'["{key}"]' if val is None else f'["{key}"="{val}"]'
                parts.append(f"{kind}{selector}(around:{radius_m},{lat},{lng});")

        # `out body center tags` gives full tag set + a single representative
        # coordinate (centroid for ways/relations, the point itself for nodes).
        return f"[out:json][timeout:60];({''.join(parts)});out body center tags;"

    async def _wait_for_quota(self) -> None:
        async with self._rate_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_allowed_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed_at = max(now, self._next_allowed_at) + self._min_interval_s

    async def _post(self, query: str) -> dict[str, Any]:
        await self._wait_for_quota()
        try:
            resp = await self._client.post(self._base_url, content=query)
        except httpx.HTTPError as exc:
            raise AdapterTransientError(f"osm transport: {exc}") from exc

        # Overpass returns 429 (too many requests) and 504 (gateway timeout)
        # under load — these are transient.
        if resp.status_code in (429, 504) or 500 <= resp.status_code < 600:
            raise AdapterTransientError(
                f"osm overpass {resp.status_code}: {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise AdapterError(f"osm overpass {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    @staticmethod
    def _element_to_record(element: dict[str, Any]) -> RawPOIRecord | None:
        kind = element.get("type")  # node | way | relation
        osm_id = element.get("id")
        if kind not in {"node", "way", "relation"} or osm_id is None:
            return None

        if kind == "node":
            lat = element.get("lat")
            lon = element.get("lon")
        else:
            center = element.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")

        location = (float(lat), float(lon)) if lat is not None and lon is not None else None

        return RawPOIRecord(
            source_poi_id=f"osm-{kind}-{osm_id}",
            raw_payload=element,
            location=location,
        )
