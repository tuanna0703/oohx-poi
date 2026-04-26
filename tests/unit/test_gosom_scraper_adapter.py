"""GosomScraperAdapter unit tests with httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from poi_lake.adapters.base import AdapterConfig, AdapterError, AdapterTransientError
from poi_lake.adapters.gosom_scraper import GosomScraperAdapter
from tests.fixtures.gosom_responses import (
    EMPTY_CSV,
    JOB_DONE,
    JOB_FAILED,
    JOB_RUNNING,
    SAMPLE_CSV,
    SUBMIT_RESPONSE,
)


def _build(handler):
    config = AdapterConfig(
        rate_limit_per_second=1000.0,  # effectively no rate limit in tests
        timeout_seconds=5,
        extra={
            "base_url": "http://gosom.test:8080",
            "lang": "vi",
            "depth": 2,
            "fast_mode": True,
            "poll_interval_s": 0.01,  # tight loop in tests
            "max_wait_s": 30,
            "cleanup_jobs": True,
        },
    )
    adapter = GosomScraperAdapter(config)
    transport = httpx.MockTransport(handler)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://gosom.test:8080",
        timeout=5,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    return adapter


@pytest.mark.asyncio
async def test_full_happy_path() -> None:
    submit_body: dict = {}
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/jobs":
            submit_body.update(json.loads(request.content))
            return httpx.Response(201, json=SUBMIT_RESPONSE)
        if request.method == "GET" and path.endswith("/download"):
            return httpx.Response(200, text=SAMPLE_CSV, headers={"Content-Type": "text/csv"})
        if request.method == "GET" and path == "/api/v1/jobs":
            # Status polling uses the LIST endpoint (the per-id endpoint
            # returns HTML on real gosom builds).
            return httpx.Response(200, json=[JOB_DONE])
        if request.method == "DELETE" and path.startswith("/api/v1/jobs/"):
            deleted.append(path.split("/")[-1])
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        records = [
            r async for r in adapter.fetch_by_area(21.0285, 105.8542, 1000, "circle k")
        ]

    # Submitted with the right shape: keywords array, lat/lon strings, max_time int.
    assert submit_body["keywords"] == ["circle k"]
    assert submit_body["lat"] == "21.0285"
    assert submit_body["lon"] == "105.8542"
    assert isinstance(submit_body["max_time"], int)
    assert submit_body["fast_mode"] is True
    assert submit_body["radius"] == 1000

    # Two records parsed from CSV.
    assert len(records) == 2
    ids = {r.source_poi_id for r in records}
    assert ids == {"ChIJxxx-1", "ChIJyyy-2"}

    first = next(r for r in records if r.source_poi_id == "ChIJxxx-1")
    assert first.location == (21.0250, 105.8545)
    assert first.raw_payload["title"] == "Circle K - Bà Triệu"
    assert first.raw_payload["phone"] == "1800 6915"

    # Job was cleaned up.
    assert SUBMIT_RESPONSE["id"] in deleted


@pytest.mark.asyncio
async def test_polls_until_done() -> None:
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(201, json=SUBMIT_RESPONSE)
        if request.method == "GET" and path.endswith("/download"):
            return httpx.Response(200, text=EMPTY_CSV, headers={"Content-Type": "text/csv"})
        if request.method == "GET" and path == "/api/v1/jobs":
            poll_count["n"] += 1
            job = JOB_RUNNING if poll_count["n"] < 3 else JOB_DONE
            return httpx.Response(200, json=[job])
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        records = [r async for r in adapter.fetch_by_area(21, 105, 500)]

    assert records == []
    assert poll_count["n"] >= 3  # polled until status flipped


@pytest.mark.asyncio
async def test_failed_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(201, json=SUBMIT_RESPONSE)
        if request.method == "GET" and path == "/api/v1/jobs":
            return httpx.Response(200, json=[JOB_FAILED])
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterError, match="failed"):
            [r async for r in adapter.fetch_by_area(21, 105, 500)]


@pytest.mark.asyncio
async def test_5xx_on_submit_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterTransientError):
            [r async for r in adapter.fetch_by_area(21, 105, 500)]


@pytest.mark.asyncio
async def test_4xx_on_submit_is_fatal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={"code": 422, "message": "bad params"})
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterError, match="422"):
            [r async for r in adapter.fetch_by_area(21, 105, 500)]


@pytest.mark.asyncio
async def test_job_disappears_raises() -> None:
    """If the gosom job never appears in the list (e.g. auto-cleaned up
    after a server restart), the adapter gives up after a bounded number
    of polls instead of looping forever."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(201, json=SUBMIT_RESPONSE)
        if request.method == "GET" and path == "/api/v1/jobs":
            return httpx.Response(200, json=[])  # always empty
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterError, match="disappeared"):
            [r async for r in adapter.fetch_by_area(21, 105, 500)]


@pytest.mark.asyncio
async def test_404_on_download_returns_no_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(201, json=SUBMIT_RESPONSE)
        if request.method == "GET" and path.endswith("/download"):
            return httpx.Response(404)
        if request.method == "GET" and path == "/api/v1/jobs":
            return httpx.Response(200, json=[JOB_DONE])
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        records = [r async for r in adapter.fetch_by_area(21, 105, 500)]
    assert records == []


@pytest.mark.asyncio
async def test_health_check_calls_jobs_list() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=[])

    adapter = _build(handler)
    async with adapter:
        ok = await adapter.health_check()
    assert ok is True
    assert "GET /api/v1/jobs" in seen


@pytest.mark.asyncio
async def test_fetch_by_id_returns_none() -> None:
    """gosom doesn't support detail-by-id; adapter is documented to return None."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    adapter = _build(handler)
    async with adapter:
        result = await adapter.fetch_by_id("ChIJxxx-1")
    assert result is None


def test_zoom_for_radius() -> None:
    z = GosomScraperAdapter._zoom_for_radius
    assert z(300) == 16
    assert z(1000) == 15
    assert z(3000) == 14
    assert z(8000) == 13
    assert z(20000) == 12
