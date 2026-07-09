"""A dedupe pass must be bounded and must keep the work it already committed.

Before 2026-07 ``dedupe_pending`` held one transaction across every cluster and
committed once at the end. ``run_dedupe`` has ``time_limit = 20 min``; once the
backlog outgrew that (278,584 pending rows on production) every pass was cut
off and committed nothing, so ``master_pois`` never moved.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text

from poi_lake.db import get_engine, get_sessionmaker
from poi_lake.db.models import ProcessedPOI, RawPOI, Source
from poi_lake.pipeline.dedupe import MergeService

EMB = [0.1] * 384
# Far apart (~11 km) so the 55 m spatial clusterer never joins two groups.
CLUSTER_ORIGINS = [(21.00, 105.80), (21.10, 105.80), (21.20, 105.80)]


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def _isolated_engine() -> AsyncIterator[None]:
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


async def _insert_pair(session, source_id: int, tag: str, lat: float, lng: float) -> None:
    """Two near-identical rows 10 m apart — one cluster, one merge."""
    for i, dlat in enumerate((0.0, 0.0001)):
        suffix = uuid.uuid4().hex[:8]
        raw = RawPOI(
            source_id=source_id,
            source_poi_id=f"{tag}-{i}-{suffix}",
            raw_payload={"name": "Circle K", "_test_tag": tag},
            content_hash=hashlib.sha256(f"{tag}{i}{suffix}".encode()).hexdigest(),
            location=f"SRID=4326;POINT({lng} {lat + dlat})",
        )
        session.add(raw)
        await session.commit()
        await session.refresh(raw)

        session.add(
            ProcessedPOI(
                raw_poi_id=raw.id,
                name_original="Circle K",
                name_normalized="circle k",
                name_embedding=EMB,
                brand="Circle K",
                quality_score=0.8,
                location=f"SRID=4326;POINT({lng} {lat + dlat})",
                merge_status="pending",
            )
        )
        await session.commit()


@pytest_asyncio.fixture(loop_scope="function")
async def three_clusters(_isolated_engine: None) -> AsyncIterator[str]:
    tag = f"batchtest-{uuid.uuid4().hex[:8]}"
    sm = get_sessionmaker()
    async with sm() as s:
        src = Source(
            code=f"src-{tag}",
            name="test",
            adapter_class="tests.fakes:FakeAdapter",
            config={},
            enabled=False,
            priority=500,
        )
        s.add(src)
        await s.commit()
        await s.refresh(src)
        for lat, lng in CLUSTER_ORIGINS:
            await _insert_pair(s, src.id, tag, lat, lng)

    yield tag

    async with sm() as s:
        await s.execute(
            text(
                "DELETE FROM master_poi_history WHERE master_poi_id IN "
                "(SELECT merged_into FROM processed_pois WHERE raw_poi_id IN "
                "(SELECT id FROM raw_pois WHERE source_poi_id LIKE :p))"
            ),
            {"p": f"{tag}%"},
        )
        await s.execute(
            text(
                "DELETE FROM master_pois WHERE id IN "
                "(SELECT merged_into FROM processed_pois WHERE raw_poi_id IN "
                "(SELECT id FROM raw_pois WHERE source_poi_id LIKE :p) "
                "AND merged_into IS NOT NULL)"
            ),
            {"p": f"{tag}%"},
        )
        await s.execute(
            text(
                "DELETE FROM processed_pois WHERE raw_poi_id IN "
                "(SELECT id FROM raw_pois WHERE source_poi_id LIKE :p)"
            ),
            {"p": f"{tag}%"},
        )
        await s.execute(
            text("DELETE FROM raw_pois WHERE source_poi_id LIKE :p"), {"p": f"{tag}%"}
        )
        await s.execute(text("DELETE FROM sources WHERE code = :c"), {"c": f"src-{tag}"})
        await s.commit()


async def _merged_count(tag: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        return (
            await s.execute(
                text(
                    "SELECT count(*) FROM processed_pois WHERE merge_status = 'merged' "
                    "AND raw_poi_id IN (SELECT id FROM raw_pois "
                    "WHERE source_poi_id LIKE :p)"
                ),
                {"p": f"{tag}%"},
            )
        ).scalar_one()


async def test_max_clusters_bounds_the_pass(three_clusters: str) -> None:
    tag = three_clusters
    sm = get_sessionmaker()
    async with sm() as s:
        stats = await MergeService(resolver=None).dedupe_pending(
            s, max_clusters=2, commit_every=1
        )

    assert stats["clusters"] == 2, "pass must stop at max_clusters"
    assert stats["clusters_available"] >= 3, "caller must see a backlog remains"

    # The untouched cluster's rows are still pending for the next pass.
    sm = get_sessionmaker()
    async with sm() as s:
        pending = (
            await s.execute(
                text(
                    "SELECT count(*) FROM processed_pois WHERE merge_status = 'pending' "
                    "AND raw_poi_id IN (SELECT id FROM raw_pois "
                    "WHERE source_poi_id LIKE :p)"
                ),
                {"p": f"{tag}%"},
            )
        ).scalar_one()
    assert pending == 2


async def test_committed_clusters_survive_an_interrupted_pass(
    three_clusters: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the regression: a pass cut off mid-way must keep earlier work."""
    tag = three_clusters
    svc = MergeService(resolver=None)
    real = svc._process_one_cluster
    calls = {"n": 0}

    async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("simulated dramatiq TimeLimitExceeded")
        return await real(*args, **kwargs)

    monkeypatch.setattr(svc, "_process_one_cluster", boom)

    sm = get_sessionmaker()
    with pytest.raises(RuntimeError, match="simulated"):
        async with sm() as s:
            await svc.dedupe_pending(s, commit_every=1)

    # commit_every=1 committed cluster #1 before cluster #2 blew up. Each
    # cluster holds 2 rows, so exactly one cluster's worth must have survived
    # the rollback — 0 would mean the pass lost everything, as it used to.
    assert await _merged_count(tag) == 2, (
        "an interrupted pass lost committed work — the whole point of the fix"
    )
