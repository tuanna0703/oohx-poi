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
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.db.models import (
    AdminUnit,
    APIClient,
    IngestionJob,
    IngestionJobStatus,
    Source,
)
from poi_lake.schemas import (
    CreateIngestionJob,
    IngestionJobOut,
    IngestionJobsList,
    RawPOIList,
    RawPOIOut,
    SourceOut,
)
from poi_lake.services.api_keys import generate_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class AdminUnitOut(BaseModel):
    code: str
    name: str
    parent_code: str | None
    level: int
    bbox: list[float]


@router.get("/admin-units", response_model=list[AdminUnitOut])
async def list_admin_units(
    level: int | None = Query(default=None, ge=1, le=3),
    parent_code: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUnitOut]:
    """List provinces / districts / wards. Filter by ``level`` (1=province,
    2=district, 3=ward) and/or ``parent_code`` (e.g. ``parent_code=01``
    returns Hà Nội's districts)."""
    stmt = select(AdminUnit).order_by(AdminUnit.level, AdminUnit.code)
    if level is not None:
        stmt = stmt.where(AdminUnit.level == level)
    if parent_code is not None:
        stmt = stmt.where(AdminUnit.parent_code == parent_code)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AdminUnitOut(
            code=r.code, name=r.name, parent_code=r.parent_code,
            level=r.level, bbox=r.bbox,
        )
        for r in rows
    ]


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
    a single source's per-query result cap (gosom is ~30-40 / keyword).

    Either ``bbox`` OR ``admin_code`` is required. ``admin_code`` resolves
    via the ``admin_units`` table — pass a province code (e.g. ``"01"`` for
    Hà Nội) or a district code (e.g. ``"01.005"`` for Cầu Giấy).

    ``categories`` is a list — one set of jobs per category. ``source_codes``
    is also a list — multiple adapters fan out across every cell. Total
    job count = cells × categories × sources, bounded by ``max_jobs``.
    Picking gosom + osm + foody for one province gives parallel coverage
    across all three sources, which dedupe folds afterwards.
    """

    # Single-source backward-compat — older callers can keep posting
    # ``source_code``; new callers should use ``source_codes``.
    source_code: str | None = Field(default=None, min_length=1)
    source_codes: list[str] = Field(default_factory=list, max_length=10)
    bbox: list[float] | None = Field(
        default=None,
        min_length=4, max_length=4,
        description="[lng_min, lat_min, lng_max, lat_max]; omit if admin_code given",
    )
    # Single admin_code kept for backward compat. ``admin_codes`` lets the
    # caller pick multiple provinces / districts in one request — bboxes
    # are computed per region and cells emitted for each, so jobs cover
    # only the actual admin areas (no wasted cells between separated regions).
    admin_code: str | None = Field(default=None, max_length=20)
    admin_codes: list[str] = Field(default_factory=list, max_length=70)
    cell_size_m: int = Field(default=5000, ge=200, le=20000)
    category: str | None = None
    categories: list[str] = Field(default_factory=list, max_length=20)
    max_jobs: int = Field(default=50, ge=1, le=2000)

    def effective_categories(self) -> list[str | None]:
        cats = list(self.categories)
        if self.category and self.category not in cats:
            cats.insert(0, self.category)
        return cats or [None]

    def effective_sources(self) -> list[str]:
        srcs = list(self.source_codes)
        if self.source_code and self.source_code not in srcs:
            srcs.insert(0, self.source_code)
        if not srcs:
            raise ValueError("at least one of source_code / source_codes is required")
        return srcs

    def effective_admin_codes(self) -> list[str]:
        codes = list(self.admin_codes)
        if self.admin_code and self.admin_code not in codes:
            codes.insert(0, self.admin_code)
        return codes


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
    """Submit one ``area_sweep`` job per (cell × category × source) combo.

    Cell radius is half the cell side, which gives a small overlap between
    neighbouring cells — that's what we want for dedupe to fold near-edge
    duplicates instead of leaving them as parallel masters.
    """
    try:
        source_codes = payload.effective_sources()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Resolve every source up front — fail loudly if any is unknown / disabled.
    src_rows = (
        await session.execute(
            select(Source).where(Source.code.in_(source_codes))
        )
    ).scalars().all()
    sources_by_code = {s.code: s for s in src_rows}
    missing = [c for c in source_codes if c not in sources_by_code]
    if missing:
        raise HTTPException(404, f"sources not found: {missing}")
    disabled = [c for c, s in sources_by_code.items() if not s.enabled]
    if disabled:
        raise HTTPException(409, f"sources disabled: {disabled}")

    # Resolve cells: bbox (single) OR admin_codes (one or more).
    admin_codes = payload.effective_admin_codes()
    centers: list[tuple[float, float]] = []
    bbox_for_response: list[float] | None = None

    if payload.bbox is not None:
        # Explicit bbox path — single rectangle, no admin lookup.
        centers = _grid_centers(payload.bbox, payload.cell_size_m)
        bbox_for_response = payload.bbox
    elif admin_codes:
        # Per-region cells. Each picked province / district contributes its
        # own grid; concatenating gives a non-rectangular coverage that
        # respects political boundaries instead of one huge bbox spanning
        # gaps.
        from poi_lake.db.models import AdminUnit
        admin_rows = (
            await session.execute(
                select(AdminUnit).where(AdminUnit.code.in_(admin_codes))
            )
        ).scalars().all()
        admins_by_code = {r.code: r for r in admin_rows}
        missing_admin = [c for c in admin_codes if c not in admins_by_code]
        if missing_admin:
            raise HTTPException(404, f"admin_codes not found: {missing_admin}")
        for code in admin_codes:
            au = admins_by_code[code]
            centers.extend(_grid_centers(au.bbox, payload.cell_size_m))
    else:
        raise HTTPException(400, "either bbox or admin_code(s) is required")

    if not centers:
        raise HTTPException(400, "no cells produced — check coordinates / cell_size_m")

    categories = payload.effective_categories()
    total_jobs = len(centers) * len(categories) * len(source_codes)
    if total_jobs > payload.max_jobs:
        raise HTTPException(
            400,
            f"would create {total_jobs} jobs ({len(centers)} cells × "
            f"{len(categories)} categories × {len(source_codes)} sources); "
            f"cap max_jobs={payload.max_jobs}. Raise the cap or use a larger "
            f"cell_size_m / fewer categories / fewer sources.",
        )

    radius = payload.cell_size_m // 2
    from poi_lake.workers.ingest import run_ingestion_job

    job_ids: list[int] = []
    for src_code in source_codes:
        src = sources_by_code[src_code]
        for cat in categories:
            for lat, lng in centers:
                params: dict[str, object] = {"lat": lat, "lng": lng, "radius_m": radius}
                if cat:
                    params["category"] = cat
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
        "tiled-ingest: %d jobs queued sources=%s cells=%d categories=%d radius=%dm",
        len(job_ids), source_codes, len(centers), len(categories), radius,
    )
    return {
        "job_ids": job_ids,
        "count": len(job_ids),
        "cells": len(centers),
        "categories": len(categories),
        "sources": len(source_codes),
        "source_codes": source_codes,
        "admin_codes": admin_codes,
        "cell_radius_m": radius,
        "bbox": bbox_for_response,
    }


# --------------------------------------------------------------- raw_pois
#
# Bronze layer surfaced for admin debugging only — pre-dedupe rows, often
# duplicated across sources / time. Public consumers should hit the
# ``master_pois`` API instead.


@router.get("/raw-pois", response_model=RawPOIList)
async def list_raw_pois(
    source_code: str | None = Query(default=None, description="filter by sources.code"),
    ingestion_job_id: int | None = Query(default=None, ge=1),
    processed: bool | None = Query(
        default=None,
        description="True = already normalized, False = still pending, omit = all",
    ),
    has_location: bool | None = Query(
        default=None, description="True = lat/lng present, False = missing"
    ),
    bbox: str | None = Query(
        default=None,
        description="lng_min,lat_min,lng_max,lat_max (comma-sep)",
    ),
    fetched_since: datetime | None = Query(
        default=None, description="ISO timestamp; rows fetched_at >= this"
    ),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> RawPOIList:
    """List raw_pois with admin filters. Used by the admin UI's *Raw POIs*
    page to verify what an adapter actually pulled in, and to find rows
    stuck in pre-normalize state."""
    clauses: list[str] = []
    bind_params: dict[str, object] = {}

    if source_code:
        # Resolve once via subquery — keeps the main query a single statement.
        clauses.append("r.source_id = (SELECT id FROM sources WHERE code = :sc)")
        bind_params["sc"] = source_code
    if ingestion_job_id is not None:
        clauses.append("r.ingestion_job_id = :jid")
        bind_params["jid"] = ingestion_job_id
    if processed is True:
        clauses.append("r.processed_at IS NOT NULL")
    elif processed is False:
        clauses.append("r.processed_at IS NULL")
    if has_location is True:
        clauses.append("r.location IS NOT NULL")
    elif has_location is False:
        clauses.append("r.location IS NULL")
    if fetched_since is not None:
        clauses.append("r.fetched_at >= :since")
        bind_params["since"] = fetched_since
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            lng_min, lat_min, lng_max, lat_max = parts
        except ValueError:
            raise HTTPException(400, "bbox must be 'lng_min,lat_min,lng_max,lat_max'")
        clauses.append(
            "ST_Within(r.location::geometry, "
            "ST_MakeEnvelope(:bb_lng_min, :bb_lat_min, :bb_lng_max, :bb_lat_max, 4326))"
        )
        bind_params.update({
            "bb_lng_min": lng_min, "bb_lat_min": lat_min,
            "bb_lng_max": lng_max, "bb_lat_max": lat_max,
        })

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    offset = (page - 1) * per_page

    list_sql = text(
        f"""
        SELECT r.id, r.source_id, s.code AS source_code,
               r.source_poi_id, r.raw_payload, r.content_hash,
               ST_Y(r.location::geometry) AS lat,
               ST_X(r.location::geometry) AS lng,
               r.fetched_at, r.ingestion_job_id, r.processed_at
        FROM raw_pois r
        JOIN sources s ON s.id = r.source_id
        {where_sql}
        ORDER BY r.fetched_at DESC, r.id DESC
        LIMIT :lim OFFSET :off
        """
    )
    count_sql = text(
        f"SELECT COUNT(*) FROM raw_pois r JOIN sources s ON s.id = r.source_id {where_sql}"
    )

    rows = (
        await session.execute(list_sql, {**bind_params, "lim": per_page, "off": offset})
    ).all()
    total = (await session.execute(count_sql, bind_params)).scalar_one()

    items = [
        RawPOIOut(
            id=int(r.id),
            source_id=int(r.source_id),
            source_code=r.source_code,
            source_poi_id=r.source_poi_id,
            raw_payload=r.raw_payload or {},
            content_hash=r.content_hash,
            lat=float(r.lat) if r.lat is not None else None,
            lng=float(r.lng) if r.lng is not None else None,
            fetched_at=r.fetched_at,
            ingestion_job_id=r.ingestion_job_id,
            processed_at=r.processed_at,
        )
        for r in rows
    ]
    return RawPOIList(items=items, total=int(total), page=page, per_page=per_page)


@router.get("/raw-pois/{raw_id}", response_model=RawPOIOut)
async def get_raw_poi(
    raw_id: int, session: AsyncSession = Depends(get_session)
) -> RawPOIOut:
    """Single raw POI by id — surfaces the full raw_payload for inspection."""
    row = (
        await session.execute(
            text(
                """
                SELECT r.id, r.source_id, s.code AS source_code,
                       r.source_poi_id, r.raw_payload, r.content_hash,
                       ST_Y(r.location::geometry) AS lat,
                       ST_X(r.location::geometry) AS lng,
                       r.fetched_at, r.ingestion_job_id, r.processed_at
                FROM raw_pois r JOIN sources s ON s.id = r.source_id
                WHERE r.id = :id
                """
            ),
            {"id": raw_id},
        )
    ).first()
    if row is None:
        raise HTTPException(404, f"raw_poi {raw_id} not found")
    return RawPOIOut(
        id=int(row.id),
        source_id=int(row.source_id),
        source_code=row.source_code,
        source_poi_id=row.source_poi_id,
        raw_payload=row.raw_payload or {},
        content_hash=row.content_hash,
        lat=float(row.lat) if row.lat is not None else None,
        lng=float(row.lng) if row.lng is not None else None,
        fetched_at=row.fetched_at,
        ingestion_job_id=row.ingestion_job_id,
        processed_at=row.processed_at,
    )


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
