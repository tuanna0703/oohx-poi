"""Crawl plan priorities: the ``openooh_priority`` tiers, corrected by yield.

A row's priority is ``priority_for(code)``, minus 50 in the priority
provinces, plus ``YIELD_PENALTY`` when the code's grid jobs have proved
unproductive: fewer than ``CRAWL_MIN_YIELD`` new places per job on average,
over at least ``CRAWL_YIELD_MIN_JOBS`` jobs in the last ``YIELD_WINDOW_DAYS``.
Such a code is still crawled, after everything that pays.

Only grid rows are judged. District sweeps (and brand sweeps, which keep the
priority ``/brands`` gave them) run once per district and are cheap.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.config import get_settings
from poi_lake.pipeline.normalize.openooh_keywords import BRAND_PREFIX
from poi_lake.seeds.openooh_priority import priority_for

logger = logging.getLogger(__name__)

YIELD_WINDOW_DAYS = 21
YIELD_PENALTY = 300


async def grid_yields(session: AsyncSession, days: int = YIELD_WINDOW_DAYS) -> dict[str, tuple[int, float]]:
    """``{code: (jobs, average new places per job)}`` for grid category jobs."""
    rows = (
        await session.execute(
            text(
                """
                SELECT params->>'category' AS code, COUNT(*) AS jobs,
                       AVG(COALESCE((stats->>'new')::int, 0)) AS new_avg
                FROM ingestion_jobs
                WHERE status = 'completed'
                  AND params ? 'crawl_plan_id'
                  AND COALESCE(params->>'sweep', '') <> 'district'
                  AND completed_at > NOW() - make_interval(days => :d)
                GROUP BY 1
                """
            ),
            {"d": days},
        )
    ).all()
    return {r.code: (int(r.jobs), float(r.new_avg or 0)) for r in rows if r.code}


def penalized(yields: dict[str, tuple[int, float]], min_yield: float, min_jobs: int) -> list[str]:
    """Codes with enough jobs to judge whose average yield is below ``min_yield``."""
    if min_yield <= 0:
        return []
    return sorted(c for c, (jobs, avg) in yields.items() if jobs >= min_jobs and avg < min_yield)


async def reprioritize(session: AsyncSession) -> dict[str, Any]:
    """Re-apply the tiers, the province boost and the yield penalty to every
    non-brand row, leaving status and progress alone. Commits."""
    settings = get_settings()
    boosted = [c.strip() for c in settings.crawl_priority_provinces.split(",") if c.strip()]
    codes = [
        r[0]
        for r in (await session.execute(text("SELECT DISTINCT openooh_code FROM crawl_plan"))).all()
        if not r[0].startswith(BRAND_PREFIX)  # set by /brands, not by the category tiers
    ]
    slow = penalized(await grid_yields(session), settings.crawl_min_yield, settings.crawl_yield_min_jobs)
    result = await session.execute(
        text(
            """
            UPDATE crawl_plan cp
            SET priority = v.prio
                + CASE WHEN cp.province_code = ANY(CAST(:boosted AS TEXT[])) THEN -50 ELSE 0 END
                + CASE WHEN cp.sweep = 'grid' AND cp.openooh_code = ANY(CAST(:slow AS TEXT[]))
                       THEN :penalty ELSE 0 END
            FROM unnest(CAST(:codes AS TEXT[]), CAST(:prios AS INT[])) AS v(code, prio)
            WHERE cp.openooh_code = v.code
              AND cp.priority IS DISTINCT FROM v.prio
                + CASE WHEN cp.province_code = ANY(CAST(:boosted AS TEXT[])) THEN -50 ELSE 0 END
                + CASE WHEN cp.sweep = 'grid' AND cp.openooh_code = ANY(CAST(:slow AS TEXT[]))
                       THEN :penalty ELSE 0 END
            RETURNING cp.id
            """
        ),
        {
            "boosted": boosted,
            "codes": codes,
            "prios": [priority_for(c) for c in codes],
            "slow": slow,
            "penalty": YIELD_PENALTY,
        },
    )
    n = len(result.all())
    await session.commit()
    logger.info("crawl-plan reprioritized: %d rows changed, slow codes %s", n, slow)
    return {"rows_changed": n, "slow_codes": slow}
