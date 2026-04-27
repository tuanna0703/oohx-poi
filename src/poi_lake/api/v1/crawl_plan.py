"""Crawl-plan admin endpoints.

  POST /admin/crawl-plan/initialize     populate the matrix
  GET  /admin/crawl-plan/status         summary KPIs
  GET  /admin/crawl-plan/matrix         heatmap-shaped data
  GET  /admin/crawl-plan/velocity       hourly throughput
  GET  /admin/crawl-plan/failed         failed plan rows for retry
  POST /admin/crawl-plan/pause          pause all pending rows
  POST /admin/crawl-plan/resume         flip paused → pending
  POST /admin/crawl-plan/retry-failed   flip failed → pending
  POST /admin/crawl-plan/tick           manual tick of the planner (debug)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.config import get_settings
from poi_lake.seeds.openooh_priority import priority_for

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/crawl-plan",
    tags=["admin", "crawl"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------- schemas

class InitializeRequest(BaseModel):
    openooh_codes: list[str] | None = Field(
        default=None,
        description="If null, use every code in openooh_categories table.",
    )
    province_codes: list[str] | None = Field(
        default=None,
        description="If null, use every level=1 admin_unit.",
    )
    cell_size_m: int = Field(default=5000, ge=500, le=20000)
    overwrite: bool = Field(
        default=False,
        description="Reset existing rows back to pending (DESTRUCTIVE — clears progress).",
    )


class InitializeResponse(BaseModel):
    inserted: int
    skipped: int
    overwritten: int
    total: int


class StatusKPIs(BaseModel):
    total: int
    pending: int
    in_progress: int
    done: int
    failed: int
    paused: int
    pois_raw_total: int
    pois_master_total: int
    last_completed_at: datetime | None
    estimated_completion_at: datetime | None


class MatrixCell(BaseModel):
    province_code: str
    province_name: str
    openooh_code: str
    status: str
    cells_done: int
    cells_total: int | None
    cells_failed: int
    pois_raw: int
    pois_master: int
    last_attempt_at: datetime | None
    error_summary: str | None


class VelocityBucket(BaseModel):
    hour: datetime
    pois_raw: int
    jobs_completed: int
    jobs_failed: int


# ---------------------------------------------------------------- endpoints

@router.post("/initialize", response_model=InitializeResponse)
async def initialize(
    payload: InitializeRequest,
    session: AsyncSession = Depends(get_session),
) -> InitializeResponse:
    """Populate the crawl_plan matrix.

    Default: cross-product of all level=1 provinces × all openooh_categories
    rows. Priority is assigned from ``openooh_priority.priority_map`` and
    further boosted (priority -= 50) for the configured priority provinces.
    """
    settings = get_settings()
    priority_provinces = {
        c.strip() for c in settings.crawl_priority_provinces.split(",") if c.strip()
    }

    # Resolve provinces.
    if payload.province_codes:
        provinces = payload.province_codes
    else:
        provinces = [
            r[0]
            for r in (
                await session.execute(
                    text("SELECT code FROM admin_units WHERE level = 1 ORDER BY code")
                )
            ).all()
        ]

    # Resolve OpenOOH codes.
    if payload.openooh_codes:
        openooh_codes = payload.openooh_codes
    else:
        openooh_codes = [
            r[0]
            for r in (
                await session.execute(
                    text("SELECT code FROM openooh_categories ORDER BY level, code")
                )
            ).all()
        ]

    inserted = 0
    skipped = 0
    overwritten = 0
    for prov in provinces:
        prov_boost = -50 if prov in priority_provinces else 0
        for code in openooh_codes:
            base_priority = priority_for(code) + prov_boost
            params = {
                "prov": prov, "code": code,
                "size": payload.cell_size_m, "prio": base_priority,
            }
            if payload.overwrite:
                result = await session.execute(
                    text(
                        """
                        INSERT INTO crawl_plan (province_code, openooh_code,
                            cell_size_m, priority, status)
                        VALUES (:prov, :code, :size, :prio, 'pending')
                        ON CONFLICT (province_code, openooh_code) DO UPDATE SET
                            cell_size_m = EXCLUDED.cell_size_m,
                            priority = EXCLUDED.priority,
                            status = 'pending',
                            cells_total = NULL,
                            cells_done = 0,
                            cells_failed = 0,
                            attempts = 0,
                            last_attempt_at = NULL,
                            completed_at = NULL,
                            error_summary = NULL
                        RETURNING (xmax = 0) AS inserted
                        """
                    ),
                    params,
                )
                row = result.first()
                if row is not None and row[0]:
                    inserted += 1
                else:
                    overwritten += 1
            else:
                result = await session.execute(
                    text(
                        """
                        INSERT INTO crawl_plan (province_code, openooh_code,
                            cell_size_m, priority)
                        VALUES (:prov, :code, :size, :prio)
                        ON CONFLICT (province_code, openooh_code) DO NOTHING
                        RETURNING id
                        """
                    ),
                    params,
                )
                if result.first() is not None:
                    inserted += 1
                else:
                    skipped += 1
    await session.commit()

    total = (
        await session.execute(text("SELECT COUNT(*) FROM crawl_plan"))
    ).scalar_one()
    logger.info(
        "crawl-plan initialize: inserted=%d skipped=%d overwritten=%d total=%d",
        inserted, skipped, overwritten, total,
    )
    return InitializeResponse(
        inserted=inserted, skipped=skipped, overwritten=overwritten, total=int(total),
    )


@router.get("/status", response_model=StatusKPIs)
async def status_kpis(session: AsyncSession = Depends(get_session)) -> StatusKPIs:
    """Top-level KPIs for the dashboard header."""
    settings = get_settings()
    row = (
        await session.execute(
            text(
                """
                SELECT
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status='pending')     AS pending,
                  COUNT(*) FILTER (WHERE status='in_progress') AS in_progress,
                  COUNT(*) FILTER (WHERE status='done')        AS done,
                  COUNT(*) FILTER (WHERE status='failed')      AS failed,
                  COUNT(*) FILTER (WHERE status='paused')      AS paused,
                  COALESCE(SUM(pois_raw), 0)    AS pois_raw_total,
                  COALESCE(SUM(pois_master), 0) AS pois_master_total,
                  MAX(completed_at)             AS last_completed_at
                FROM crawl_plan
                """
            )
        )
    ).one()

    # Estimate completion: take last 24h velocity and extrapolate against pending.
    velocity_row = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) FROM crawl_plan
                WHERE completed_at >= NOW() - INTERVAL '24 hours'
                """
            )
        )
    ).scalar_one()
    pending_remaining = int(row.pending) + int(row.in_progress)
    eta: datetime | None = None
    completed_24h = int(velocity_row)
    if completed_24h > 0 and pending_remaining > 0:
        # Plan rows / day → days to finish.
        days = pending_remaining / completed_24h
        eta = datetime.now(timezone.utc) + timedelta(days=days)

    _ = settings  # silence linter (settings reserved for future tuning)
    return StatusKPIs(
        total=int(row.total),
        pending=int(row.pending),
        in_progress=int(row.in_progress),
        done=int(row.done),
        failed=int(row.failed),
        paused=int(row.paused),
        pois_raw_total=int(row.pois_raw_total),
        pois_master_total=int(row.pois_master_total),
        last_completed_at=row.last_completed_at,
        estimated_completion_at=eta,
    )


@router.get("/matrix", response_model=list[MatrixCell])
async def matrix(
    province_code: str | None = Query(default=None),
    openooh_level: int | None = Query(default=None, ge=1, le=2),
    session: AsyncSession = Depends(get_session),
) -> list[MatrixCell]:
    """Heatmap-shaped data: one row per (province × openooh_code) plan row.

    Optional filters narrow the response. Sort: by province name then
    openooh code so the UI can render a stable grid.
    """
    where: list[str] = []
    params: dict[str, object] = {}
    if province_code:
        where.append("cp.province_code = :prov")
        params["prov"] = province_code
    if openooh_level is not None:
        where.append(
            "cp.openooh_code IN (SELECT code FROM openooh_categories WHERE level = :lvl)"
        )
        params["lvl"] = openooh_level
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (
        await session.execute(
            text(
                f"""
                SELECT cp.province_code, p.name AS province_name,
                       cp.openooh_code, cp.status,
                       cp.cells_done, cp.cells_total, cp.cells_failed,
                       cp.pois_raw, cp.pois_master,
                       cp.last_attempt_at, cp.error_summary
                FROM crawl_plan cp
                JOIN admin_units p ON p.code = cp.province_code
                {where_sql}
                ORDER BY p.name, cp.openooh_code
                """
            ),
            params,
        )
    ).all()
    return [
        MatrixCell(
            province_code=r.province_code,
            province_name=r.province_name,
            openooh_code=r.openooh_code,
            status=r.status,
            cells_done=int(r.cells_done),
            cells_total=int(r.cells_total) if r.cells_total is not None else None,
            cells_failed=int(r.cells_failed),
            pois_raw=int(r.pois_raw),
            pois_master=int(r.pois_master),
            last_attempt_at=r.last_attempt_at,
            error_summary=r.error_summary,
        )
        for r in rows
    ]


@router.get("/velocity", response_model=list[VelocityBucket])
async def velocity(
    hours: int = Query(default=24, ge=1, le=720),
    session: AsyncSession = Depends(get_session),
) -> list[VelocityBucket]:
    """Hourly throughput — POIs collected, jobs completed, jobs failed."""
    rows = (
        await session.execute(
            text(
                """
                WITH bucket AS (
                  SELECT date_trunc('hour', j.completed_at) AS hour,
                         COUNT(*) FILTER (WHERE j.status = 'completed') AS jobs_completed,
                         COUNT(*) FILTER (WHERE j.status = 'failed')    AS jobs_failed,
                         COALESCE(SUM((j.stats->>'raw_count')::int)
                                  FILTER (WHERE j.status = 'completed'), 0) AS pois_raw
                  FROM ingestion_jobs j
                  WHERE j.completed_at >= NOW() - make_interval(hours => :hours)
                  GROUP BY 1
                )
                SELECT hour, pois_raw, jobs_completed, jobs_failed
                FROM bucket ORDER BY hour
                """
            ),
            {"hours": hours},
        )
    ).all()
    return [
        VelocityBucket(
            hour=r.hour,
            pois_raw=int(r.pois_raw),
            jobs_completed=int(r.jobs_completed),
            jobs_failed=int(r.jobs_failed),
        )
        for r in rows
    ]


@router.get("/failed", response_model=list[MatrixCell])
async def failed_rows(
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[MatrixCell]:
    """Failed plan rows for retry UI."""
    rows = (
        await session.execute(
            text(
                """
                SELECT cp.province_code, p.name AS province_name,
                       cp.openooh_code, cp.status,
                       cp.cells_done, cp.cells_total, cp.cells_failed,
                       cp.pois_raw, cp.pois_master,
                       cp.last_attempt_at, cp.error_summary
                FROM crawl_plan cp
                JOIN admin_units p ON p.code = cp.province_code
                WHERE cp.status = 'failed'
                ORDER BY cp.last_attempt_at DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
    ).all()
    return [
        MatrixCell(
            province_code=r.province_code,
            province_name=r.province_name,
            openooh_code=r.openooh_code,
            status=r.status,
            cells_done=int(r.cells_done),
            cells_total=int(r.cells_total) if r.cells_total is not None else None,
            cells_failed=int(r.cells_failed),
            pois_raw=int(r.pois_raw),
            pois_master=int(r.pois_master),
            last_attempt_at=r.last_attempt_at,
            error_summary=r.error_summary,
        )
        for r in rows
    ]


@router.post("/pause", status_code=status.HTTP_200_OK)
async def pause(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Pause all pending rows. In-progress rows finish naturally."""
    result = await session.execute(
        text(
            "UPDATE crawl_plan SET status = 'paused' "
            "WHERE status = 'pending' RETURNING id"
        )
    )
    n = len(result.all())
    await session.commit()
    logger.info("crawl-plan paused: %d rows", n)
    return {"paused_rows": n}


@router.post("/resume", status_code=status.HTTP_200_OK)
async def resume(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    result = await session.execute(
        text(
            "UPDATE crawl_plan SET status = 'pending' "
            "WHERE status = 'paused' RETURNING id"
        )
    )
    n = len(result.all())
    await session.commit()
    logger.info("crawl-plan resumed: %d rows", n)
    return {"resumed_rows": n}


@router.post("/retry-failed", status_code=status.HTTP_200_OK)
async def retry_failed(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Reset failed rows back to pending so the planner picks them up again."""
    result = await session.execute(
        text(
            """
            UPDATE crawl_plan
            SET status = 'pending',
                error_summary = NULL,
                cells_done = 0,
                cells_failed = 0,
                cells_total = NULL,
                last_attempt_at = NULL
            WHERE status = 'failed' RETURNING id
            """
        )
    )
    n = len(result.all())
    await session.commit()
    logger.info("crawl-plan retry-failed: %d rows", n)
    return {"retried_rows": n}


@router.post("/tick", status_code=status.HTTP_202_ACCEPTED)
async def tick_planner() -> dict[str, str]:
    """Manually trigger one planner tick (useful for debugging)."""
    from poi_lake.workers.crawl_planner import run_crawl_planner

    msg = run_crawl_planner.send()
    return {"status": "enqueued", "message_id": msg.message_id}
