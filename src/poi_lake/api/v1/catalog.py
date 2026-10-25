"""Chain catalog endpoints.

  GET  /api/v1/master-pois/{id}/products   what the chain store at this master lists
                                           online (read:master)
  GET  /api/v1/admin/catalog/winmart       store list, matching and crawl progress
  POST /api/v1/admin/catalog/winmart/refresh   sweep stores now, then crawl products
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.catalog.winmart import CHAIN
from poi_lake.services import catalog
from poi_lake.services.rate_limit import (
    require_permission_with_rate_limit as require_permission,
)

router = APIRouter(tags=["catalog"])
admin_router = APIRouter(
    prefix="/admin/catalog", tags=["admin"], dependencies=[Depends(require_admin)]
)


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


@admin_router.get("/winmart")
async def winmart_summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    from poi_lake.workers.catalog import WINMART_BRANDS

    return await catalog.summary(session, CHAIN, WINMART_BRANDS)


@admin_router.post("/winmart/refresh", status_code=202)
async def winmart_refresh(crawl_items: bool = Query(default=True)) -> dict[str, Any]:
    from poi_lake.workers.catalog import refresh_winmart

    refresh_winmart.send(crawl_items)
    return {"queued": True, "crawl_items": crawl_items}
