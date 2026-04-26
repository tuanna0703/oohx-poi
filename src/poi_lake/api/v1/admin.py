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


class TiledJobRequest(BaseModel):
    """Tile a bounding box into ``cell_size_m`` × ``cell_size_m`` cells and
    submit one ingestion job per cell. Useful for area sweeps that exceed
    a single source's per-query result cap (gosom is ~30-40 / keyword)."""

    source_code: str = Field(min_length=1)
    bbox: list[float] = Field(
        min_length=4, max_length=4,
        description="[lng_min, lat_min, lng_max, lat_max]",
    )
    cell_size_m: int = Field(default=5000, ge=200, le=20000)
    category: str | None = None
    max_jobs: int = Field(default=50, ge=1, le=500)


def _grid_centers(
    bbox: list[float], cell_size_m: int
) -> list[tuple[float, float]]:
    """Return (lat, lng) centres of a regular grid covering ``bbox``.

    Always emits at least one centre. When ``cell_size_m`` exceeds the bbox
    on a given axis, the axis collapses to a single row/column centred on
    the bbox midpoint — so a tiny bbox always gets a reasonable single
    centre instead of an empty grid.
    """
    import math

    lng_min, lat_min, lng_max, lat_max = bbox
    if not (lng_min < lng_max and lat_min < lat_max):
        raise ValueError("bbox must be [lng_min, lat_min, lng_max, lat_max]")

    avg_lat_rad = math.radians((lat_min + lat_max) / 2.0)
    cell_lat_deg = cell_size_m / 111_000.0
    cos_lat = max(math.cos(avg_lat_rad), 0.05)
    cell_lng_deg = cell_size_m / (111_000.0 * cos_lat)

    n_lat = max(1, math.ceil((lat_max - lat_min) / cell_lat_deg))
    n_lng = max(1, math.ceil((lng_max - lng_min) / cell_lng_deg))
    step_lat = (lat_max - lat_min) / n_lat
    step_lng = (lng_max - lng_min) / n_lng

    centers: list[tuple[float, float]] = []
    for i in range(n_lat):
        lat = lat_min + step_lat * (i + 0.5)
        for j in range(n_lng):
            lng = lng_min + step_lng * (j + 0.5)
            centers.append((round(lat, 6), round(lng, 6)))
    return centers


@router.post(
    "/ingestion-jobs/tiled",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_tiled_ingestion_jobs(
    payload: TiledJobRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Submit one ``area_sweep`` job per grid cell over ``bbox``.

    Cell radius is half the cell side, which gives a small overlap between
    neighbouring cells — that's what we want for dedupe to fold near-edge
    duplicates instead of leaving them as parallel masters.
    """
    src = (
        await session.execute(select(Source).where(Source.code == payload.source_code))
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, f"source {payload.source_code!r} not found")
    if not src.enabled:
        raise HTTPException(409, f"source {payload.source_code!r} is disabled")

    centers = _grid_centers(payload.bbox, payload.cell_size_m)
    if not centers:
        raise HTTPException(400, "bbox produced zero cells — check coordinate order")
    if len(centers) > payload.max_jobs:
        raise HTTPException(
            400,
            f"would create {len(centers)} jobs (cap max_jobs={payload.max_jobs}); "
            f"raise the cap or use a larger cell_size_m",
        )

    radius = payload.cell_size_m // 2
    from poi_lake.workers.ingest import run_ingestion_job

    job_ids: list[int] = []
    for lat, lng in centers:
        params: dict[str, object] = {"lat": lat, "lng": lng, "radius_m": radius}
        if payload.category:
            params["category"] = payload.category
        job = IngestionJob(
            source_id=src.id,
            job_type="area_sweep",
            params=params,
            status=IngestionJobStatus.PENDING.value,
            stats={},
        )
        session.add(job)
        await session.flush()
        job_ids.append(job.id)
    await session.commit()

    # Dispatch after commit so workers see the rows.
    for jid in job_ids:
        run_ingestion_job.send(jid)

    logger.info(
        "tiled-ingest: %d jobs queued source=%s cells=%d radius=%dm category=%s",
        len(job_ids), payload.source_code, len(centers), radius, payload.category,
    )
    return {"job_ids": job_ids, "count": len(job_ids), "cell_radius_m": radius}


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
