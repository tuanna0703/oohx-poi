"""SpatialClusterer against live Postgres + PostGIS.

Inserts a small set of synthetic processed_pois with known coordinates,
then verifies cluster assignments group them as expected.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from poi_lake.db import get_sessionmaker
from poi_lake.db.models import ProcessedPOI, RawPOI, Source
from poi_lake.pipeline.dedupe import SpatialClusterer

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_processed(
    session,
    source_id: int,
    *,
    name: str,
    lat: float,
    lng: float,
    test_tag: str,
) -> int:
    """Insert one raw_poi + one processed_poi tagged with ``test_tag`` for cleanup."""
    import hashlib

    suffix = uuid.uuid4().hex[:8]
    raw = RawPOI(
        source_id=source_id,
        source_poi_id=f"{test_tag}-{suffix}",
        raw_payload={"name": name, "_test_tag": test_tag},
        content_hash=hashlib.sha256((test_tag + suffix).encode()).hexdigest(),
        location=f"SRID=4326;POINT({lng} {lat})",
    )
    session.add(raw)
    await session.commit()
    await session.refresh(raw)

    proc = ProcessedPOI(
        raw_poi_id=raw.id,
        name_original=name,
        name_normalized=name.lower(),
        name_embedding=[0.0] * 384,
        location=f"SRID=4326;POINT({lng} {lat})",
        merge_status="pending",
    )
    session.add(proc)
    await session.commit()
    await session.refresh(proc)
    return proc.id


async def _cleanup_tag(session, test_tag: str) -> None:
    await session.execute(
        text(
            "DELETE FROM processed_pois WHERE raw_poi_id IN "
            "(SELECT id FROM raw_pois WHERE source_poi_id LIKE :p)"
        ),
        {"p": f"{test_tag}-%"},
    )
    await session.execute(
        text("DELETE FROM raw_pois WHERE source_poi_id LIKE :p"),
        {"p": f"{test_tag}-%"},
    )
    await session.commit()


async def test_three_circle_k_cluster_into_one() -> None:
    """Three Circle Ks within ~30m of each other should cluster together,
    and a fourth one 1km away should be its own cluster."""
    sm = get_sessionmaker()
    tag = "clu1-" + uuid.uuid4().hex[:6]

    async with sm() as s:
        src = (await s.execute(select(Source).where(Source.code == "osm_overpass"))).scalar_one()
        # Three near each other (Hồ Hoàn Kiếm corner)
        a = await _make_processed(s, src.id, name="Circle K — A", lat=21.0285, lng=105.8542, test_tag=tag)
        b = await _make_processed(s, src.id, name="Circle K — B", lat=21.02855, lng=105.85425, test_tag=tag)
        c = await _make_processed(s, src.id, name="Circle K — C", lat=21.02860, lng=105.85430, test_tag=tag)
        # One 1km north — should be its own cluster
        d = await _make_processed(s, src.id, name="Circle K — Far", lat=21.0375, lng=105.8542, test_tag=tag)

    try:
        async with sm() as s:
            clusters = await SpatialClusterer().cluster(s, eps_meters=55, ids=[a, b, c, d])
        assert len(clusters) == 4
        # A, B, C share a cluster id; D has a different one.
        assert clusters[a] == clusters[b] == clusters[c]
        assert clusters[d] != clusters[a]
    finally:
        async with sm() as s:
            await _cleanup_tag(s, tag)


async def test_grouping_helper() -> None:
    by_cluster = {1: 10, 2: 10, 3: 11, 4: 11, 5: 12}
    grouped = SpatialClusterer.group(by_cluster)
    assert grouped == {10: [1, 2], 11: [3, 4], 12: [5]}


async def test_empty_ids_returns_empty() -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        result = await SpatialClusterer().cluster(s, ids=[])
    assert result == {}


async def test_only_pending_filter() -> None:
    """A 'merged' row should not appear in the result when only_pending=True."""
    sm = get_sessionmaker()
    tag = "clu2-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        src = (await s.execute(select(Source).where(Source.code == "osm_overpass"))).scalar_one()
        pending = await _make_processed(s, src.id, name="Pending", lat=21.0, lng=105.0, test_tag=tag)
        merged = await _make_processed(s, src.id, name="Merged", lat=21.0001, lng=105.0001, test_tag=tag)
        await s.execute(
            text("UPDATE processed_pois SET merge_status='merged' WHERE id = :i"),
            {"i": merged},
        )
        await s.commit()

    try:
        async with sm() as s:
            # Without ids: only_pending=True (default) excludes the merged one
            result = await SpatialClusterer().cluster(
                s, eps_meters=55, ids=[pending, merged], only_pending=True
            )
        # ids takes precedence over only_pending in our impl, both should appear
        assert pending in result and merged in result

        async with sm() as s:
            # When we don't pass ids and only_pending=True, only "pending" shows.
            # Use a wide eps so any other unrelated pending rows in DB don't matter
            # — we just verify our merged row is NOT in the result.
            result_pending = await SpatialClusterer().cluster(s, only_pending=True, eps_meters=55)
        assert merged not in result_pending
        assert pending in result_pending
    finally:
        async with sm() as s:
            await _cleanup_tag(s, tag)
