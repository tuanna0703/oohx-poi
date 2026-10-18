"""Cell probing: probe a province's cells once, then skip the empty ones.

88% of crawl jobs returned nothing on 2026-10-18: the grid spans whole
province bboxes, so most cells are sea, mountain or another country, and each
was crawled once per OpenOOH code.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from poi_lake.api.v1.admin import _grid_centers
from poi_lake.db import get_sessionmaker
from poi_lake.db.models import IngestionJob

CELL_SIZE_M = 5000
BBOX = (105.70, 20.90, 105.90, 21.10)  # a 0.2° square near Hanoi


@pytest_asyncio.fixture(loop_scope="function")
async def plan_row() -> AsyncIterator[dict]:
    suffix = uuid.uuid4().hex[:8]
    prov = f"P{suffix[:6]}"
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO admin_units (code, name, level, lng_min, lat_min, lng_max, lat_max) "
                "VALUES (:c, :n, 1, :x1, :y1, :x2, :y2)"
            ),
            {"c": prov, "n": f"Probe {suffix}",
             "x1": BBOX[0], "y1": BBOX[1], "x2": BBOX[2], "y2": BBOX[3]},
        )
        await s.execute(
            text(
                "INSERT INTO sources (code, name, adapter_class, config, enabled, priority) "
                "VALUES ('gosom_scraper', 'gosom', "
                "'poi_lake.adapters.gosom_scraper:GosomScraperAdapter', '{}'::jsonb, true, 30) "
                "ON CONFLICT (code) DO UPDATE SET enabled = true"
            )
        )
        plan_id = (
            await s.execute(
                text(
                    "INSERT INTO crawl_plan (province_code, openooh_code, cell_size_m, priority, status) "
                    "VALUES (:p, 'retail.grocery', :sz, -1000, 'pending') RETURNING id"
                ),
                {"p": prov, "sz": CELL_SIZE_M},
            )
        ).scalar_one()
        await s.commit()

    centers = _grid_centers(list(BBOX), CELL_SIZE_M)
    yield {"plan_id": int(plan_id), "province": prov, "centers": centers}

    async with sm() as s:
        await s.execute(
            text(
                "DELETE FROM ingestion_jobs WHERE params->>'crawl_plan_id' = :i "
                "OR (params->>'probe_cell_id')::bigint IN "
                "(SELECT id FROM crawl_cells WHERE province_code = :p)"
            ),
            {"i": str(plan_id), "p": prov},
        )
        await s.execute(text("DELETE FROM crawl_plan WHERE province_code = :p"), {"p": prov})
        await s.execute(text("DELETE FROM admin_units WHERE code = :c"), {"c": prov})  # cascades cells
        await s.commit()


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import poi_lake.workers.ingest as ingest

    monkeypatch.setattr(ingest.run_ingestion_job, "send", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from poi_lake.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "crawl_batch_size", 4, raising=False)
    monkeypatch.setattr(s, "crawl_planner_minutes", 10, raising=False)
    monkeypatch.setattr(s, "crawl_rate_per_hour", 200, raising=False)
    monkeypatch.setattr(s, "crawl_cell_size_m", CELL_SIZE_M, raising=False)
    monkeypatch.setattr(s, "crawl_probe_cells", True, raising=False)


async def _cells(prov: str) -> dict[int, str]:
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                text("SELECT idx, status FROM crawl_cells WHERE province_code = :p"), {"p": prov}
            )
        ).all()
    return {int(i): st for i, st in rows}


async def _drain(prov: str) -> None:
    """Complete every in-flight job so the planner's in_flight guard stays open."""
    async with get_sessionmaker()() as s:
        await s.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'completed', completed_at = NOW() "
                "WHERE status IN ('pending','running')"
            )
        )
        await s.commit()


async def _report(prov: str, verdict: dict[int, int]) -> None:
    """Run the ingestion hook for this province's probe jobs: {idx: fetched}."""
    from poi_lake.services.ingestion import IngestionService

    async with get_sessionmaker()() as s:
        jobs = (
            await s.execute(
                select(IngestionJob).where(IngestionJob.params.has_key("probe_cell_id"))  # type: ignore[attr-defined]
            )
        ).scalars().all()
        idx_of = {
            int(cid): int(idx)
            for cid, idx in (
                await s.execute(
                    text("SELECT id, idx FROM crawl_cells WHERE province_code = :p"), {"p": prov}
                )
            ).all()
        }
        svc = IngestionService(s)
        for job in jobs:
            idx = idx_of.get(int(job.params["probe_cell_id"]))
            if idx in verdict:
                await svc._update_probe_cell(job, fetched=verdict[idx])


async def test_probes_first_then_skips_empty_cells(plan_row: dict) -> None:
    from poi_lake.workers.crawl_planner import _tick

    prov, centers, plan_id = plan_row["province"], plan_row["centers"], plan_row["plan_id"]
    n = len(centers)
    assert n > 4

    stats = await _tick()
    assert stats["probes"] == 4 and stats["jobs_enqueued"] == 4
    cells = await _cells(prov)
    assert len(cells) == n
    assert sum(st == "probing" for st in cells.values()) == 4

    async with get_sessionmaker()() as s:
        cat_jobs = (
            await s.execute(
                text("SELECT count(*) FROM ingestion_jobs WHERE params->>'crawl_plan_id' = :i"),
                {"i": str(plan_id)},
            )
        ).scalar_one()
    assert cat_jobs == 0, "no category job before the province is probed"

    # Probes come back: only cells 0 and 2 have anything.
    await _report(prov, {0: 7, 1: 0, 2: 3, 3: 0})
    async with get_sessionmaker()() as s:  # the rest come back empty too
        await s.execute(
            text("UPDATE crawl_cells SET status = 'empty' WHERE province_code = :p AND idx >= 4"),
            {"p": prov},
        )
        await s.commit()
    await _drain(prov)

    stats = await _tick()
    assert stats["probes"] == 0
    assert stats["jobs_enqueued"] == 2
    assert stats["cells_skipped"] == n - 2
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                text(
                    "SELECT cells_enqueued, cells_done, cells_skipped, cells_total "
                    "FROM crawl_plan WHERE id = :i"
                ),
                {"i": plan_id},
            )
        ).one()
        dispatched = {
            (float(a), float(b))
            for a, b in (
                await s.execute(
                    text(
                        "SELECT params->>'lat', params->>'lng' FROM ingestion_jobs "
                        "WHERE params->>'crawl_plan_id' = :i"
                    ),
                    {"i": str(plan_id)},
                )
            ).all()
        }
    assert row.cells_enqueued == n and row.cells_total == n
    assert row.cells_skipped == n - 2 and row.cells_done == n - 2
    assert dispatched == {centers[0], centers[2]}


async def test_cells_with_past_results_are_not_probed(plan_row: dict) -> None:
    from poi_lake.workers.crawl_planner import _tick

    prov, centers = plan_row["province"], plan_row["centers"]
    async with get_sessionmaker()() as s:
        other = (
            await s.execute(
                text(
                    "INSERT INTO crawl_plan (province_code, openooh_code, cell_size_m, priority, status) "
                    "VALUES (:p, 'hospitality', :sz, 5000, 'done') RETURNING id"
                ),
                {"p": prov, "sz": CELL_SIZE_M},
            )
        ).scalar_one()
        src = (await s.execute(text("SELECT id FROM sources WHERE code='gosom_scraper'"))).scalar_one()
        lat, lng = centers[1]
        await s.execute(
            text(
                "INSERT INTO ingestion_jobs (source_id, job_type, params, status, stats) "
                "VALUES (:s, 'area_sweep', CAST(:p AS jsonb), 'completed', '{\"fetched\": 12}'::jsonb)"
            ),
            {"s": src, "p": f'{{"lat": {lat}, "lng": {lng}, "crawl_plan_id": {other}}}'},
        )
        await s.commit()

    await _tick()
    assert (await _cells(prov))[1] == "populated", "known-inhabited cell needs no probe"


async def test_three_failed_probes_count_as_populated(plan_row: dict) -> None:
    from poi_lake.workers.crawl_planner import _tick

    await _tick()
    from poi_lake.services.ingestion import IngestionService

    async with get_sessionmaker()() as s:
        job = (
            await s.execute(
                select(IngestionJob).where(IngestionJob.params.has_key("probe_cell_id")).limit(1)  # type: ignore[attr-defined]
            )
        ).scalar_one()
        cid = int(job.params["probe_cell_id"])
        svc = IngestionService(s)
        for _ in range(2):
            await svc._update_probe_cell(job, fetched=None)
        st = (await s.execute(text("SELECT status FROM crawl_cells WHERE id=:c"), {"c": cid})).scalar_one()
        assert st == "probing", "a failed attempt is retried, not written off"
        await svc._update_probe_cell(job, fetched=None)
        st = (await s.execute(text("SELECT status FROM crawl_cells WHERE id=:c"), {"c": cid})).scalar_one()
    assert st == "populated", "never write off a cell because the scraper failed"
