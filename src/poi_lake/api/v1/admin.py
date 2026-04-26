"""Admin endpoints — internal only (X-Admin-Token).

Phase 2 surface:
  POST /api/v1/admin/ingestion-jobs       trigger a new job
  GET  /api/v1/admin/ingestion-jobs       list recent jobs
  GET  /api/v1/admin/ingestion-jobs/{id}  job detail
  GET  /api/v1/admin/sources              source registry
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.db.models import (
    IngestionJob,
    IngestionJobStatus,
    Source,
)
from poi_lake.schemas import (
    CreateIngestionJob,
    IngestionJobOut,
    IngestionJobsList,
    SourceOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(session: AsyncSession = Depends(get_session)) -> list[Source]:
    rows = (await session.execute(select(Source).order_by(Source.priority))).scalars().all()
    return list(rows)


@router.post(
    "/ingestion-jobs",
    response_model=IngestionJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ingestion_job(
    payload: CreateIngestionJob,
    session: AsyncSession = Depends(get_session),
) -> IngestionJobOut:
    src = (
        await session.execute(select(Source).where(Source.code == payload.source_code))
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, f"source {payload.source_code!r} not found")
    if not src.enabled:
        raise HTTPException(409, f"source {payload.source_code!r} is disabled")

    job = IngestionJob(
        source_id=src.id,
        job_type=payload.job_type,
        params=payload.params,
        status=IngestionJobStatus.PENDING.value,
        stats={},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Enqueue the dramatiq actor. Importing here keeps the worker module
    # out of the request critical path on cold start.
    from poi_lake.workers.ingest import run_ingestion_job

    run_ingestion_job.send(job.id)
    logger.info("enqueued ingestion job %d (source=%s)", job.id, src.code)

    return _to_out(job, src.code)


@router.get("/ingestion-jobs", response_model=IngestionJobsList)
async def list_ingestion_jobs(
    status_eq: str | None = Query(default=None, alias="status"),
    source_code: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> IngestionJobsList:
    stmt = select(IngestionJob, Source.code).join(
        Source, Source.id == IngestionJob.source_id
    )
    count_stmt = select(func.count(IngestionJob.id))
    if status_eq:
        stmt = stmt.where(IngestionJob.status == status_eq)
        count_stmt = count_stmt.where(IngestionJob.status == status_eq)
    if source_code:
        stmt = stmt.join(Source, Source.id == IngestionJob.source_id, isouter=False)
        stmt = stmt.where(Source.code == source_code)
        count_stmt = count_stmt.where(
            IngestionJob.source_id.in_(select(Source.id).where(Source.code == source_code))
        )
    stmt = stmt.order_by(desc(IngestionJob.created_at)).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    total = (await session.execute(count_stmt)).scalar_one()
    items = [_to_out(job, code) for (job, code) in rows]
    return IngestionJobsList(items=items, total=total)


@router.post("/dedupe/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_dedupe() -> dict[str, str]:
    """Enqueue a dedupe pass. The worker handles all currently-pending rows."""
    from poi_lake.workers.dedupe import run_dedupe

    msg = run_dedupe.send()
    logger.info("enqueued dedupe pass: message_id=%s", msg.message_id)
    return {"status": "enqueued", "message_id": msg.message_id}


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobOut)
async def get_ingestion_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> IngestionJobOut:
    row = (
        await session.execute(
            select(IngestionJob, Source.code)
            .join(Source, Source.id == IngestionJob.source_id)
            .where(IngestionJob.id == job_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, f"ingestion_job {job_id} not found")
    job, code = row
    return _to_out(job, code)


def _to_out(job: IngestionJob, source_code: str) -> IngestionJobOut:
    return IngestionJobOut(
        id=job.id,
        source_id=job.source_id,
        source_code=source_code,
        job_type=job.job_type,
        params=job.params or {},
        status=job.status,
        stats=job.stats or {},
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        created_at=job.created_at or datetime.now(timezone.utc),
    )
