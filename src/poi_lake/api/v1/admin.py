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

from datetime import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.db.models import (
    APIClient,
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
from poi_lake.services.api_keys import generate_api_key

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


class ManualMergeRequest(BaseModel):
    processed_poi_ids: list[int] = Field(min_length=1, max_length=100)


@router.post("/dedupe/manual-merge", status_code=status.HTTP_200_OK)
async def manual_merge(
    payload: ManualMergeRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Force-merge the given pending processed_pois into one master."""
    from poi_lake.pipeline.dedupe import MergeService

    master_id = await MergeService().merge_records(session, payload.processed_poi_ids)
    if master_id is None:
        return {"status": "skipped", "reason": "no eligible pending rows"}
    return {"status": "merged", "master_poi_id": str(master_id)}


class ManualRejectRequest(BaseModel):
    processed_poi_ids: list[int] = Field(min_length=1, max_length=100)
    reason: str = Field(default="manual override", max_length=200)


@router.post("/dedupe/manual-reject", status_code=status.HTTP_200_OK)
async def manual_reject(
    payload: ManualRejectRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Mark the given processed_pois as ``rejected`` so they are excluded
    from future dedupe passes."""
    from sqlalchemy import text as _text

    result = await session.execute(
        _text(
            "UPDATE processed_pois SET merge_status = 'rejected', "
            "merge_reason = :reason WHERE id = ANY(:ids) "
            "AND merge_status = 'pending'"
        ),
        {"reason": payload.reason, "ids": payload.processed_poi_ids},
    )
    await session.commit()
    return {"status": "rejected", "rows_updated": result.rowcount}


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


# ----------------------------------------------------------------- api_clients


class APIClientOut(BaseModel):
    id: int
    name: str
    permissions: list[str]
    rate_limit_per_minute: int
    enabled: bool
    created_at: _dt


class APIClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    permissions: list[str] = Field(default_factory=lambda: ["read:master"])
    rate_limit_per_minute: int = Field(default=1000, ge=1, le=100_000)


class APIClientCreated(APIClientOut):
    api_key: str  # plaintext, shown ONCE


@router.get("/api-clients", response_model=list[APIClientOut])
async def list_api_clients(session: AsyncSession = Depends(get_session)) -> list[APIClient]:
    rows = (
        await session.execute(select(APIClient).order_by(APIClient.id))
    ).scalars().all()
    return [
        APIClientOut(
            id=r.id,
            name=r.name,
            permissions=list(r.permissions or []),
            rate_limit_per_minute=r.rate_limit_per_minute,
            enabled=r.enabled,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post(
    "/api-clients",
    response_model=APIClientCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_client(
    payload: APIClientCreate,
    session: AsyncSession = Depends(get_session),
) -> APIClientCreated:
    """Create an API client and return the plaintext key. Show once, never again."""
    existing = (
        await session.execute(select(APIClient).where(APIClient.name == payload.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"api client {payload.name!r} already exists")

    key = generate_api_key()
    client = APIClient(
        name=payload.name,
        api_key_hash=key.hash,
        permissions=payload.permissions,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        enabled=True,
    )
    session.add(client)
    await session.commit()
    await session.refresh(client)
    logger.info("created api_client name=%s id=%d", payload.name, client.id)

    return APIClientCreated(
        id=client.id,
        name=client.name,
        permissions=list(client.permissions or []),
        rate_limit_per_minute=client.rate_limit_per_minute,
        enabled=client.enabled,
        created_at=client.created_at,
        api_key=key.plaintext,
    )


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
