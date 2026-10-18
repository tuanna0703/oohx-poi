"""Crawl planner worker.

Periodic actor that drains the ``crawl_plan`` queue at a gosom-friendly
rate. On each tick:

1. Finalize rows whose cells have all landed; recompute pois_master.
2. Compute current load: how many ingest jobs are pending+running.
3. If load >= ``budget * 2`` → bail (workers are already saturated).
4. Otherwise pull rows by (priority, last_attempt_at) that still have cells
   left to dispatch, and turn the next slice into ingest jobs.
5. Advance ``cells_enqueued`` by the slice size and mark the row
   ``in_progress``; the on-completion hook tracks ``cells_done``.

Cell probing (``CRAWL_PROBE_CELLS``, on by default): before any category is
crawled in a province, each of its cells (``crawl_cells``) gets one probe job
with broad keywords. A probe that finds nothing marks the cell ``empty``, and
every category row then passes over it without a job (counted in
``cells_skipped`` and ``cells_done``). Before this, 88% of jobs returned
nothing — the grid spans whole province bboxes, sea and Laos included.

Throttling shape: tick every ``CRAWL_PLANNER_MINUTES`` minutes; per tick
budget = ``CRAWL_RATE_PER_HOUR / (60 / CRAWL_PLANNER_MINUTES)``. Default
200/hour and 10-minute tick → 33 jobs per tick.

A plan row usually holds far more cells than one tick's budget (production
averages 563), so a row is dispatched a slice at a time across many ticks.
``cells_enqueued`` is the resume cursor; it must not be conflated with
``cells_done``, which lags while jobs are still running. Before 2026-07 there
was no cursor and the SELECT excluded ``in_progress`` rows, so every row got
exactly one slice and was then abandoned — coverage froze at 5.9%.

Triggered:
  * automatic — armed at worker startup via a Timer (see workers/__init__.py)
  * on demand — POST /admin/crawl-plan/tick → ``run_crawl_planner.send()``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import dramatiq

logger = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="dedupe",        # piggyback on dedupe queue — low traffic
    max_retries=1,
    time_limit=10 * 60_000,
)
def run_crawl_planner() -> None:
    """One tick of the planner — non-recurring; the Timer in workers/__init__.py
    armed at startup re-fires every interval."""
    logger.info("crawl planner: tick start")
    stats = asyncio.run(_tick())
    logger.info("crawl planner: tick done %r", stats)


async def _tick() -> dict[str, Any]:
    from poi_lake.config import get_settings
    from poi_lake.db import get_engine, get_sessionmaker, session_scope

    settings = get_settings()
    interval_min = max(1, settings.crawl_planner_minutes)
    rate_per_hour = max(1, settings.crawl_rate_per_hour)
    # Jobs we're allowed to enqueue this tick. Min of:
    #   (rate_per_hour * interval_min / 60) — what the throttle budgets, and
    #   crawl_batch_size — hard cap so a single tick can't fan out 1000 jobs.
    budget = min(
        max(1, rate_per_hour * interval_min // 60),
        settings.crawl_batch_size,
    )
    cell_size_m = settings.crawl_cell_size_m

    try:
        async with session_scope() as session:
            from sqlalchemy import text

            # ---- maintenance: recompute pois_master + auto-pause check ----
            await _recompute_pois_master(session)
            await _finalize_completed(session)
            if settings.crawl_probe_cells:
                await _reset_stale_probes(session)
            paused_count = await _maybe_auto_pause(session)
            if paused_count:
                logger.warning(
                    "crawl planner: auto-paused %d rows after failure surge",
                    paused_count,
                )
                return {
                    "skipped": "auto_paused",
                    "paused": paused_count,
                }

            # Don't flood — if there are already many in-flight ingest jobs,
            # let them drain before adding more.
            in_flight = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM ingestion_jobs "
                        "WHERE status IN ('pending','running')"
                    )
                )
            ).scalar_one()
            if int(in_flight) >= budget * 2:
                return {
                    "skipped": "in_flight_high",
                    "in_flight": int(in_flight),
                    "budget": budget,
                }

            # Pull the next plan rows by priority. NULLS FIRST puts never-
            # attempted rows at the front; tied rows go by oldest attempt, so
            # partially-dispatched rows round-robin instead of one row hogging
            # every tick.
            #
            # in_progress rows stay eligible until every cell is dispatched —
            # a tick only ever enqueues `budget` cells, so a row of 563 cells
            # needs ~17 ticks. Excluding them (the pre-2026-07 behaviour) meant
            # each row got exactly one slice and was then abandoned forever.
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT cp.id, cp.province_code, cp.openooh_code,
                               cp.cell_size_m, cp.cells_enqueued, cp.cells_total,
                               p.lng_min, p.lat_min, p.lng_max, p.lat_max
                        FROM crawl_plan cp
                        JOIN admin_units p ON p.code = cp.province_code
                        WHERE cp.status IN ('pending', 'in_progress')
                          AND (cp.cells_total IS NULL
                               OR cp.cells_enqueued < cp.cells_total)
                        ORDER BY cp.priority ASC,
                                 cp.last_attempt_at ASC NULLS FIRST
                        LIMIT 5
                        FOR UPDATE OF cp SKIP LOCKED
                        """
                    )
                )
            ).all()

            if not rows:
                return {"picked": 0, "in_flight": int(in_flight)}

            # Pull the gosom source row once.
            src_row = (
                await session.execute(
                    text("SELECT id, enabled FROM sources WHERE code = 'gosom_scraper'")
                )
            ).first()
            if not src_row:
                logger.warning("gosom_scraper source not found — cannot plan")
                return {"error": "no_gosom_source"}
            if not src_row.enabled:
                logger.warning("gosom_scraper disabled — pausing planner this tick")
                return {"error": "gosom_disabled"}
            src_id = int(src_row.id)

            # Generate cells per plan row, enqueue jobs, track stats.
            from poi_lake.api.v1.admin import _grid_centers
            from poi_lake.pipeline.normalize.openooh_keywords import PROBE_CATEGORY

            picked = 0
            jobs_enqueued = 0
            probes = 0
            cells_skipped = 0
            to_send: list[int] = []
            for r in rows:
                if jobs_enqueued >= budget:
                    break
                bbox = [float(r.lng_min), float(r.lat_min),
                        float(r.lng_max), float(r.lat_max)]
                size = int(r.cell_size_m or cell_size_m)
                centers = _grid_centers(bbox, size)
                cells_total = len(centers)
                if cells_total == 0:
                    await session.execute(
                        text(
                            """
                            UPDATE crawl_plan
                            SET status='failed', cells_total=0,
                                last_attempt_at=NOW(),
                                error_summary='zero cells'
                            WHERE id = :id
                            """
                        ),
                        {"id": int(r.id)},
                    )
                    continue

                # Resume where the last tick stopped. cells_enqueued — not
                # cells_done — is the cursor: a tick can fire while earlier
                # jobs are still running, and slicing on cells_done would
                # re-dispatch cells that are already in flight.
                offset = int(r.cells_enqueued or 0)
                if offset >= cells_total:
                    # Grid shrank (cell_size_m was raised) — nothing left to
                    # dispatch. Leave it to the completion sweep above.
                    continue
                allowance = max(0, budget - jobs_enqueued)
                radius = size // 2

                cell_status: dict[int, str] = {}
                if settings.crawl_probe_cells:
                    cells = await _ensure_cells(session, r.province_code, size, centers)
                    cell_status = {idx: st for idx, (_cid, st) in cells.items()}
                    # Probe the province before crawling any category in it: a
                    # category job on an empty cell is the waste this avoids.
                    unknown = [i for i, st in cell_status.items() if st == "unknown"]
                    if unknown or "probing" in cell_status.values():
                        for idx in unknown[:allowance]:
                            cid = cells[idx][0]
                            lat, lng = centers[idx]
                            jid = await _insert_job(session, src_id, {
                                "lat": lat, "lng": lng, "radius_m": radius,
                                "category": PROBE_CATEGORY, "probe_cell_id": cid,
                            })
                            await session.execute(
                                text(
                                    "UPDATE crawl_cells SET status = 'probing', "
                                    "probe_job_id = :j, dispatched_at = NOW() WHERE id = :c"
                                ),
                                {"j": jid, "c": cid},
                            )
                            to_send.append(jid)
                            jobs_enqueued += 1
                            probes += 1
                        continue

                # Walk the grid from the cursor: dispatch populated (or
                # unprobed, when probing is off) cells, pass over empty ones.
                # Skips cost no budget, so cap them per tick to keep one
                # transaction small.
                job_ids: list[int] = []
                skipped = 0
                pos = offset
                while pos < cells_total and len(job_ids) < allowance and skipped < 5000:
                    if cell_status.get(pos) == "empty":
                        skipped += 1
                    else:
                        lat, lng = centers[pos]
                        job_ids.append(await _insert_job(session, src_id, {
                            "lat": lat, "lng": lng, "radius_m": radius,
                            "category": r.openooh_code,
                            "crawl_plan_id": int(r.id),
                        }))
                    pos += 1
                jobs_enqueued += len(job_ids)
                cells_skipped += skipped
                to_send.extend(job_ids)

                await session.execute(
                    text(
                        """
                        UPDATE crawl_plan SET
                          status = 'in_progress',
                          cells_total = :total,
                          cells_enqueued = :pos,
                          cells_done = cells_done + :skipped,
                          cells_skipped = cells_skipped + :skipped,
                          attempts = attempts + 1,
                          last_attempt_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"total": cells_total, "pos": pos, "skipped": skipped, "id": int(r.id)},
                )
                picked += 1

            await session.commit()
            # Only now do the job rows exist for a worker to load.
            from poi_lake.workers.ingest import run_ingestion_job

            for jid in to_send:
                run_ingestion_job.send(jid)
            return {
                "picked": picked,
                "jobs_enqueued": jobs_enqueued,
                "probes": probes,
                "cells_skipped": cells_skipped,
                "budget": budget,
                "in_flight_before": int(in_flight),
            }
    finally:
        engine = get_engine()
        await engine.dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


def _json_dumps(d: dict[str, Any]) -> str:
    import json
    return json.dumps(d, ensure_ascii=False, default=str)


async def _insert_job(session, src_id: int, params: dict[str, Any]) -> int:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    return int(
        (
            await session.execute(
                text(
                    """
                    INSERT INTO ingestion_jobs (source_id, job_type, params, status, stats)
                    VALUES (:src, 'area_sweep', cast(:params AS jsonb), 'pending', '{}'::jsonb)
                    RETURNING id
                    """
                ),
                {"src": src_id, "params": _json_dumps(params)},
            )
        ).scalar_one()
    )


async def _ensure_cells(  # type: ignore[no-untyped-def]
    session, province_code: str, size: int, centers: list[tuple[float, float]]
) -> dict[int, tuple[int, str]]:
    """Return ``{idx: (cell_id, status)}`` for a province's grid, creating it once.

    New cells start ``unknown``; any cell an earlier category job already
    found something in is marked ``populated`` straight away — there is no
    point probing what we know is inhabited. Matching is on the job's
    lat/lng, which came from the same ``_grid_centers`` call.
    """
    from sqlalchemy import text

    params = {"p": province_code, "s": size}
    found = (
        await session.execute(
            text(
                "SELECT idx, id, status FROM crawl_cells "
                "WHERE province_code = :p AND cell_size_m = :s"
            ),
            params,
        )
    ).all()
    if found:
        return {int(i): (int(cid), st) for i, cid, st in found}

    await session.execute(
        text(
            """
            INSERT INTO crawl_cells (province_code, cell_size_m, idx, lat, lng)
            SELECT :p, :s, t.idx, round(t.lat::numeric, 6), round(t.lng::numeric, 6)
            FROM unnest(CAST(:idx AS INT[]), CAST(:lat AS FLOAT8[]), CAST(:lng AS FLOAT8[]))
                 AS t(idx, lat, lng)
            ON CONFLICT ON CONSTRAINT uq_crawl_cells_prov_size_idx DO NOTHING
            """
        ),
        {
            **params,
            "idx": list(range(len(centers))),
            "lat": [c[0] for c in centers],
            "lng": [c[1] for c in centers],
        },
    )
    await session.execute(
        text(
            """
            UPDATE crawl_cells c SET status = 'populated', probed_at = NOW()
            FROM (
                SELECT DISTINCT round((j.params->>'lat')::numeric, 6) AS lat,
                                round((j.params->>'lng')::numeric, 6) AS lng
                FROM ingestion_jobs j
                JOIN crawl_plan cp ON cp.id = (j.params->>'crawl_plan_id')::bigint
                WHERE cp.province_code = :p AND cp.cell_size_m = :s
                  AND j.status = 'completed'
                  AND COALESCE((j.stats->>'fetched')::int, 0) > 0
            ) h
            WHERE c.province_code = :p AND c.cell_size_m = :s
              AND c.lat = h.lat AND c.lng = h.lng
            """
        ),
        params,
    )
    rows = (
        await session.execute(
            text(
                "SELECT idx, id, status FROM crawl_cells "
                "WHERE province_code = :p AND cell_size_m = :s"
            ),
            params,
        )
    ).all()
    logger.info(
        "crawl planner: created %d cells for %s (%d already known populated)",
        len(rows), province_code, sum(1 for r in rows if r.status == "populated"),
    )
    return {int(i): (int(cid), st) for i, cid, st in rows}


async def _reset_stale_probes(session) -> int:  # type: ignore[no-untyped-def]
    """Return cells stuck in ``probing`` to ``unknown``.

    A worker restart drops in-flight dramatiq messages, so a probe job can
    vanish without ever reporting back; its province would then wait forever.
    """
    from sqlalchemy import text

    result = await session.execute(
        text(
            "UPDATE crawl_cells SET status = 'unknown' "
            "WHERE status = 'probing' AND dispatched_at < NOW() - INTERVAL '2 hours' "
            "RETURNING id"
        )
    )
    n = len(result.all())
    if n:
        await session.commit()
        logger.warning("crawl planner: reset %d stale probes", n)
    return n


async def _recompute_pois_master(session) -> None:  # type: ignore[no-untyped-def]
    """Refresh ``crawl_plan.pois_master`` from the actual master_pois table.

    Run lazily on each planner tick rather than on every dedupe pass —
    the count drifts (master can be merged/deleted independently of
    crawl_plan) and the heatmap updates within one tick of the truth.
    """
    from sqlalchemy import text
    try:
        await session.execute(
            text(
                """
                UPDATE crawl_plan cp
                SET pois_master = COALESCE(sub.n, 0)
                FROM (
                    SELECT province_code, openooh_subcategory AS code, COUNT(*) AS n
                    FROM master_pois
                    WHERE status='active' AND openooh_subcategory IS NOT NULL
                    GROUP BY province_code, openooh_subcategory
                    UNION ALL
                    SELECT province_code, openooh_category AS code, COUNT(*) AS n
                    FROM master_pois
                    WHERE status='active' AND openooh_category IS NOT NULL
                      AND openooh_subcategory IS NULL
                    GROUP BY province_code, openooh_category
                ) sub
                WHERE cp.province_code = sub.province_code
                  AND cp.openooh_code = sub.code
                """
            )
        )
        await session.commit()
    except Exception:
        logger.exception("recompute pois_master failed (non-fatal)")
        await session.rollback()


async def _finalize_completed(session) -> int:  # type: ignore[no-untyped-def]
    """Flip fully-accounted rows to ``done``.

    ``services/ingestion._update_crawl_plan`` normally does this as the last
    cell lands, but only while the row is still ``in_progress``. A row paused
    (or auto-paused) mid-flight, then resumed, comes back as ``pending`` and
    would never flip. This sweep is the backstop: it owns the terminal
    transition so no row can strand with every cell accounted for.

    Returns the number of rows finalized.
    """
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            UPDATE crawl_plan
            SET status = 'done',
                completed_at = COALESCE(completed_at, NOW())
            WHERE status IN ('pending', 'in_progress')
              AND cells_total IS NOT NULL
              AND cells_done + cells_failed >= cells_total
            RETURNING id
            """
        )
    )
    n = len(result.all())
    if n:
        await session.commit()
        logger.info("crawl planner: finalized %d completed rows", n)
    return n


async def _maybe_auto_pause(session) -> int:  # type: ignore[no-untyped-def]
    """Auto-pause crawl if recent jobs are mostly failing.

    Heuristic: in the last 30 minutes, count completed vs failed ingest
    jobs that have a ``crawl_plan_id``. If failed/(failed+done) > 0.5 and
    failed >= 5, pause every pending row to give an admin a chance to
    investigate before burning more gosom quota.

    Returns the number of rows paused (0 if no auto-pause triggered).
    """
    from sqlalchemy import text
    row = (
        await session.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (
                    WHERE status='completed'
                      AND completed_at >= NOW() - INTERVAL '30 minutes'
                  ) AS done,
                  COUNT(*) FILTER (
                    WHERE status='failed'
                      AND completed_at >= NOW() - INTERVAL '30 minutes'
                  ) AS failed
                FROM ingestion_jobs
                WHERE params ? 'crawl_plan_id' OR params ? 'probe_cell_id'
                """
            )
        )
    ).one()
    done = int(row.done)
    failed = int(row.failed)
    total = done + failed
    if total < 10 or failed < 5:
        return 0
    fail_rate = failed / total
    if fail_rate <= 0.5:
        return 0
    # Pause every row that would still dispatch cells, so the operator can
    # investigate before more gosom quota burns. in_progress rows must be
    # included: since the resume-cursor change they keep dispatching across
    # ticks, so pausing only 'pending' would stop nothing.
    result = await session.execute(
        text(
            """
            UPDATE crawl_plan
            SET status = 'paused',
                error_summary = 'auto-paused: failure rate '
                  || ROUND((:fr * 100)::numeric, 1) || '% in 30m'
            WHERE status IN ('pending', 'in_progress')
              AND (cells_total IS NULL OR cells_enqueued < cells_total)
            RETURNING id
            """
        ),
        {"fr": fail_rate},
    )
    n = len(result.all())
    await session.commit()
    return n
