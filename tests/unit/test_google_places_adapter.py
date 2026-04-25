"""Tests for GooglePlacesAdapter using httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from poi_lake.adapters.base import AdapterConfig, AdapterError
from poi_lake.adapters.google_places import GooglePlacesAdapter
from tests.fixtures.google_places_responses import NEARBY_RESPONSE, PLACE_DETAIL_RESPONSE


def _build(handler):
    config = AdapterConfig(api_key="test-key", rate_limit_per_second=1000.0, timeout_seconds=5)
    adapter = GooglePlacesAdapter(config)
    transport = httpx.MockTransport(handler)
    # Replace the client created in __init__ with one wired to the mock transport.
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url=GooglePlacesAdapter.BASE_URL,
        headers={"X-Goog-Api-Key": "test-key", "Content-Type": "application/json"},
        timeout=5,
    )
    return adapter


@pytest.mark.asyncio
async def test_fetch_by_area_yields_records() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["field_mask"] = request.headers.get("X-Goog-FieldMask")
        return httpx.Response(200, json=NEARBY_RESPONSE)

    adapter = _build(handler)
    async with adapter:
        records = [r async for r in adapter.fetch_by_area(21.0285, 105.8542, 1000)]

    assert len(records) == 2
    assert records[0].source_poi_id == "ChIJxxxxx-place-1"
    assert records[0].location == (21.0247, 105.8556)
    assert "Highlands" in records[0].raw_payload["displayName"]["text"]
    assert "/places:searchNearby" in captured["url"]
    assert captured["field_mask"] is not None and "places.id" in captured["field_mask"]


@pytest.mark.asyncio
async def test_fetch_by_id_returns_record() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/places/ChIJzzzzz-place-3" in str(request.url)
        return httpx.Response(200, json=PLACE_DETAIL_RESPONSE)

    adapter = _build(handler)
    async with adapter:
        record = await adapter.fetch_by_id("ChIJzzzzz-place-3")

    assert record is not None
    assert record.source_poi_id == "ChIJzzzzz-place-3"
    assert record.location == (21.0263, 105.8550)


@pytest.mark.asyncio
async def test_fetch_by_id_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    adapter = _build(handler)
    async with adapter:
        result = await adapter.fetch_by_id("ChIJ-missing")
    assert result is None


@pytest.mark.asyncio
async def test_4xx_other_raises_adapter_error() -> None:
    from poi_lake.adapters.base import AdapterError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterError):
            [r async for r in adapter.fetch_by_area(0, 0, 100)]


@pytest.mark.asyncio
async def test_5xx_raises_transient() -> None:
    from poi_lake.adapters.base import AdapterTransientError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream error")

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterTransientError):
            [r async for r in adapter.fetch_by_area(0, 0, 100)]


@pytest.mark.asyncio
async def test_category_added_to_request() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"places": []})

    adapter = _build(handler)
    async with adapter:
        [r async for r in adapter.fetch_by_area(21, 105, 500, category="restaurant")]
    import json
    body = json.loads(captured["body"])
    assert body["includedTypes"] == ["restaurant"]


def test_init_requires_api_key() -> None:
    with pytest.raises(AdapterError, match="GOOGLE_PLACES_API_KEY"):
        GooglePlacesAdapter(AdapterConfig())
