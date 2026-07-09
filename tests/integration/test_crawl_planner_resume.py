"""Planner must resume a plan row across ticks until its grid is exhausted.

Regression cover for the 2026-07 stall: the planner sliced ``centers[:budget]``
and flipped the row to ``in_progress``, which its own SELECT excluded. Every
row therefore received exactly one slice and was abandoned. On production all
4,914 rows sat at ``cells_done = 33`` against an average ``cells_total`` of
563 — 5.9% coverage, frozen for six weeks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text

from poi_lake.api.v1.admin import _grid_centers
from poi_lake.db import get_engine, get_sessionmaker

CELL_SIZE_M = 5000
# Roughly 0.2° square near Hanoi — a handful of 5 km cells, enough that a
# budget of 2 needs several ticks to drain it.
BBOX = (105.70, 20.90, 105.90, 21.10)


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def _isolated_engine() -> AsyncIterator[None]:
    """Bind a fresh engine to this test's loop.

    pyproject sets ``asyncio_default_fixture_loop_scope = "session"`` but
    leaves tests on function-scoped loops. The shared engine is cached, so a
    fixture that executes SQL pools a connection on the session loop which the
    test body then reuses on its own loop — asyncpg rejects that. Also,
    ``_tick()`` disposes the engine in its ``finally``, so a cached handle
    cannot survive between tests anyway.
    """
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest_asyncio.fixture(loop_scope="function")
async def plan_row(_isolated_engine: None) -> AsyncIterator[dict]:
    """A province + gosom source + one pending crawl_plan row."""
    suffix = uuid.uuid4().hex[:8]
    prov = f"T{suffix[:6]}"
    sm = get_sessionmaker()

    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO admin_units (code, name, level, "
                "lng_min, lat_min, lng_max, lat_max) "
                "VALUES (:c, :n, 1, :x1, :y1, :x2, :y2)"
            ),
            {
                "c": prov, "n": f"Test {suffix}",
                "x1": BBOX[0], "y1": BBOX[1], "x2": BBOX[2], "y2": BBOX[3],
            },
        )
        # The planner refuses to plan without an enabled gosom source.
        await s.execute(
            text(
                "INSERT INTO sources (code, name, adapter_class, config, "
                "enabled, priority) VALUES ('gosom_scraper', 'gosom', "
                "'poi_lake.adapters.gosom_scraper:GosomScraperAdapter', "
                "'{}'::jsonb, true, 30) "
                "ON CONFLICT (code) DO UPDATE SET enabled = true"
            )
        )
        plan_id = (
            await s.execute(
                text(
                    "INSERT INTO crawl_plan (province_code, openooh_code, "
                    "cell_size_m, priority, status) "
                    "VALUES (:p, 'retail.malls', :sz, 1, 'pending') "
                    "RETURNING id"
                ),
                {"p": prov, "sz": CELL_SIZE_M},
            )
        ).scalar_one()
        await s.commit()

    total = len(_grid_centers(list(BBOX), CELL_SIZE_M))
    yield {"plan_id": int(plan_id), "province": prov, "cells_total": total}

    async with sm() as s:
        await s.execute(
            text("DELETE FROM ingestion_jobs WHERE params->>'crawl_plan_id' = :i"),
            {"i": str(plan_id)},
        )
        await s.execute(text("DELETE FROM crawl_plan WHERE id = :i"), {"i": plan_id})
        await s.execute(text("DELETE FROM admin_units WHERE code = :c"), {"c": prov})
        await s.commit()


async def _row(plan_id: int) -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        r = (
            await s.execute(
                text(
                    "SELECT status, cells_enqueued, cells_total, cells_done "
                    "FROM crawl_plan WHERE id = :i"
                ),
                {"i": plan_id},
            )
        ).one()
    return {
        "status": r.status,
        "enqueued": r.cells_enqueued,
        "total": r.cells_total,
        "done": r.cells_done,
    }


async def _drain_jobs(plan_id: int) -> None:
    """Mark this row's jobs completed so the in_flight guard stays open."""
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'completed', "
                "completed_at = NOW() WHERE params->>'crawl_plan_id' = :i "
                "AND status IN ('pending', 'running')"
            ),
            {"i": str(plan_id)},
        )
        await s.commit()


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never put real messages on the broker — a live worker would run gosom."""
    import poi_lake.workers.ingest as ingest

    monkeypatch.setattr(ingest.run_ingestion_job, "send", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _tiny_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """budget = min(rate*interval//60, batch_size) -> 2, so a row needs many ticks."""
    from poi_lake.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "crawl_batch_size", 2, raising=False)
    monkeypatch.setattr(settings, "crawl_planner_minutes", 10, raising=False)
    monkeypatch.setattr(settings, "crawl_rate_per_hour", 200, raising=False)
    monkeypatch.setattr(settings, "crawl_cell_size_m", CELL_SIZE_M, raising=False)


async def test_row_resumes_across_ticks_until_grid_exhausted(plan_row: dict) -> None:
    from poi_lake.workers.crawl_planner import _tick

    plan_id, total = plan_row["plan_id"], plan_row["cells_total"]
    assert total > 2, "bbox must need more than one tick to drain"

    stats = await _tick()
    assert stats["picked"] == 1
    after_first = await _row(plan_id)
    assert after_first["enqueued"] == 2
    assert after_first["status"] == "in_progress"
    assert after_first["total"] == total

    # The regression: a second tick must still see this in_progress row and
    # dispatch the *next* slice, not the same first cells again.
    await _drain_jobs(plan_id)
    stats = await _tick()
    assert stats["picked"] == 1
    after_second = await _row(plan_id)
    assert after_second["enqueued"] == 4

    # Cells dispatched are distinct — no cell is crawled twice.
    sm = get_sessionmaker()
    async with sm() as s:
        pairs = (
            await s.execute(
                text(
                    "SELECT params->>'lat', params->>'lng' FROM ingestion_jobs "
                    "WHERE params->>'crawl_plan_id' = :i"
                ),
                {"i": str(plan_id)},
            )
        ).all()
    assert len(pairs) == 4
    assert len(set(pairs)) == 4, "planner re-dispatched a cell it already sent"


async def test_fully_enqueued_row_is_not_picked_again(plan_row: dict) -> None:
    from poi_lake.workers.crawl_planner import _tick

    plan_id, total = plan_row["plan_id"], plan_row["cells_total"]
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE crawl_plan SET status = 'in_progress', "
                "cells_total = :t, cells_enqueued = :t WHERE id = :i"
            ),
            {"t": total, "i": plan_id},
        )
        await s.commit()

    stats = await _tick()
    assert stats["picked"] == 0


async def test_finalize_sweep_closes_a_resumed_row(plan_row: dict) -> None:
    """A row whose cells all landed is flipped to done even while 'pending'.

    /pause then /resume leaves a mid-flight row as 'pending', where
    ingestion's own in_progress-gated flip can never fire.
    """
    from poi_lake.workers.crawl_planner import _finalize_completed

    plan_id, total = plan_row["plan_id"], plan_row["cells_total"]
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE crawl_plan SET status = 'pending', cells_total = :t, "
                "cells_enqueued = :t, cells_done = :t WHERE id = :i"
            ),
            {"t": total, "i": plan_id},
        )
        await s.commit()

    async with sm() as s:
        n = await _finalize_completed(s)
    assert n == 1
    assert (await _row(plan_id))["status"] == "done"
