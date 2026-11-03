"""Chain management for consumers (bizmap.live admin).

  PUT /api/v1/brands/{name}           add or update a chain: dictionary, re-tag, sweep
  GET /api/v1/brands/{name}/progress  sweep rows done, places found, stores tagged
  GET /api/v1/brands/quality          is each brand a chain, its industry, its twins
  GET /api/v1/brands/quality/plan     dry run: what a clean-up would untag
  POST /api/v1/brands/quality/apply   untag stores not carrying their brand (logged)
  POST /api/v1/brands/quality/undo/{run_id}
  GET|PUT /api/v1/brands/quality/settings   {"apply": bool}: clean up daily

The quality list needs ``read:master``; everything else ``write:brands``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_permission
from poi_lake.services import app_settings, brand_cleanup, brand_quality
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


@router.get("/quality", dependencies=[Depends(require_permission("read:master"))])
async def list_quality(
    verdict: str | None = Query(default=None, pattern="^(keep|hide)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Every assessed brand, most stores first (``services/brand_quality.py``).

    Recorded only: a "hide" brand is still tagged on its stores until an
    admin acts on it. ``industry`` keys are bizmap's industries.
    """
    where = "WHERE verdict = :v" if verdict else ""
    rows = (await session.execute(
        text(
            f"""
            SELECT brand, display_name, verdict, industry, confidence, name_share, vote,
                   lake_code, reason, merge_into, stores, matching_stores, computed_at
            FROM brand_quality {where}
            ORDER BY stores DESC, brand
            """
        ),
        {"v": verdict} if verdict else {},
    )).mappings().all()
    items = [{**r, "computed_at": r["computed_at"].isoformat()} for r in rows]
    return {"items": items, "total": len(items)}


@router.get("/quality/plan")
async def quality_plan(session: AsyncSession = Depends(get_session), _client: Any = _perm) -> dict[str, Any]:
    """What ``apply`` would change, brand by brand. Writes nothing."""
    return await brand_cleanup.plan(session)


@router.post("/quality/apply")
async def quality_apply(session: AsyncSession = Depends(get_session), _client: Any = _perm) -> dict[str, Any]:
    result = await brand_cleanup.apply(session)
    await session.commit()
    return result


@router.post("/quality/undo/{run_id}")
async def quality_undo(
    run_id: str, session: AsyncSession = Depends(get_session), _client: Any = _perm
) -> dict[str, int]:
    result = await brand_cleanup.undo(session, run_id)
    await session.commit()
    return result


class CleanupSettings(BaseModel):
    apply: bool


@router.get("/quality/settings")
async def get_cleanup_settings(
    session: AsyncSession = Depends(get_session), _client: Any = _perm
) -> dict[str, Any]:
    return await brand_cleanup.settings(session)


@router.put("/quality/settings")
async def put_cleanup_settings(
    payload: CleanupSettings, session: AsyncSession = Depends(get_session), _client: Any = _perm
) -> dict[str, Any]:
    value = {**(await brand_cleanup.settings(session)), "apply": payload.apply}
    await app_settings.write(session, brand_cleanup.SETTINGS_KEY, value)
    await session.commit()
    return value


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
    await brand_quality.run(session, [name])
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
