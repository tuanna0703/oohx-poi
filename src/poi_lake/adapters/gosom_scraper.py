"""gosom/google-maps-scraper REST adapter.

Driving model:
  POST /api/v1/jobs              submit a scrape job (returns {id})
  GET  /api/v1/jobs/{id}         poll status
  GET  /api/v1/jobs/{id}/download download results as CSV
  DELETE /api/v1/jobs/{id}        clean up

Notes from the live API (different from the form UI fields):
  * ``keywords`` MUST be a JSON array of strings.
  * ``lat``/``lon`` are STRINGS, not numbers.
  * ``max_time`` is INTEGER seconds, not a duration string.
  * ``fast_mode`` (snake_case) — the form UI uses ``fastmode`` but the JSON
    endpoint expects the snake_case name.
  * Results are CSV only (the OpenAPI spec doesn't expose a JSON results
    endpoint despite ``/json`` returning data on some builds).

The adapter is designed for **enrichment** — short jobs (depth 1-3, single
keyword) so a single ``fetch_by_area`` call returns within ~1-3 minutes.
For full-area sweeps the orchestrator should tile and submit many jobs.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
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


_DONE_STATUSES = frozenset({"ok", "completed", "done", "finished"})
_FAILED_STATUSES = frozenset({"failed", "error", "cancelled", "canceled"})


class GosomScraperAdapter(SourceAdapter):
    code: ClassVar[str] = "gosom_scraper"
    name: ClassVar[str] = "gosom google-maps-scraper"

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        base_url = config.extra.get("base_url") or get_settings().gosom_scraper_url
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=config.timeout_seconds,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        self._lang = str(config.extra.get("lang", "vi"))
        self._depth = int(config.extra.get("depth", 1))
        self._zoom_default = int(config.extra.get("zoom", 15))
        self._fast_mode = bool(config.extra.get("fast_mode", True))
        self._fetch_emails = bool(config.extra.get("fetch_emails", False))
        self._poll_interval_s = float(config.extra.get("poll_interval_s", 5))
        self._max_wait_s = int(config.extra.get("max_wait_s", 600))
        self._cleanup_jobs = bool(config.extra.get("cleanup_jobs", True))

        # 120 places/min = 2/s — adapter rate-limits job *submissions*.
        # Within a single job, gosom paces its own scraping.
        self._min_interval_s = 1.0 / max(config.rate_limit_per_second, 0.001)
        self._next_allowed_at: float = 0.0
        self._rate_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    # ----------------------------------------------------------------- public

    async def fetch_by_area(
        self,
        lat: float,
        lng: float,
        radius_m: int,
        category: str | None = None,
    ) -> AsyncIterator[RawPOIRecord]:
        keywords = [category] if category else self._default_keywords()
        zoom = self._zoom_for_radius(radius_m)

        job_id = await self._submit_job(
            keywords=keywords, lat=lat, lng=lng, radius_m=radius_m, zoom=zoom
        )
        try:
            await self._wait_for_completion(job_id)
            async for record in self._download_and_yield(job_id):
                yield record
        finally:
            if self._cleanup_jobs:
                await self._delete_job_quiet(job_id)

    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        # gosom's REST API has no lookup-by-id. Detail enrichment is done
        # via fetch_by_area with a tight radius around the known coords —
        # which the IngestionService can submit as a separate job.
        logger.debug("gosom fetch_by_id(%s) not supported — returns None", source_poi_id)
        return None

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/api/v1/jobs")
        except httpx.HTTPError as exc:
            logger.warning("gosom health_check transport: %s", exc)
            return False
        return resp.status_code == 200

    # ---------------------------------------------------------------- private

    @staticmethod
    def _default_keywords() -> list[str]:
        # When the IngestionService doesn't pass a category, do a broad VN sweep.
        return ["restaurant", "cafe", "convenience store", "shop"]

    @staticmethod
    def _zoom_for_radius(radius_m: int) -> int:
        # Roughly: zoom 16 ≈ 600m viewport, 15 ≈ 1.2km, 14 ≈ 2.5km, 13 ≈ 5km.
        if radius_m <= 600:
            return 16
        if radius_m <= 1500:
            return 15
        if radius_m <= 4000:
            return 14
        if radius_m <= 10000:
            return 13
        return 12

    async def _submit_job(
        self,
        *,
        keywords: list[str],
        lat: float,
        lng: float,
        radius_m: int,
        zoom: int,
    ) -> str:
        await self._wait_for_quota()
        body: dict[str, Any] = {
            "name": f"poi-lake-{uuid.uuid4().hex[:8]}",
            "keywords": keywords,
            "lang": self._lang,
            "lat": str(lat),               # API expects strings
            "lon": str(lng),
            "zoom": zoom,
            "radius": int(radius_m),
            "depth": self._depth,
            "fast_mode": self._fast_mode,
            "email": self._fetch_emails,
            "max_time": self._max_wait_s,  # integer seconds
        }
        try:
            resp = await self._client.post("/api/v1/jobs", json=body)
        except httpx.HTTPError as exc:
            raise AdapterTransientError(f"gosom transport: {exc}") from exc

        if resp.status_code in (429, 502, 503, 504):
            raise AdapterTransientError(f"gosom {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise AdapterError(f"gosom submit {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            raise AdapterError(f"gosom submit returned no id: {data!r}")
        logger.info("gosom job submitted: id=%s keywords=%s", job_id, keywords)
        return job_id

    async def _wait_for_completion(self, job_id: str) -> None:
        # gosom's ``GET /api/v1/jobs/{id}`` serves the HTML dashboard regardless
        # of the Accept header on the builds we have run against. The list
        # endpoint ``GET /api/v1/jobs`` always returns JSON, so we poll that
        # and filter for our job. Cheap (one request) and reliable.
        deadline = asyncio.get_running_loop().time() + self._max_wait_s + 30
        missing_polls = 0
        max_missing_polls = 6  # ~30s at default 5s interval
        seen_at_least_once = False
        while True:
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                raise AdapterTransientError(
                    f"gosom job {job_id} did not complete within {self._max_wait_s}s"
                )

            try:
                resp = await self._client.get("/api/v1/jobs")
            except httpx.HTTPError as exc:
                raise AdapterTransientError(f"gosom poll transport: {exc}") from exc

            if resp.status_code >= 500:
                raise AdapterTransientError(f"gosom poll {resp.status_code}")
            if resp.status_code >= 400:
                raise AdapterError(f"gosom poll {resp.status_code}: {resp.text[:200]}")

            try:
                jobs = resp.json() or []
            except ValueError:
                jobs = []

            # gosom's list endpoint returns Go-default PascalCase keys
            # (``ID``, ``Status``, ``Name``…) while the POST response returns
            # snake_case (``id``). Match on either form.
            ours = next(
                (j for j in jobs if (j.get("id") or j.get("ID")) == job_id),
                None,
            )
            if ours is None:
                missing_polls += 1
                if seen_at_least_once or missing_polls >= max_missing_polls:
                    # Job vanished from the list — either gosom cleaned it up
                    # mid-flight, or our submit response.id never appeared.
                    # Either way, this run has no recoverable state.
                    raise AdapterError(
                        f"gosom job {job_id} disappeared from the job list"
                    )
                await asyncio.sleep(self._poll_interval_s)
                continue

            seen_at_least_once = True
            missing_polls = 0
            status = str(ours.get("status") or ours.get("Status") or "").lower()
            if status in _FAILED_STATUSES:
                raise AdapterError(f"gosom job {job_id} failed: status={status!r}")
            if status in _DONE_STATUSES:
                logger.debug("gosom job %s status=%s", job_id, status)
                return

            await asyncio.sleep(self._poll_interval_s)

    async def _download_and_yield(self, job_id: str) -> AsyncIterator[RawPOIRecord]:
        try:
            resp = await self._client.get(f"/api/v1/jobs/{job_id}/download")
        except httpx.HTTPError as exc:
            raise AdapterTransientError(f"gosom download transport: {exc}") from exc

        if resp.status_code == 404:
            # No CSV produced — empty result.
            logger.info("gosom job %s produced no results", job_id)
            return
        if resp.status_code >= 400:
            raise AdapterError(f"gosom download {resp.status_code}: {resp.text[:200]}")

        text = resp.text
        if not text.strip():
            return

        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            record = self._row_to_record(row)
            if record is not None:
                yield record

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> RawPOIRecord | None:
        # Prefer Google's place_id; fall back to gosom's data_id, then a hash.
        spid = (row.get("place_id") or row.get("data_id") or "").strip()
        if not spid:
            link = row.get("link") or row.get("title") or ""
            if not link:
                return None
            spid = "gosom-" + str(abs(hash(link)))

        lat_s = (row.get("latitude") or "").strip()
        lng_s = (row.get("longitude") or "").strip()
        location: tuple[float, float] | None = None
        if lat_s and lng_s:
            try:
                location = (float(lat_s), float(lng_s))
            except ValueError:
                location = None

        # Drop empty cells so raw_payload stays small. CSV gives us strings
        # for everything; the normalize layer handles type coercion.
        payload = {k: v for k, v in row.items() if v not in (None, "")}
        return RawPOIRecord(source_poi_id=spid, raw_payload=payload, location=location)

    async def _delete_job_quiet(self, job_id: str) -> None:
        try:
            await self._client.delete(f"/api/v1/jobs/{job_id}")
        except httpx.HTTPError:
            pass  # best-effort cleanup

    async def _wait_for_quota(self) -> None:
        async with self._rate_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_allowed_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed_at = max(now, self._next_allowed_at) + self._min_interval_s
