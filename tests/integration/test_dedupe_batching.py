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


class _BrokenResolver:
    """Stands in for Anthropic returning 400 (e.g. credit balance too low)."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, a: dict, b: dict, **kw: object) -> object:
        self.calls += 1
        raise RuntimeError("credit balance is too low")


async def test_broken_llm_resolver_does_not_kill_the_pass(
    three_clusters: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead resolver must degrade the pass, not abort it.

    Production hit exactly this: `anthropic.BadRequestError` on the first
    NEEDS_LLM pair propagated out of `run_dedupe` and killed every pass.
    """
    from poi_lake.pipeline.dedupe import decision as decision_mod
    from poi_lake.pipeline.dedupe import merge as merge_mod

    # Force every pair down the NEEDS_LLM branch.
    monkeypatch.setattr(
        merge_mod, "decide", lambda _score: decision_mod.DedupeDecision.NEEDS_LLM
    )

    resolver = _BrokenResolver()
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    sm = get_sessionmaker()
    async with sm() as s:
        stats = await svc.dedupe_pending(s, commit_every=1)

    assert stats["clusters"] == 3, "the pass must finish all clusters"
    assert stats["llm_errors"] == 1
    # One failure disables the resolver: the API is not retried per pair.
    assert resolver.calls == 1, f"resolver hammered {resolver.calls} times"
    assert stats["llm_calls"] == 1

    # Unresolved pairs stay separate masters — same as running with no resolver.
    assert stats["masters_created"] == 6
    assert await _merged_count(three_clusters) == 6


class _NoCreditResolver:
    """Anthropic saying the wallet is empty — a failure retrying cannot fix."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, a: dict, b: dict, **kw: object) -> object:
        import anthropic
        import httpx

        self.calls += 1
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        msg = "Your credit balance is too low to access the Anthropic API."
        raise anthropic.BadRequestError(
            message=msg,
            response=httpx.Response(400, request=req, json={"error": {"message": msg}}),
            body=None,
        )


async def test_no_credit_aborts_the_pass_but_keeps_committed_work(
    three_clusters: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fatal resolver failure must stop, not degrade.

    Merging NEEDS_LLM pairs without the resolver writes each row to its own
    master and marks it 'merged'; the clusterer only looks at 'pending' rows,
    so those duplicates are permanent. Stopping is the recoverable option.
    """
    from poi_lake.pipeline.dedupe import decision as decision_mod
    from poi_lake.pipeline.dedupe import merge as merge_mod
    from poi_lake.pipeline.dedupe.llm_state import LLMUnavailableError

    # Don't lean on the scorer's thresholds: state the decisions outright.
    # Each cluster holds one pair, so the first call is cluster #1's.
    calls = {"n": 0}

    def decide_second_cluster_needs_llm(score):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return decision_mod.DedupeDecision.AUTO_MERGE
        return decision_mod.DedupeDecision.NEEDS_LLM

    monkeypatch.setattr(merge_mod, "decide", decide_second_cluster_needs_llm)

    resolver = _NoCreditResolver()
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    sm = get_sessionmaker()
    with pytest.raises(LLMUnavailableError, match="credit balance"):
        async with sm() as s:
            await svc.dedupe_pending(s, commit_every=1)

    assert resolver.calls == 1, "must abort on the first fatal error, not retry per pair"
    # Cluster #1 was committed before the failure; it is not rolled back.
    assert await _merged_count(three_clusters) == 2


class _FakeClock:
    """Zero, then far past the budget. Replaces the name `time` inside the
    merge module only — patching the real `time.monotonic` would also rewire
    asyncpg and SQLAlchemy, which call it constantly."""

    def __init__(self) -> None:
        self.calls = 0

    def monotonic(self) -> float:
        self.calls += 1
        return 0.0 if self.calls <= 2 else 99.0


async def test_max_seconds_stops_the_pass(
    three_clusters: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wall-clock is the real bound: a cluster costs ms without LLM, seconds with."""
    from poi_lake.pipeline.dedupe import merge as merge_mod

    monkeypatch.setattr(merge_mod, "time", _FakeClock())

    sm = get_sessionmaker()
    async with sm() as s:
        stats = await MergeService(resolver=None).dedupe_pending(
            s, commit_every=1, max_seconds=10.0
        )

    assert stats["clusters"] == 1, "budget spent after the first cluster"
    assert stats["clusters_available"] >= 3


async def test_deadline_stops_llm_calls_inside_a_cluster(
    three_clusters: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget must bite mid-cluster, not only between clusters.

    One dense cluster is O(n²) pairs, each an LLM round-trip. Production ran a
    single cluster past the actor's 20-minute time_limit while the per-cluster
    check sat unread at the top of the loop.
    """
    from poi_lake.pipeline.dedupe import decision as decision_mod
    from poi_lake.pipeline.dedupe import merge as merge_mod

    monkeypatch.setattr(
        merge_mod, "decide", lambda _s: decision_mod.DedupeDecision.NEEDS_LLM
    )

    class _CountingResolver:
        def __init__(self) -> None:
            self.calls = 0

        async def resolve(self, a: dict, b: dict, **kw: object) -> object:
            self.calls += 1
            raise AssertionError("resolver must not be called past the deadline")

    resolver = _CountingResolver()
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    rows = []  # a cluster's rows; content is irrelevant, only the deadline is
    sm = get_sessionmaker()
    async with sm() as s:
        from sqlalchemy import select

        rows = list((await s.execute(select(ProcessedPOI).limit(2))).scalars().all())

    # Deadline already in the past.
    components, usage = await svc._components_of(rows, deadline=0.0)

    assert resolver.calls == 0, "no LLM call may be made after the deadline"
    assert usage.calls == 0
    assert len(components) == 2, "unresolved pairs stay separate, to retry next pass"


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
