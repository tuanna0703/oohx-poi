"""Tests for OSMOverpassAdapter."""

from __future__ import annotations

import httpx
import pytest

from poi_lake.adapters.base import AdapterConfig
from poi_lake.adapters.osm_overpass import OSMOverpassAdapter
from tests.fixtures.osm_overpass_responses import NEARBY_RESPONSE


def _build(handler):
    config = AdapterConfig(rate_limit_per_second=1000.0, timeout_seconds=5)
    adapter = OSMOverpassAdapter(config)
    transport = httpx.MockTransport(handler)
    adapter._client = httpx.AsyncClient(transport=transport, timeout=5)  # type: ignore[attr-defined]
    return adapter


@pytest.mark.asyncio
async def test_fetch_by_area_parses_node_and_way() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json=NEARBY_RESPONSE)

    adapter = _build(handler)
    async with adapter:
        records = [r async for r in adapter.fetch_by_area(21.0285, 105.8542, 1000)]

    assert len(records) == 3
    ids = {r.source_poi_id for r in records}
    assert ids == {"osm-node-12345", "osm-way-67890", "osm-node-11111"}

    # Node uses lat/lon directly; way uses center.
    node = next(r for r in records if r.source_poi_id == "osm-node-12345")
    assert node.location == (21.0285, 105.8542)
    way = next(r for r in records if r.source_poi_id == "osm-way-67890")
    assert way.location == (21.0290, 105.8550)

    # Built query mentions our tag keys.
    body = captured["body"]
    assert "amenity" in body and "shop" in body and "around:1000" in body


@pytest.mark.asyncio
async def test_fetch_by_area_with_category() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"elements": []})

    adapter = _build(handler)
    async with adapter:
        [r async for r in adapter.fetch_by_area(21, 105, 500, category="cafe")]

    body = captured["body"]
    # Single tag key per category — should appear with =cafe under multiple keys.
    assert '"amenity"="cafe"' in body or '"shop"="cafe"' in body


@pytest.mark.asyncio
async def test_fetch_by_id_node() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 99,
                        "lat": 21.0,
                        "lon": 105.0,
                        "tags": {"amenity": "bank", "name": "Vietcombank"},
                    }
                ]
            },
        )

    adapter = _build(handler)
    async with adapter:
        record = await adapter.fetch_by_id("osm-node-99")

    assert record is not None
    assert record.source_poi_id == "osm-node-99"
    assert record.raw_payload["tags"]["name"] == "Vietcombank"


@pytest.mark.asyncio
async def test_fetch_by_id_invalid_format() -> None:
    from poi_lake.adapters.base import AdapterError

    adapter = _build(lambda req: httpx.Response(200, json={}))
    async with adapter:
        with pytest.raises(AdapterError, match="invalid OSM source_poi_id"):
            await adapter.fetch_by_id("not-a-real-id")


@pytest.mark.asyncio
async def test_5xx_transient() -> None:
    from poi_lake.adapters.base import AdapterTransientError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="gateway timeout")

    adapter = _build(handler)
    async with adapter:
        with pytest.raises(AdapterTransientError):
            [r async for r in adapter.fetch_by_area(0, 0, 100)]
