"""Brand discovery for bizmap's admin ("Thương hiệu mới", "Cấu hình AI").

  GET  /brand-discovery/summary                    counts for the page head
  GET  /brand-discovery/candidates?status=…        pending | added | rejected
  POST /brand-discovery/candidates/{id}/approve    add it (fields as edited)
  POST /brand-discovery/candidates/{id}/reject
  POST /brand-discovery/candidates/{id}/restore    back to pending
  POST /brand-discovery/candidates/{id}/remove     undo an added brand
  POST /brand-discovery/batches/{batch}/undo       undo every brand of a batch
  POST /brand-discovery/run                        a discovery pass, now
  GET  /brand-discovery/settings                   keys reduced to their last 4
  PUT  /brand-discovery/settings                   thresholds, budget, lane
  PUT  /brand-discovery/providers/{p}              key, model, role
  DELETE /brand-discovery/providers/{p}/key
  POST /brand-discovery/providers/{p}/check        a tiny call with the key

All need the ``write:brands`` permission, like ``PUT /brands/{name}``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_permission
from poi_lake.pipeline.normalize.openooh_keywords import BRAND_PREFIX
from poi_lake.services import app_settings, brand_ai, brand_discovery

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/brand-discovery",
    tags=["brand-discovery"],
    dependencies=[Depends(require_permission("write:brands"))],
)

STATUSES = {
    "pending": ("pending",),
    "added": ("added", "auto_added"),
    "rejected": ("rejected", "auto_rejected"),
}

_COLUMNS = (
    "id, key, name, category, aliases, search_term, stores, provinces, score, signals, "
    "samples, ai, status, batch, tagged, sweep_rows, created_at, decided_at"
)


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    for k in ("created_at", "decided_at"):
        d[k] = d[k].isoformat() if d[k] else None
    return d


async def _one(session: AsyncSession, cid: int) -> dict[str, Any]:
    r = (await session.execute(text(f"SELECT {_COLUMNS} FROM brand_candidates WHERE id = :i"), {"i": cid})).first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such candidate")
    return _row(r)


@router.get("/summary")
async def summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    counts = (
        await session.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE status = 'pending') AS pending,
                  count(*) FILTER (WHERE status = 'new') AS awaiting_ai,
                  count(*) FILTER (WHERE status IN ('added', 'auto_added')
                                   AND decided_at > NOW() - INTERVAL '7 days') AS added_7d,
                  count(*) FILTER (WHERE status = 'auto_rejected'
                                   AND updated_at > NOW() - INTERVAL '7 days') AS rejected_7d,
                  max(updated_at) AS last_run
                FROM brand_candidates
                """
            )
        )
    ).one()
    queue = (
        await session.execute(
            text(
                "SELECT count(*) FROM crawl_plan WHERE openooh_code LIKE :like "
                "AND status IN ('pending', 'in_progress')"
            ),
            {"like": BRAND_PREFIX + "%"},
        )
    ).scalar_one()
    from poi_lake.config import get_settings

    rate = get_settings().crawl_rate_per_hour
    share = await app_settings.brand_share(session)
    return {
        "pending": counts.pending,
        "awaiting_ai": counts.awaiting_ai,
        "added_7d": counts.added_7d,
        "rejected_7d": counts.rejected_7d,
        "last_run": counts.last_run.isoformat() if counts.last_run else None,
        "brand_queue_rows": int(queue),
        "brand_share": share,
        "rate_per_hour": rate,
        "brand_per_hour": round(rate * share),
        "ai_spent_usd": round(await brand_ai.spent_this_month(session), 4),
    }


@router.get("/candidates")
async def candidates(
    status_: Literal["pending", "added", "rejected"] = Query("pending", alias="status"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    order = "decided_at DESC NULLS LAST, id DESC" if status_ != "pending" else "score DESC, stores DESC"
    rows = (
        await session.execute(
            text(f"SELECT {_COLUMNS} FROM brand_candidates WHERE status = ANY(:s) ORDER BY {order} LIMIT :n"),
            {"s": list(STATUSES[status_]), "n": limit},
        )
    ).all()
    return {"items": [_row(r) for r in rows]}


class Approve(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    aliases: list[str] | None = Field(default=None, max_length=10)
    search_term: str | None = Field(default=None, min_length=2, max_length=40)
    category: str | None = Field(default=None, max_length=50)


@router.post("/candidates/{cid}/approve")
async def approve(cid: int, body: Approve, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    cand = await _one(session, cid)
    if cand["status"] in ("added", "auto_added"):
        raise HTTPException(status.HTTP_409_CONFLICT, "already added")
    name = " ".join((body.name or cand["name"]).split())
    if name.lower() != cand["name"].lower() and await brand_discovery._name_taken(session, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is already a brand")
    aliases = [" ".join(a.split())[:100] for a in (body.aliases or []) if a.strip()] if body.aliases is not None else None
    batch = "manual-" + str(cid)
    result = await brand_discovery.apply(
        session, cid, batch=batch, status="added", name=name, aliases=aliases,
        term=body.search_term, category=body.category,
    )
    await session.commit()
    return result


async def _set_status(session: AsyncSession, cid: int, new: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    cand = await _one(session, cid)
    if cand["status"] not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"candidate is {cand['status']}")
    await session.execute(
        text("UPDATE brand_candidates SET status = :s, decided_at = NOW(), updated_at = NOW() WHERE id = :i"),
        {"s": new, "i": cid},
    )
    await session.commit()
    return {"id": cid, "status": new}


@router.post("/candidates/{cid}/reject")
async def reject(cid: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _set_status(session, cid, "rejected", ("pending",))


@router.post("/candidates/{cid}/restore")
async def restore(cid: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await _set_status(session, cid, "pending", ("rejected", "auto_rejected", "removed"))


@router.post("/candidates/{cid}/remove")
async def remove(cid: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _one(session, cid)
    result = await brand_discovery.remove(session, cid)
    await session.commit()
    return result


@router.post("/batches/{batch}/undo")
async def undo(batch: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await brand_discovery.undo_batch(session, batch)
    await session.commit()
    return result


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now() -> dict[str, str]:
    from poi_lake.workers.brand_discovery import discover_brands

    discover_brands.send()
    return {"queued": "discover_brands"}


@router.get("/settings")
async def get_settings_(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return app_settings.public(await app_settings.discovery(session))


class SettingsIn(BaseModel):
    budget_usd: float | None = Field(default=None, ge=0, le=1000)
    auto_add: bool | None = None
    add_at: int | None = Field(default=None, ge=1, le=100)
    reject_below: int | None = Field(default=None, ge=0, le=100)
    min_stores: int | None = Field(default=None, ge=1, le=10000)
    min_provinces: int | None = Field(default=None, ge=1, le=63)
    brand_share: float | None = Field(default=None, ge=0, le=1)
    rescan_days: int | None = Field(default=None, ge=0, le=3650)


@router.put("/settings")
async def put_settings(body: SettingsIn, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    stored = await app_settings.read(session, app_settings.DISCOVERY)
    stored.update(body.model_dump(exclude_none=True))
    merged = {**app_settings.DEFAULTS, **stored}
    if merged["reject_below"] >= merged["add_at"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "reject_below must be below add_at")
    await app_settings.write(session, app_settings.DISCOVERY, stored)
    await session.commit()
    return app_settings.public(await app_settings.discovery(session))


class ProviderIn(BaseModel):
    key: str | None = Field(default=None, min_length=8, max_length=400)
    model: str | None = Field(default=None, max_length=64)
    role: Literal["primary", "fallback", "off"] | None = None


def _provider(p: str) -> str:
    if p not in app_settings.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "provider is claude or openai")
    return p


async def _update_provider(session: AsyncSession, p: str, change: dict[str, Any]) -> dict[str, Any]:
    stored = await app_settings.read(session, app_settings.DISCOVERY)
    providers = stored.setdefault("providers", {})
    cfg = providers.setdefault(p, {})
    cfg.update(change)
    if change.get("role") == "primary":  # one primary: the other steps down to fallback
        for other, ocfg in providers.items():
            if other != p and ocfg.get("role") == "primary":
                ocfg["role"] = "fallback"
    await app_settings.write(session, app_settings.DISCOVERY, stored)
    await session.commit()
    return app_settings.public(await app_settings.discovery(session))


@router.put("/providers/{p}")
async def put_provider(p: str, body: ProviderIn, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    p = _provider(p)
    change: dict[str, Any] = body.model_dump(exclude_none=True, exclude={"key"})
    if body.key:
        key = body.key.strip()
        change.update({"key": app_settings.seal(key), "key_last4": key[-4:], "checked": None})
    return await _update_provider(session, p, change)


@router.delete("/providers/{p}/key")
async def delete_key(p: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    p = _provider(p)
    return await _update_provider(session, p, {"key": None, "key_last4": None, "checked": None})


@router.post("/providers/{p}/check")
async def check_provider(p: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    p = _provider(p)
    result = await brand_ai.check(session, p)
    await _update_provider(session, p, {"checked": "ok" if result["ok"] else "failed"})
    return result
