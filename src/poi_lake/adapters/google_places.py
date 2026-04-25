"""Google Places API (New) v1 adapter.

Uses ``places:searchNearby`` for area sweeps and ``places/{id}`` for detail
enrichment. The field mask is configurable via ``sources.config.field_mask``
to keep cost in check (Places API v1 charges per field tier).

Reference: https://developers.google.com/maps/documentation/places/web-service/nearby-search
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

logger = logging.getLogger(__name__)


_DEFAULT_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.types,places.primaryType,places.nationalPhoneNumber,"
    "places.internationalPhoneNumber,places.websiteUri,places.rating,"
    "places.userRatingCount"
)
_DETAIL_FIELD_MASK = _DEFAULT_FIELD_MASK.replace("places.", "")  # detail endpoint is unprefixed


class GooglePlacesAdapter(SourceAdapter):
    """Wraps the Places API (New) v1 REST endpoints."""

    code: ClassVar[str] = "google_places"
    name: ClassVar[str] = "Google Places API (New)"
    BASE_URL: ClassVar[str] = "https://places.googleapis.com/v1"
    NEARBY_MAX_RESULTS: ClassVar[int] = 20  # API hard limit

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        if not config.api_key:
            raise AdapterError("GooglePlacesAdapter requires GOOGLE_PLACES_API_KEY")

        self._field_mask: str = str(config.extra.get("field_mask") or _DEFAULT_FIELD_MASK)
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=config.timeout_seconds,
            headers={
                "X-Goog-Api-Key": config.api_key,
                "Content-Type": "application/json",
            },
        )
        # Crude rate limiter: minimum interval between requests.
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
        body: dict[str, Any] = {
            "maxResultCount": self.NEARBY_MAX_RESULTS,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius_m),
                }
            },
            "languageCode": str(self.config.extra.get("language_code", "vi")),
        }
        if category:
            body["includedTypes"] = [category]

        data = await self._post("/places:searchNearby", body, field_mask=self._field_mask)
        for place in data.get("places", []) or []:
            yield self._to_record(place)

    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        try:
            data = await self._get(
                f"/places/{source_poi_id}", field_mask=_DETAIL_FIELD_MASK
            )
        except AdapterError as exc:
            if "404" in str(exc):
                return None
            raise
        return self._to_record(data)

    async def health_check(self) -> bool:
        # The cheapest no-cost call is a malformed request that's still
        # authenticated — we send an empty searchNearby body and accept any
        # non-401/403 response as "auth + connectivity ok". Even 400 confirms
        # the API key is valid.
        try:
            resp = await self._client.post(
                "/places:searchNearby",
                json={},
                headers={"X-Goog-FieldMask": "places.id"},
            )
        except httpx.HTTPError as exc:
            logger.warning("google_places health_check transport failed: %s", exc)
            return False
        return resp.status_code not in (401, 403)

    # ----------------------------------------------------------------- private

    async def _wait_for_quota(self) -> None:
        async with self._rate_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_allowed_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed_at = max(now, self._next_allowed_at) + self._min_interval_s

    async def _post(
        self, path: str, body: dict[str, Any], *, field_mask: str
    ) -> dict[str, Any]:
        await self._wait_for_quota()
        try:
            resp = await self._client.post(
                path, json=body, headers={"X-Goog-FieldMask": field_mask}
            )
        except httpx.HTTPError as exc:
            raise AdapterTransientError(f"google_places transport: {exc}") from exc
        return self._parse(resp)

    async def _get(self, path: str, *, field_mask: str) -> dict[str, Any]:
        await self._wait_for_quota()
        try:
            resp = await self._client.get(path, headers={"X-Goog-FieldMask": field_mask})
        except httpx.HTTPError as exc:
            raise AdapterTransientError(f"google_places transport: {exc}") from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            raise AdapterTransientError(
                f"google_places {resp.status_code}: {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise AdapterError(
                f"google_places {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    @staticmethod
    def _to_record(place: dict[str, Any]) -> RawPOIRecord:
        loc = place.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        location = (float(lat), float(lng)) if lat is not None and lng is not None else None
        return RawPOIRecord(
            source_poi_id=str(place["id"]),
            raw_payload=place,
            location=location,
        )
