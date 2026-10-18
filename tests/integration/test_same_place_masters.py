"""One source record, one master.

A re-crawl brings a place back as a new raw row (new content_hash, same
source_poi_id). The pending pass must attach it to the master that already
holds the place, and ``fold_same_place_masters`` must repair masters that were
split that way before the fix.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select, text

from poi_lake.db import get_engine, get_sessionmaker
from poi_lake.db.models import ProcessedPOI, RawPOI, Source
from poi_lake.pipeline.dedupe import MergeService

EMB = [0.1] * 384
LAT, LNG = 21.0278, 105.8342


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def _isolated_engine() -> AsyncIterator[None]:
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest_asyncio.fixture(loop_scope="function")
async def two_copies(_isolated_engine: None) -> AsyncIterator[dict]:
    """Two raw copies of one place (same source_poi_id), both processed, pending."""
    tag = f"sameplace-{uuid.uuid4().hex[:8]}"
    sm = get_sessionmaker()
    async with sm() as s:
        src = Source(
            code=f"src-{tag}", name="test",
            adapter_class="tests.fakes:FakeAdapter",
            config={}, enabled=False, priority=500,
        )
        s.add(src)
        await s.commit()
        await s.refresh(src)

        proc_ids: list[int] = []
        for i in range(2):
            raw = RawPOI(
                source_id=src.id,
                source_poi_id=f"{tag}-place",
                raw_payload={"name": "Circle K", "scrape": i},
                content_hash=hashlib.sha256(f"{tag}{i}".encode()).hexdigest(),
                location=f"SRID=4326;POINT({LNG} {LAT})",
            )
            s.add(raw)
            await s.commit()
            await s.refresh(raw)
            proc = ProcessedPOI(
                raw_poi_id=raw.id,
                name_original="Circle K",
                name_normalized="circle k",
                name_embedding=EMB,
                brand="Circle K",
                quality_score=0.8,
                location=f"SRID=4326;POINT({LNG} {LAT})",
                merge_status="pending",
            )
            s.add(proc)
            await s.commit()
            await s.refresh(proc)
            proc_ids.append(proc.id)

    yield {"tag": tag, "proc_ids": proc_ids, "source_id": src.id}

    async with sm() as s:
        await s.execute(
            text(
                "DELETE FROM master_poi_history WHERE master_poi_id IN "
                "(SELECT id FROM master_pois WHERE merged_processed_ids && CAST(:i AS BIGINT[]))"
            ),
            {"i": proc_ids},
        )
        await s.execute(
            text("UPDATE processed_pois SET merged_into = NULL WHERE id = ANY(:i)"),
            {"i": proc_ids},
        )
        await s.execute(
            text("DELETE FROM master_pois WHERE merged_processed_ids && CAST(:i AS BIGINT[])"),
            {"i": proc_ids},
        )
        await s.execute(text("DELETE FROM processed_pois WHERE id = ANY(:i)"), {"i": proc_ids})
        await s.execute(
            text("DELETE FROM raw_pois WHERE source_poi_id LIKE :p"), {"p": f"{tag}%"}
        )
        await s.execute(text("DELETE FROM sources WHERE code = :c"), {"c": f"src-{tag}"})
        await s.commit()


async def _make_own_master(pid: int, source_id: int) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        row = (await s.execute(select(ProcessedPOI).where(ProcessedPOI.id == pid))).scalar_one()
        row._source_id = source_id  # type: ignore[attr-defined]
        await MergeService(resolver=None)._make_master(s, [row], {source_id: 500})
        await s.commit()


async def _state(proc_ids: list[int]) -> list[tuple]:
    sm = get_sessionmaker()
    async with sm() as s:
        return list(
            (
                await s.execute(
                    text(
                        "SELECT p.id, p.merge_status, p.merged_into, m.status "
                        "FROM processed_pois p LEFT JOIN master_pois m ON m.id = p.merged_into "
                        "WHERE p.id = ANY(:i) ORDER BY p.id"
                    ),
                    {"i": proc_ids},
                )
            ).all()
        )


async def test_pending_copy_attaches_to_the_existing_master(two_copies: dict) -> None:
    first, second = two_copies["proc_ids"]
    await _make_own_master(first, two_copies["source_id"])
    master_id = (await _state([first]))[0][2]

    sm = get_sessionmaker()
    async with sm() as s:
        attached = await MergeService(resolver=None)._attach_known_places(
            s, {two_copies["source_id"]: 500}
        )
        await s.commit()

    assert attached >= 1
    rows = await _state([first, second])
    assert [r[1] for r in rows] == ["merged", "merged"]
    assert {r[2] for r in rows} == {master_id}, "the copy joined the master, not a new one"


async def test_fold_merges_masters_split_over_one_place(two_copies: dict) -> None:
    first, second = two_copies["proc_ids"]
    await _make_own_master(first, two_copies["source_id"])
    await _make_own_master(second, two_copies["source_id"])
    before = {r[2] for r in await _state([first, second])}
    assert len(before) == 2, "fixture must start split"

    sm = get_sessionmaker()
    async with sm() as s:
        stats = await MergeService(resolver=None).fold_same_place_masters(s, commit_every=1)
    assert stats["masters_merged_away"] >= 1

    rows = await _state([first, second])
    survivors = {r[2] for r in rows}
    assert len(survivors) == 1
    assert survivors <= before, "survivor kept one of the existing ids"
    assert rows[0][3] == "active"

    async with sm() as s:
        loser = (
            await s.execute(
                text("SELECT status, merged_into FROM master_pois WHERE id = ANY(CAST(:i AS UUID[]))"),
                {"i": [str(m) for m in before - survivors]},
            )
        ).one()
    assert loser[0] == "merged_away"
    assert loser[1] in survivors

    async with sm() as s:
        again = await MergeService(resolver=None).same_place_master_groups(s)
    assert not any(set(g) & before for g in again), "nothing left to fold"


async def test_fold_skips_a_group_that_spans_two_places(two_copies: dict) -> None:
    """A master that already holds a second place would drag it into the fold."""
    first, second = two_copies["proc_ids"]
    sm = get_sessionmaker()
    async with sm() as s:
        raw = RawPOI(
            source_id=two_copies["source_id"],
            source_poi_id=f"{two_copies['tag']}-other",
            raw_payload={"name": "Bãi đậu xe"},
            content_hash=hashlib.sha256(f"{two_copies['tag']}-other".encode()).hexdigest(),
            location=f"SRID=4326;POINT({LNG} {LAT})",
        )
        s.add(raw)
        await s.commit()
        await s.refresh(raw)
        other = ProcessedPOI(
            raw_poi_id=raw.id, name_original="Bãi đậu xe", name_normalized="bãi đậu xe",
            name_embedding=EMB, quality_score=0.8,
            location=f"SRID=4326;POINT({LNG} {LAT})", merge_status="pending",
        )
        s.add(other)
        await s.commit()
        await s.refresh(other)
        two_copies["proc_ids"].append(other.id)  # fixture teardown cleans it

    # first + other in one master (an over-merge), second in its own.
    async with sm() as s:
        rows = (
            await s.execute(select(ProcessedPOI).where(ProcessedPOI.id.in_([first, other.id])))
        ).scalars().all()
        for r in rows:
            r._source_id = two_copies["source_id"]  # type: ignore[attr-defined]
        await MergeService(resolver=None)._make_master(s, list(rows), {two_copies["source_id"]: 500})
        await s.commit()
    await _make_own_master(second, two_copies["source_id"])
    before = {r[2] for r in await _state([first, second])}
    assert len(before) == 2

    async with sm() as s:
        stats = await MergeService(resolver=None).fold_same_place_masters(s, commit_every=1)
    assert stats["skipped_multi_place"] >= 1
    assert {r[2] for r in await _state([first, second])} == before, "left alone"
