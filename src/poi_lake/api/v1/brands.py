"""Chain management for consumers (bizmap.live admin).

  PUT /api/v1/brands/{name}           add or update a chain: dictionary, re-tag, sweep
  GET /api/v1/brands/{name}/progress  sweep rows done, places found, stores tagged

Both need the ``write:brands`` permission on the API client.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_permission
from poi_lake.services.brands import add_brand_sweeps, brand_progress, retag, upsert_brand

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brands", tags=["brands"])

_perm = Depends(require_permission("write:brands"))


class BrandUpsert(BaseModel):
    aliases: list[str] = Field(
        default_factory=list,
        description='Other spellings matched in place names, e.g. "Kingfoodmart".',
    )
    search_terms: list[str] = Field(
        default_factory=list,
        description="What gosom searches, district by district. Defaults to the name.",
    )
    category: str | None = Field(default=None, max_length=50, description="OpenOOH level-1 code")
    province_codes: list[str] | None = Field(default=None, description="If null, every province.")
    sweep: bool = True
    priority: int = Field(default=5, description="5 runs with the other brand sweeps.")


def _clean(values: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for v in values:
        v = " ".join(v.split())
        if v and v not in out:
            out.append(v[:limit])
    return out


@router.put("/{name}", status_code=status.HTTP_200_OK)
async def put_brand(
    name: str,
    payload: BrandUpsert,
    session: AsyncSession = Depends(get_session),
    _client: Any = _perm,
) -> dict[str, Any]:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name must be 2–100 characters")
    aliases = _clean(payload.aliases, 100)
    terms = _clean(payload.search_terms, 40) or [name[:40]]
    await upsert_brand(session, name, aliases, payload.category)
    tagged = await retag(session, name)
    sweep: dict[str, int] = {}
    if payload.sweep:
        try:
            sweep = await add_brand_sweeps(session, terms, payload.province_codes, payload.priority)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    await session.commit()
    logger.info("brand %r upserted: tagged=%s sweep=%s", name, tagged, sweep)
    return {"brand": name, "aliases": aliases, "search_terms": terms, "tagged": tagged, "sweep": sweep}


@router.get("/{name}/progress")
async def get_progress(
    name: str,
    search_terms: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_session),
    _client: Any = _perm,
) -> dict[str, Any]:
    name = " ".join(name.split())
    return await brand_progress(session, name, _clean(search_terms, 40) or [name[:40]])
