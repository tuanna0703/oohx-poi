"""Splitting a master that swallowed two different places.

Reproduces the production damage: four ATM rows of one bank, two at each of two
locations 217 m apart. Every non-geometric field agrees — name, national
hotline, brand — so the old AUTO_MERGE branch merged all four into one master
without ever consulting the resolver.

The repair is only possible because a loser keeps its `source_refs` when it is
marked `merged_away`: each raw_poi is still attributable to the master that
owned it, so `split_master` can hand each spatial group back a real id instead
of minting one the Data Engine has never seen.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from poi_lake.db import get_engine, get_sessionmaker
from poi_lake.db.models import ProcessedPOI, RawPOI, Source
from poi_lake.pipeline.dedupe import MergeService

EMB = [0.1] * 384
LAT, LNG = 10.880388, 106.679078
FAR_DLAT = 0.001953  # ~217 m north — the real separation of the two ATMs
NEAR_DLAT = 0.00002  # ~2 m — a genuine duplicate of the same ATM


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def _isolated_engine() -> AsyncIterator[None]:
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest_asyncio.fixture(loop_scope="function")
async def overmerged(_isolated_engine: None) -> AsyncIterator[dict]:
    """Four rows -> four singleton masters -> one over-merged master."""
    tag = f"split-{uuid.uuid4().hex[:8]}"
    sm = get_sessionmaker()
    offsets = [0.0, NEAR_DLAT, FAR_DLAT, FAR_DLAT + NEAR_DLAT]

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
        for i, dlat in enumerate(offsets):
            raw = RawPOI(
                source_id=src.id,
                source_poi_id=f"{tag}-{i}",
                raw_payload={"name": "ATM SACOMBANK"},
                content_hash=hashlib.sha256(f"{tag}{i}".encode()).hexdigest(),
                location=f"SRID=4326;POINT({LNG} {LAT + dlat})",
            )
            s.add(raw)
            await s.commit()
            await s.refresh(raw)

            proc = ProcessedPOI(
                raw_poi_id=raw.id,
                name_original="ATM SACOMBANK",
                name_normalized="atm sacombank",
                name_embedding=EMB,
                brand="Sacombank",
                phone_e164="+84190055588",  # one hotline for every branch
                quality_score=0.8,
                location=f"SRID=4326;POINT({LNG} {LAT + dlat})",
                merge_status="pending",
            )
            s.add(proc)
            await s.commit()
            await s.refresh(proc)
            proc_ids.append(proc.id)

    svc = MergeService(resolver=None)
    async with sm() as s:
        # Each row gets its own master, as the only_pending pass leaves them...
        rows = []
        for pid in proc_ids:
            row = (await s.execute(select(ProcessedPOI).where(ProcessedPOI.id == pid))).scalar_one()
            row._source_id = src.id  # type: ignore[attr-defined]
            rows.append(row)
            await svc._make_master(s, [row], {src.id: 500})
        await s.commit()

        # ...then the pre-gate remerge folds all four onto one survivor.
        survivor = (
            await s.execute(
                text(
                    "SELECT merged_into FROM processed_pois WHERE id = :p"
                ),
                {"p": proc_ids[0]},
            )
        ).scalar_one()
        losers = [
            m
            for m in (
                await s.execute(
                    text(
                        "SELECT DISTINCT merged_into FROM processed_pois "
                        "WHERE id = ANY(:i) AND merged_into <> :s"
                    ),
                    {"i": proc_ids, "s": survivor},
                )
            ).scalars().all()
        ]
        await svc._grow_master(s, survivor, rows, {src.id: 500}, losers)
        await s.commit()

    yield {"tag": tag, "proc_ids": proc_ids, "survivor": survivor, "losers": losers}

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
        await s.execute(text("DELETE FROM raw_pois WHERE source_poi_id LIKE :p"), {"p": f"{tag}%"})
        await s.execute(text("DELETE FROM sources WHERE code = :c"), {"c": f"src-{tag}"})
        await s.commit()


async def _active_masters(proc_ids: list[int]) -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT p.id, p.merged_into, m.status "
                    "FROM processed_pois p JOIN master_pois m ON m.id = p.merged_into "
                    "WHERE p.id = ANY(:i)"
                ),
                {"i": proc_ids},
            )
        ).all()
    return {pid: (mid, status) for pid, mid, status in rows}


async def test_the_fixture_really_is_over_merged(overmerged: dict) -> None:
    """Guard: without this, the split test could pass on already-correct data."""
    state = await _active_masters(overmerged["proc_ids"])
    assert len({mid for mid, _ in state.values()}) == 1, "all four share one master"


async def test_split_separates_the_two_locations(overmerged: dict) -> None:
    proc_ids = overmerged["proc_ids"]
    sm = get_sessionmaker()
    async with sm() as s:
        result = await MergeService().split_master(s, overmerged["survivor"], eps_meters=55.0)
        await s.commit()

    assert result["changed"] is True
    assert result["groups"] == 2, "217 m apart, eps 55 m — two clusters"

    state = await _active_masters(proc_ids)
    near = {state[proc_ids[0]][0], state[proc_ids[1]][0]}
    far = {state[proc_ids[2]][0], state[proc_ids[3]][0]}
    assert len(near) == 1, "the two rows 2 m apart stay together"
    assert len(far) == 1, "so do the other two"
    assert near != far, "but the two locations are now different masters"
    assert all(status == "active" for _, status in state.values())


async def test_the_survivor_keeps_its_id(overmerged: dict) -> None:
    """The Data Engine stores master_pois.id as source.pois.external_id."""
    survivor = overmerged["survivor"]
    sm = get_sessionmaker()
    async with sm() as s:
        result = await MergeService().split_master(s, survivor, eps_meters=55.0)
        await s.commit()

    assert str(survivor) in result["masters"], "the survivor id must not move"


async def test_the_second_master_is_resurrected_not_minted(overmerged: dict) -> None:
    """Every group gets back an id its rows used to have."""
    sm = get_sessionmaker()
    async with sm() as s:
        result = await MergeService().split_master(s, overmerged["survivor"], eps_meters=55.0)
        await s.commit()

    known = {str(overmerged["survivor"])} | {str(x) for x in overmerged["losers"]}
    assert set(result["masters"]) <= known, "no id the Data Engine has never seen"


async def test_split_is_a_no_op_on_a_tight_master(overmerged: dict) -> None:
    """A master whose members really are one place must be left alone."""
    sm = get_sessionmaker()
    async with sm() as s:
        await MergeService().split_master(s, overmerged["survivor"], eps_meters=55.0)
        await s.commit()

    state = await _active_masters(overmerged["proc_ids"])
    tight = state[overmerged["proc_ids"][0]][0]

    async with sm() as s:
        again = await MergeService().split_master(s, tight, eps_meters=55.0)
        await s.commit()

    assert again["changed"] is False
    assert again["groups"] == 1


async def test_dry_run_writes_nothing(overmerged: dict) -> None:
    """Rolling back must leave the over-merge exactly as it was."""
    sm = get_sessionmaker()
    async with sm() as s:
        await MergeService().split_master(s, overmerged["survivor"], eps_meters=55.0)
        await s.rollback()

    state = await _active_masters(overmerged["proc_ids"])
    assert len({mid for mid, _ in state.values()}) == 1, "still one master"


async def test_split_refuses_a_master_that_is_not_active(overmerged: dict) -> None:
    loser = overmerged["losers"][0]
    sm = get_sessionmaker()
    async with sm() as s:
        with pytest.raises(ValueError, match="merged_away"):
            await MergeService().split_master(s, loser)
