"""Crawl planner worker.

Periodic actor that drains the ``crawl_plan`` queue at a gosom-friendly
rate. On each tick:

1. Compute current load: how many ingest jobs are pending+running.
2. If load >= ``CRAWL_BATCH_SIZE`` → bail (workers are already saturated).
3. Otherwise pull the next ``budget`` rows by (priority, last_attempt_at)
   and convert each into ingest jobs (cells × keywords).
4. Mark plan rows ``in_progress`` with the cell count so the on-completion
   hook can track progress.

Throttling shape: tick every ``CRAWL_PLANNER_MINUTES`` minutes; per tick
budget = ``CRAWL_RATE_PER_HOUR / (60 / CRAWL_PLANNER_MINUTES)``. Default
200/hour and 10-minute tick → 33 jobs per tick. Each plan row spawns
``cells_total`` jobs, so 1-2 plan rows actually fit per tick at full rate.

Triggered:
  * automatic — armed at worker startup via a Timer (see workers/__init__.py)
  * on demand — POST /admin/crawl-plan/tick → ``run_crawl_planner.send()``
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
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
            # attempted rows at the front; tied rows go by oldest attempt.
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT cp.id, cp.province_code, cp.openooh_code,
                               cp.cell_size_m,
                               p.lng_min, p.lat_min, p.lng_max, p.lat_max
                        FROM crawl_plan cp
                        JOIN admin_units p ON p.code = cp.province_code
                        WHERE cp.status = 'pending'
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
            picked = 0
            jobs_enqueued = 0
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

                # If this row would push us over budget, slice it. We still
                # mark in_progress so it doesn't get re-picked; remaining
                # cells will be filled in later ticks (planner re-emits the
                # whole row only on retry-failed).
                allowance = max(0, budget - jobs_enqueued)
                use_centers = centers[:allowance]
                radius = size // 2

                # Enqueue ingest jobs, tagged with crawl_plan_id.
                from poi_lake.workers.ingest import run_ingestion_job
                job_ids = []
                for lat, lng in use_centers:
                    params = {
                        "lat": lat, "lng": lng, "radius_m": radius,
                        "category": r.openooh_code,
                        "crawl_plan_id": int(r.id),
                    }
                    job_id_row = (
                        await session.execute(
                            text(
                                """
                                INSERT INTO ingestion_jobs
                                    (source_id, job_type, params, status, stats)
                                VALUES
                                    (:src, 'area_sweep', cast(:params AS jsonb),
                                     'pending', '{}'::jsonb)
                                RETURNING id
                                """
                            ),
                            {
                                "src": src_id,
                                "params": _json_dumps(params),
                            },
                        )
                    ).scalar_one()
                    job_ids.append(int(job_id_row))
                jobs_enqueued += len(job_ids)

                await session.execute(
                    text(
                        """
                        UPDATE crawl_plan SET
                          status = 'in_progress',
                          cells_total = :total,
                          attempts = attempts + 1,
                          last_attempt_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"total": cells_total, "id": int(r.id)},
                )
                picked += 1
                # Dispatch after row update so worker doesn't race.
                for jid in job_ids:
                    run_ingestion_job.send(jid)

            await session.commit()
            return {
                "picked": picked,
                "jobs_enqueued": jobs_enqueued,
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
    except Exception:  # noqa: BLE001
        logger.exception("recompute pois_master failed (non-fatal)")
        await session.rollback()


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
                WHERE params ? 'crawl_plan_id'
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
    # Pause everything pending so the operator can investigate.
    result = await session.execute(
        text(
            """
            UPDATE crawl_plan
            SET status = 'paused',
                error_summary = 'auto-paused: failure rate '
                  || ROUND((:fr * 100)::numeric, 1) || '% in 30m'
            WHERE status = 'pending'
            RETURNING id
            """
        ),
        {"fr": fail_rate},
    )
    n = len(result.all())
    await session.commit()
    return n
