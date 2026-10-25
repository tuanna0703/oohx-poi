"""Chain catalog endpoints.

  GET  /api/v1/master-pois/{id}/products       what the chain store at this master
                                               lists online (read:master)
  GET  /api/v1/master-pois/{id}/chain-stores   what each chain's own store list says
                                               about this master: hours, phone (read:master)
  GET  /api/v1/admin/catalog/{chain}           store list, matching and crawl progress
  POST /api/v1/admin/catalog/{chain}/refresh   read the chain's store list now
  GET  /api/v1/admin/catalog/{chain}/missing   stores the chain lists that the lake lacks
  POST /api/v1/admin/catalog/{chain}/add-missing   send those through ingest → dedupe
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.catalog.chains import CHAINS, Chain
from poi_lake.db.models import IngestionJob, IngestionJobStatus, Source
from poi_lake.services import catalog
from poi_lake.services.rate_limit import (
    require_permission_with_rate_limit as require_permission,
)

router = APIRouter(tags=["catalog"])
admin_router = APIRouter(
    prefix="/admin/catalog", tags=["admin"], dependencies=[Depends(require_admin)]
)


def _chain(chain: str) -> Chain:
    found = CHAINS.get(chain)
    if found is None:
        raise HTTPException(404, f"unknown chain {chain!r}; known: {', '.join(CHAINS)}")
    return found


@router.get(
    "/master-pois/{master_id}/products",
    dependencies=[Depends(require_permission("read:master"))],
)
async def master_products(
    master_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=100),
    menu: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1, le=1000),
    per_page: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """``store`` is null when no chain store is linked to this master;
    ``store.crawled_at`` is null until its products have been read."""
    return await catalog.master_products(
        session, master_id, q=q, menu=menu, page=page, per_page=per_page
    )


@router.get(
    "/master-pois/{master_id}/chain-stores",
    dependencies=[Depends(require_permission("read:master"))],
)
async def master_chain_stores(
    master_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return {"stores": await catalog.chain_stores_at(session, master_id)}


@admin_router.get("/{chain}")
async def chain_summary(chain: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    c = _chain(chain)
    return await catalog.summary(session, c.code, c.brands)


@admin_router.post("/{chain}/refresh", status_code=202)
async def chain_refresh(chain: str, crawl_items: bool = Query(default=True)) -> dict[str, Any]:
    c = _chain(chain)
    from poi_lake.workers.catalog import refresh_bachhoaxanh, refresh_winmart

    if c.code == "winmart":
        refresh_winmart.send(crawl_items)
    else:
        refresh_bachhoaxanh.send()
    return {"queued": True, "chain": c.code, "crawl_items": crawl_items and c.products}


@admin_router.get("/{chain}/missing")
async def chain_missing(
    chain: str,
    min_m: float = Query(default=catalog.MISSING_MIN_M, ge=150, le=5000),
    limit: int = Query(default=50, ge=0, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Stores the chain lists with no master of its brand within ``min_m``."""
    c = _chain(chain)
    rows = await catalog.missing_stores(session, c.code, c.brands, min_m=min_m)
    by_region: dict[str, int] = {}
    for r in rows:
        by_region[r["region_code"]] = by_region.get(r["region_code"], 0) + 1
    return {
        "chain": c.code,
        "min_m": min_m,
        "count": len(rows),
        "by_region": dict(sorted(by_region.items(), key=lambda kv: -kv[1])),
        "stores": rows[:limit],
    }


@admin_router.post("/{chain}/add-missing", status_code=202)
async def chain_add_missing(
    chain: str,
    min_m: float = Query(default=catalog.MISSING_MIN_M, ge=150, le=5000),
    max_stores: int = Query(default=500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One ``chain_websites`` ingestion job for up to ``max_stores`` missing
    stores; normalize and dedupe then take each like any crawled place."""
    from poi_lake.workers.ingest import run_ingestion_job

    c = _chain(chain)
    src = (
        await session.execute(select(Source).where(Source.code == "chain_websites"))
    ).scalar_one_or_none()
    if src is None or not src.enabled:
        raise HTTPException(409, "source 'chain_websites' is missing or disabled")
    rows = await catalog.missing_stores(session, c.code, c.brands, min_m=min_m)
    ids = [f"{c.code}:{r['store_code']}" for r in rows[:max_stores]]
    if not ids:
        return {"chain": c.code, "queued": 0, "missing": 0}
    job = IngestionJob(
        source_id=src.id,
        job_type="detail_enrich",
        params={"source_poi_ids": ids, "chain": c.code, "min_m": min_m},
        status=IngestionJobStatus.PENDING.value,
        stats={},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    run_ingestion_job.send(job.id)
    return {"chain": c.code, "queued": len(ids), "missing": len(rows), "job_id": job.id}
