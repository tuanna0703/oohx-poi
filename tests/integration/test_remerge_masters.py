"""Folding duplicate masters back together.

Reproduces the damage a resolver-less pass does: a NEEDS_LLM pair is never
deferred — each row becomes its own master, marked ``merged``, and the normal
pass only looks at ``pending`` rows, so nothing revisits them. ``remerge_masters``
is the repair, and the ``include_existing_masters=True`` this module's docstring
has promised since Phase 4 without ever implementing it.
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
async def split_pair(_isolated_engine: None) -> AsyncIterator[dict]:
    """Two rows 10 m apart, each already merged into its own master.

    Exactly the state a resolver-less pass leaves behind.
    """
    tag = f"remerge-{uuid.uuid4().hex[:8]}"
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
        for i, dlat in enumerate((0.0, 0.0001)):
            raw = RawPOI(
                source_id=src.id,
                source_poi_id=f"{tag}-{i}",
                raw_payload={"name": "Circle K"},
                content_hash=hashlib.sha256(f"{tag}{i}".encode()).hexdigest(),
                location=f"SRID=4326;POINT({LNG} {LAT + dlat})",
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
                location=f"SRID=4326;POINT({LNG} {LAT + dlat})",
                merge_status="pending",
            )
            s.add(proc)
            await s.commit()
            await s.refresh(proc)
            proc_ids.append(proc.id)

    # Give each row its own master, through the real service so the rows look
    # exactly like the ones a resolver-less production pass left behind.
    from sqlalchemy import select

    svc = MergeService(resolver=None)
    async with sm() as s:
        for pid in proc_ids:
            row = (
                await s.execute(select(ProcessedPOI).where(ProcessedPOI.id == pid))
            ).scalar_one()
            row._source_id = src.id  # type: ignore[attr-defined]
            await svc._make_master(s, [row], {src.id: 500})
        await s.commit()

    yield {"tag": tag, "proc_ids": proc_ids, "source_id": src.id}

    async with sm() as s:
        # Scope by the members each master absorbed — covers the survivor and
        # every merged_away loser, whose history rows also hold an FK.
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


async def _masters_of(proc_ids: list[int]) -> list[tuple]:
    sm = get_sessionmaker()
    async with sm() as s:
        return list(
            (
                await s.execute(
                    text(
                        "SELECT m.id, m.status, m.merged_into, m.version "
                        "FROM master_pois m WHERE m.id IN "
                        "(SELECT DISTINCT merged_into FROM processed_pois "
                        " WHERE id = ANY(:i) AND merged_into IS NOT NULL)"
                    ),
                    {"i": proc_ids},
                )
            ).all()
        )


async def test_two_masters_fold_into_one_keeping_the_survivor_id(
    split_pair: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_ids = split_pair["proc_ids"]

    before = await _masters_of(proc_ids)
    assert len({m[0] for m in before}) == 2, "fixture must start with two masters"
    survivor_before = sorted(before, key=lambda m: str(m[0]))

    from poi_lake.pipeline.dedupe import decision as decision_mod
    from poi_lake.pipeline.dedupe import merge as merge_mod

    # The pair is exactly the ambiguous kind the resolver is meant to settle.
    monkeypatch.setattr(
        merge_mod, "decide", lambda _s: decision_mod.DedupeDecision.AUTO_MERGE
    )

    sm = get_sessionmaker()
    async with sm() as s:
        stats = await MergeService(resolver=None).remerge_masters(
            s, ids=proc_ids, commit_every=1
        )

    assert stats["clusters"] == 1
    assert stats["masters_merged_away"] == 1
    assert stats["members_moved"] == 2

    after = await _masters_of(proc_ids)
    assert len(after) == 1, "both rows must now point at one master"
    survivor_id, status, merged_into, version = after[0]
    assert status == "active"
    assert merged_into is None
    assert version >= 2, "survivor was updated in place, not recreated"
    assert survivor_id in {m[0] for m in survivor_before}, "survivor kept its id"

    # The loser is retired, not deleted, and forwards to the survivor.
    sm = get_sessionmaker()
    async with sm() as s:
        losers = list(
            (
                await s.execute(
                    text(
                        "SELECT id, status, merged_into, archived_reason "
                        "FROM master_pois "
                        "WHERE status = 'merged_away' AND merged_into = :sid"
                    ),
                    {"sid": survivor_id},
                )
            ).all()
        )
    assert len(losers) == 1
    assert losers[0][2] == survivor_id
    assert "remerged into" in (losers[0][3] or "")


async def test_remerge_is_idempotent_once_the_cluster_is_one_master(
    split_pair: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second run finds nothing to fold — no wasted work, no wasted LLM calls."""
    from poi_lake.pipeline.dedupe import decision as decision_mod
    from poi_lake.pipeline.dedupe import merge as merge_mod

    monkeypatch.setattr(
        merge_mod, "decide", lambda _s: decision_mod.DedupeDecision.AUTO_MERGE
    )

    proc_ids = split_pair["proc_ids"]
    sm = get_sessionmaker()

    async with sm() as s:
        first = await MergeService(resolver=None).remerge_masters(
            s, ids=proc_ids, commit_every=1
        )
    assert first["masters_merged_away"] == 1

    async with sm() as s:
        second = await MergeService(resolver=None).remerge_masters(
            s, ids=proc_ids, commit_every=1
        )
    assert second["clusters"] == 0, "a collapsed cluster must not be touched again"
    assert second["masters_merged_away"] == 0
    assert second["llm_calls"] == 0
