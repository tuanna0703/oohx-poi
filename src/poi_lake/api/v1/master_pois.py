"""Master POI consumer endpoints (Phase 5).

Auth: ``X-API-Key`` resolved against ``api_clients`` with ``read:master``
permission.

Indexes that back the queries (created in the Phase 1 migration):
  * ``idx_master_location``  — GIST on ``location`` for ST_DWithin
  * ``idx_master_active``    — partial on (status, updated_at) for default sort
  * ``idx_master_brand``     — partial WHERE brand IS NOT NULL
  * ``idx_master_category``  — btree
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_permission
from poi_lake.db.models import MasterPOI, MasterPOIHistory
from poi_lake.schemas import (
    BrandSummary,
    HistoryEntryOut,
    MasterPOIList,
    MasterPOIOut,
    SearchRequest,
    SourceRefOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/master-pois", tags=["master-pois"])


# Single SELECT shape used by list / search / get_one — pulls lat/lng out of
# the geography column server-side so the response model matches MasterPOIOut.
_BASE_SELECT_SQL = """
SELECT
  id, canonical_name, canonical_address, canonical_address_components,
  canonical_phone, canonical_website,
  ST_Y(location::geometry) AS lat,
  ST_X(location::geometry) AS lng,
  openooh_category, openooh_subcategory, brand,
  province_code, district_code, ward_code,
  sources_count, confidence, quality_score, dooh_score,
  status, version, created_at, updated_at
FROM master_pois
"""


def _row_to_out(row: Any) -> MasterPOIOut:
    return MasterPOIOut(
        id=row.id,
        canonical_name=row.canonical_name,
        canonical_address=row.canonical_address,
        canonical_address_components=row.canonical_address_components,
        canonical_phone=row.canonical_phone,
        canonical_website=row.canonical_website,
        lat=float(row.lat),
        lng=float(row.lng),
        openooh_category=row.openooh_category,
        openooh_subcategory=row.openooh_subcategory,
        brand=row.brand,
        province_code=row.province_code,
        district_code=row.district_code,
        ward_code=row.ward_code,
        sources_count=int(row.sources_count),
        confidence=float(row.confidence),
        quality_score=float(row.quality_score) if row.quality_score is not None else None,
        dooh_score=float(row.dooh_score) if row.dooh_score is not None else None,
        status=row.status,
        version=int(row.version),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "",
    response_model=MasterPOIList,
    dependencies=[Depends(require_permission("read:master"))],
)
async def list_master_pois(
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    radius_m: int | None = Query(default=None, ge=1, le=200_000),
    category: str | None = Query(default=None),
    brand: str | None = Query(default=None),
    province_code: str | None = Query(default=None, max_length=20),
    district_code: str | None = Query(default=None, max_length=20),
    ward_code: str | None = Query(default=None, max_length=20),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> MasterPOIList:
    """List master POIs with optional radius + category + brand filters.

    Either none of ``lat/lng/radius_m`` or all three are required. Active
    records only (``status = 'active'``).
    """
    has_radius = any(v is not None for v in (lat, lng, radius_m))
    if has_radius and not all(v is not None for v in (lat, lng, radius_m)):
        raise HTTPException(400, "lat, lng, and radius_m must all be provided together")

    clauses = ["status = 'active'", "confidence >= :min_conf"]
    params: dict[str, Any] = {"min_conf": min_confidence}
    if has_radius:
        clauses.append(
            "ST_DWithin(location, ST_GeogFromText(:point), :radius)"
        )
        params["point"] = f"SRID=4326;POINT({lng} {lat})"
        params["radius"] = radius_m
    if category:
        clauses.append("(openooh_category = :cat OR openooh_subcategory = :cat)")
        params["cat"] = category
    if brand:
        clauses.append("brand = :brand")
        params["brand"] = brand
    if province_code:
        clauses.append("province_code = :prov")
        params["prov"] = province_code
    if district_code:
        clauses.append("district_code = :dist")
        params["dist"] = district_code
    if ward_code:
        clauses.append("ward_code = :ward")
        params["ward"] = ward_code

    where = " AND ".join(clauses)
    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    sql = text(
        _BASE_SELECT_SQL
        + f" WHERE {where} ORDER BY confidence DESC, updated_at DESC "
        + " LIMIT :limit OFFSET :offset"
    )
    rows = (await session.execute(sql, params)).all()

    count_sql = text(f"SELECT COUNT(*) FROM master_pois WHERE {where}")
    total = (await session.execute(count_sql, params)).scalar_one()

    return MasterPOIList(
        items=[_row_to_out(r) for r in rows],
        total=int(total),
        page=page,
        per_page=per_page,
    )


@router.get(
    "/brands",
    response_model=list[BrandSummary],
    dependencies=[Depends(require_permission("read:master"))],
)
async def list_brands(
    province_code: str | None = Query(default=None, max_length=20),
    district_code: str | None = Query(default=None, max_length=20),
    category: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[BrandSummary]:
    """Group active master POIs by ``brand`` and return counts.

    Optional admin / category filters scope the aggregation. Brands with
    only one master are still returned — the ``count = 1`` case is the
    "long tail" that the AdTRUE inventory tool wants to know about.
    """
    clauses = ["status = 'active'", "brand IS NOT NULL", "confidence >= :mc"]
    params: dict[str, Any] = {"mc": min_confidence, "lim": limit}
    if province_code:
        clauses.append("province_code = :prov")
        params["prov"] = province_code
    if district_code:
        clauses.append("district_code = :dist")
        params["dist"] = district_code
    if category:
        clauses.append("(openooh_category = :cat OR openooh_subcategory = :cat)")
        params["cat"] = category
    where = " AND ".join(clauses)

    sql = text(
        f"""
        SELECT
            brand,
            COUNT(*) AS n,
            (ARRAY_AGG(openooh_subcategory ORDER BY openooh_subcategory)
                FILTER (WHERE openooh_subcategory IS NOT NULL))[1] AS category,
            (ARRAY_AGG(id::text ORDER BY confidence DESC))[1:3]   AS sample_ids
        FROM master_pois
        WHERE {where}
        GROUP BY brand
        ORDER BY n DESC, brand ASC
        LIMIT :lim
        """
    )
    rows = (await session.execute(sql, params)).all()
    return [
        BrandSummary(
            brand=r.brand,
            count=int(r.n),
            category=r.category,
            sample_master_ids=list(r.sample_ids) if r.sample_ids else [],
        )
        for r in rows
    ]


@router.get(
    "/{master_id}",
    response_model=MasterPOIOut,
    dependencies=[Depends(require_permission("read:master"))],
)
async def get_master_poi(
    master_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> MasterPOIOut:
    sql = text(_BASE_SELECT_SQL + " WHERE id = :id")
    row = (await session.execute(sql, {"id": master_id})).first()
    if row is None:
        raise HTTPException(404, f"master_poi {master_id} not found")
    return _row_to_out(row)


@router.get(
    "/{master_id}/sources",
    response_model=list[SourceRefOut],
    dependencies=[Depends(require_permission("read:master"))],
)
async def get_master_poi_sources(
    master_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[SourceRefOut]:
    """Return the lineage — which raw_pois were merged into this master."""
    master = await session.get(MasterPOI, master_id)
    if master is None:
        raise HTTPException(404, f"master_poi {master_id} not found")
    refs = master.source_refs or []
    return [
        SourceRefOut(
            source=str(r.get("source", "")),
            source_poi_id=str(r.get("source_poi_id", "")),
            raw_poi_id=int(r.get("raw_poi_id", 0)),
        )
        for r in refs
        if isinstance(r, dict)
    ]


@router.get(
    "/{master_id}/history",
    response_model=list[HistoryEntryOut],
    dependencies=[Depends(require_permission("read:master"))],
)
async def get_master_poi_history(
    master_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[HistoryEntryOut]:
    """Audit log for a master POI — creation + each subsequent change."""
    rows = (
        await session.execute(
            select(MasterPOIHistory)
            .where(MasterPOIHistory.master_poi_id == master_id)
            .order_by(MasterPOIHistory.version.asc(), MasterPOIHistory.changed_at.asc())
        )
    ).scalars().all()
    if not rows:
        # Empty history is a legitimate response, but let's confirm the master
        # exists so 404 still fires for a bad UUID.
        master = await session.get(MasterPOI, master_id)
        if master is None:
            raise HTTPException(404, f"master_poi {master_id} not found")
    return [HistoryEntryOut.model_validate(r) for r in rows]


@router.post(
    "/search",
    response_model=MasterPOIList,
    dependencies=[Depends(require_permission("read:master"))],
)
async def search_master_pois(
    body: SearchRequest,
    session: AsyncSession = Depends(get_session),
) -> MasterPOIList:
    """Fuzzy text + bbox + category/brand filter.

    The text match uses pg_trgm-style ``ILIKE`` with broken-up tokens
    (cheap, no extension required). For Phase 7 we'd swap in either:
      * pg_trgm + a similarity threshold; or
      * pgvector cosine on canonical_name_embedding.
    """
    clauses = ["status = 'active'", "confidence >= :min_conf"]
    params: dict[str, Any] = {"min_conf": body.min_confidence}

    if body.query:
        # Match every space-separated token as ILIKE substring on name OR address.
        tokens = [t for t in body.query.split() if t]
        for i, tok in enumerate(tokens[:5]):  # cap at 5 tokens
            clauses.append(
                f"(canonical_name ILIKE :q{i} OR canonical_address ILIKE :q{i})"
            )
            params[f"q{i}"] = f"%{tok}%"

    if body.bbox:
        lng_min, lat_min, lng_max, lat_max = body.bbox
        clauses.append(
            "ST_Within(location::geometry, "
            "ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326))"
        )
        params.update(
            {"lng_min": lng_min, "lat_min": lat_min, "lng_max": lng_max, "lat_max": lat_max}
        )

    if body.category:
        clauses.append("(openooh_category = :cat OR openooh_subcategory = :cat)")
        params["cat"] = body.category
    if body.brand:
        clauses.append("brand = :brand")
        params["brand"] = body.brand
    if body.province_code:
        clauses.append("province_code = :prov")
        params["prov"] = body.province_code
    if body.district_code:
        clauses.append("district_code = :dist")
        params["dist"] = body.district_code
    if body.ward_code:
        clauses.append("ward_code = :ward")
        params["ward"] = body.ward_code

    where = " AND ".join(clauses)
    offset = (body.page - 1) * body.per_page
    params["limit"] = body.per_page
    params["offset"] = offset

    sql = text(
        _BASE_SELECT_SQL
        + f" WHERE {where} ORDER BY confidence DESC, updated_at DESC "
        + " LIMIT :limit OFFSET :offset"
    )
    rows = (await session.execute(sql, params)).all()

    count_sql = text(f"SELECT COUNT(*) FROM master_pois WHERE {where}")
    total = (await session.execute(count_sql, params)).scalar_one()

    return MasterPOIList(
        items=[_row_to_out(r) for r in rows],
        total=int(total),
        page=body.page,
        per_page=body.per_page,
    )
