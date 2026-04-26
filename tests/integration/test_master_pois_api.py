"""End-to-end tests for the master_pois consumer API.

Uses an httpx ASGITransport so we hit the real FastAPI app + the live DB
without binding to a TCP port. Each test seeds a small set of master_pois
under a per-test ``test_tag`` and cleans up afterwards.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from poi_lake.db import get_sessionmaker
from poi_lake.db.models import APIClient, MasterPOI
from poi_lake.main import app
from poi_lake.services.api_keys import generate_api_key

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _emb(seed: float = 0.42, dim: int = 384) -> str:
    """Return the pgvector text representation of a normalized vector."""
    raw = [(seed * (i + 1)) % 5 - 2 for i in range(dim)]
    n = sum(v * v for v in raw) ** 0.5
    vals = [v / n for v in raw]
    return "[" + ",".join(f"{v:.7f}" for v in vals) + "]"


async def _seed_master(
    session,
    *,
    name: str,
    lat: float,
    lng: float,
    test_tag: str,
    brand: str | None = None,
    category: str | None = None,
    confidence: float = 0.9,
) -> uuid.UUID:
    sql = text(
        """
        INSERT INTO master_pois (
            canonical_name, canonical_name_embedding,
            location, openooh_category, brand,
            source_refs, merged_processed_ids,
            confidence, status, version
        ) VALUES (
            :name, CAST(:emb AS VECTOR(384)),
            ST_GeogFromText(:wkt), :cat, :brand,
            CAST(:srcrefs AS JSONB), CAST(:procids AS BIGINT[]),
            :conf, 'active', 1
        ) RETURNING id
        """
    )
    result = await session.execute(
        sql,
        {
            "name": name,
            "emb": _emb(),
            "wkt": f"SRID=4326;POINT({lng} {lat})",
            "cat": category,
            "brand": brand,
            "srcrefs": json.dumps(
                [{"source": "test", "source_poi_id": f"{test_tag}-{name}", "raw_poi_id": 0}]
            ),
            "procids": [],
            "conf": confidence,
        },
    )
    mid = result.scalar_one()
    await session.commit()
    # Tag the row so cleanup is a single DELETE WHERE source_refs @> ...
    await session.execute(
        text("UPDATE master_pois SET archived_reason = :tag WHERE id = :id"),
        {"tag": test_tag, "id": mid},
    )
    await session.commit()
    return mid


async def _cleanup(session, test_tag: str) -> None:
    await session.execute(
        text("DELETE FROM master_poi_history WHERE master_poi_id IN "
             "(SELECT id FROM master_pois WHERE archived_reason = :tag)"),
        {"tag": test_tag},
    )
    await session.execute(
        text("DELETE FROM master_pois WHERE archived_reason = :tag"),
        {"tag": test_tag},
    )
    await session.execute(
        text("DELETE FROM api_clients WHERE name LIKE :p"),
        {"p": f"test-{test_tag}-%"},
    )
    await session.commit()


async def _make_client(session, test_tag: str, *, permissions=None, enabled=True) -> tuple[str, int]:
    key = generate_api_key()
    name = f"test-{test_tag}-{uuid.uuid4().hex[:6]}"
    client = APIClient(
        name=name,
        api_key_hash=key.hash,
        permissions=permissions if permissions is not None else ["read:master"],
        rate_limit_per_minute=10000,
        enabled=enabled,
    )
    session.add(client)
    await session.commit()
    await session.refresh(client)
    return key.plaintext, client.id


async def _http():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_missing_api_key_returns_401() -> None:
    async with await _http() as c:
        r = await c.get("/api/v1/master-pois")
    assert r.status_code == 401


async def test_invalid_api_key_returns_401() -> None:
    async with await _http() as c:
        r = await c.get("/api/v1/master-pois", headers={"X-API-Key": "pl_garbage"})
    assert r.status_code == 401


async def test_disabled_client_returns_403() -> None:
    sm = get_sessionmaker()
    tag = "disable-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        plaintext, _ = await _make_client(s, tag, enabled=False)
    try:
        async with await _http() as c:
            r = await c.get("/api/v1/master-pois", headers={"X-API-Key": plaintext})
        assert r.status_code == 403
    finally:
        async with sm() as s:
            await _cleanup(s, tag)


async def test_missing_permission_returns_403() -> None:
    sm = get_sessionmaker()
    tag = "perm-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        plaintext, _ = await _make_client(s, tag, permissions=["read:other"])
    try:
        async with await _http() as c:
            r = await c.get("/api/v1/master-pois", headers={"X-API-Key": plaintext})
        assert r.status_code == 403
    finally:
        async with sm() as s:
            await _cleanup(s, tag)


async def test_radius_search_and_filters() -> None:
    sm = get_sessionmaker()
    tag = "list-" + uuid.uuid4().hex[:6]

    async with sm() as s:
        plaintext, _ = await _make_client(s, tag)
        # 3 nearby (Hồ Hoàn Kiếm), 1 far (Đà Nẵng)
        ck1 = await _seed_master(s, name="Circle K HK1", lat=21.0285, lng=105.8542,
                                 test_tag=tag, brand="Circle K", category="retail")
        ck2 = await _seed_master(s, name="Circle K HK2", lat=21.0290, lng=105.8550,
                                 test_tag=tag, brand="Circle K", category="retail",
                                 confidence=0.7)
        cafe = await _seed_master(s, name="Cộng Cà Phê", lat=21.0288, lng=105.8545,
                                  test_tag=tag, brand="Cong Caphe", category="hospitality")
        far = await _seed_master(s, name="Circle K Đà Nẵng", lat=16.0590, lng=108.2208,
                                 test_tag=tag, brand="Circle K", category="retail")

    try:
        async with await _http() as c:
            headers = {"X-API-Key": plaintext}

            # 1) radius around Hồ Hoàn Kiếm catches HK1, HK2, cafe — not far one
            r = await c.get(
                "/api/v1/master-pois",
                params={"lat": 21.0285, "lng": 105.8542, "radius_m": 500},
                headers=headers,
            )
            assert r.status_code == 200
            data = r.json()
            ids = {item["id"] for item in data["items"]}
            assert str(ck1) in ids and str(ck2) in ids and str(cafe) in ids
            assert str(far) not in ids

            # 2) brand filter
            r = await c.get(
                "/api/v1/master-pois",
                params={"lat": 21.0285, "lng": 105.8542, "radius_m": 500, "brand": "Circle K"},
                headers=headers,
            )
            ids = {item["id"] for item in r.json()["items"]}
            assert str(ck1) in ids and str(ck2) in ids
            assert str(cafe) not in ids

            # 3) min_confidence excludes ck2 (conf=0.7)
            r = await c.get(
                "/api/v1/master-pois",
                params={
                    "lat": 21.0285, "lng": 105.8542, "radius_m": 500,
                    "brand": "Circle K", "min_confidence": 0.85,
                },
                headers=headers,
            )
            ids = {item["id"] for item in r.json()["items"]}
            assert str(ck1) in ids
            assert str(ck2) not in ids

            # 4) category=hospitality includes the cafe but NOT the Circle Ks
            r = await c.get(
                "/api/v1/master-pois",
                params={"lat": 21.0285, "lng": 105.8542, "radius_m": 500,
                        "category": "hospitality"},
                headers=headers,
            )
            ids = {item["id"] for item in r.json()["items"]}
            assert str(cafe) in ids
            assert str(ck1) not in ids and str(ck2) not in ids

            # 5) pagination per_page caps result count
            r = await c.get(
                "/api/v1/master-pois",
                params={"lat": 21.0285, "lng": 105.8542, "radius_m": 500, "per_page": 2},
                headers=headers,
            )
            body = r.json()
            assert len(body["items"]) == 2
            assert body["total"] >= 3
    finally:
        async with sm() as s:
            await _cleanup(s, tag)


async def test_get_master_and_sources_and_history() -> None:
    sm = get_sessionmaker()
    tag = "detail-" + uuid.uuid4().hex[:6]

    async with sm() as s:
        plaintext, _ = await _make_client(s, tag)
        mid = await _seed_master(s, name="Test Detail POI",
                                 lat=21.0, lng=105.0, test_tag=tag, brand="X")
        # Add a history row so /history returns something.
        await s.execute(
            text(
                """INSERT INTO master_poi_history
                   (master_poi_id, version, changed_fields, previous_values,
                    new_values, change_reason, changed_at)
                   VALUES (:mid, 1, CAST(:cf AS JSONB), CAST(:pv AS JSONB),
                           CAST(:nv AS JSONB), 'initial_merge', NOW())"""
            ),
            {
                "mid": mid,
                "cf": json.dumps(["canonical_name"]),
                "pv": json.dumps({}),
                "nv": json.dumps({"canonical_name": "Test Detail POI"}),
            },
        )
        await s.commit()

    try:
        async with await _http() as c:
            headers = {"X-API-Key": plaintext}

            r = await c.get(f"/api/v1/master-pois/{mid}", headers=headers)
            assert r.status_code == 200
            body = r.json()
            assert body["canonical_name"] == "Test Detail POI"
            assert "lat" in body and "lng" in body

            r = await c.get(f"/api/v1/master-pois/{mid}/sources", headers=headers)
            assert r.status_code == 200
            srcs = r.json()
            assert len(srcs) == 1
            assert srcs[0]["source"] == "test"

            r = await c.get(f"/api/v1/master-pois/{mid}/history", headers=headers)
            assert r.status_code == 200
            h = r.json()
            assert len(h) == 1
            assert h[0]["change_reason"] == "initial_merge"

            # Unknown id → 404
            bad = uuid.uuid4()
            r = await c.get(f"/api/v1/master-pois/{bad}", headers=headers)
            assert r.status_code == 404
    finally:
        async with sm() as s:
            await _cleanup(s, tag)


async def test_search_endpoint() -> None:
    sm = get_sessionmaker()
    tag = "search-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        plaintext, _ = await _make_client(s, tag)
        ck = await _seed_master(s, name="Circle K Tràng Tiền", lat=21.0285,
                                 lng=105.8542, test_tag=tag, brand="Circle K")
        cafe = await _seed_master(s, name="Highlands Coffee Tràng Tiền",
                                   lat=21.0286, lng=105.8543, test_tag=tag,
                                   brand="Highlands Coffee")

    try:
        async with await _http() as c:
            headers = {"X-API-Key": plaintext}

            # 1) text search by brand keyword
            r = await c.post(
                "/api/v1/master-pois/search",
                headers=headers,
                json={"query": "Circle"},
            )
            assert r.status_code == 200
            ids = {item["id"] for item in r.json()["items"]}
            assert str(ck) in ids
            assert str(cafe) not in ids

            # 2) bbox search includes both
            r = await c.post(
                "/api/v1/master-pois/search",
                headers=headers,
                json={"bbox": [105.85, 21.02, 105.86, 21.03]},
            )
            ids = {item["id"] for item in r.json()["items"]}
            assert str(ck) in ids and str(cafe) in ids

            # 3) bbox + brand: cafe is included, Circle K is not
            r = await c.post(
                "/api/v1/master-pois/search",
                headers=headers,
                json={"bbox": [105.85, 21.02, 105.86, 21.03], "brand": "Highlands Coffee"},
            )
            ids = {item["id"] for item in r.json()["items"]}
            assert str(cafe) in ids
            assert str(ck) not in ids
    finally:
        async with sm() as s:
            await _cleanup(s, tag)


async def test_radius_validation() -> None:
    sm = get_sessionmaker()
    tag = "valid-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        plaintext, _ = await _make_client(s, tag)

    try:
        async with await _http() as c:
            # Only lat without lng/radius_m → 400
            r = await c.get(
                "/api/v1/master-pois", params={"lat": 21.0},
                headers={"X-API-Key": plaintext},
            )
            assert r.status_code == 400
    finally:
        async with sm() as s:
            await _cleanup(s, tag)
