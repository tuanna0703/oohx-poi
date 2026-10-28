"""Daily reports.

  GET  /api/v1/reports/daily?days=30            the latest reports, newest first (read:master)
  GET  /api/v1/reports/growth?days=7            what the lake gained in the last days (read:master)
  POST /api/v1/admin/reports/daily?day=YYYY-MM-DD   build one now (admin)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.api.deps import get_session, require_admin
from poi_lake.services import reports
from poi_lake.services.rate_limit import (
    require_permission_with_rate_limit as require_permission,
)

router = APIRouter(tags=["reports"])
admin_router = APIRouter(prefix="/admin/reports", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/reports/daily", dependencies=[Depends(require_permission("read:master"))])
async def daily_reports(
    days: int = Query(default=30, ge=1, le=366),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {"reports": await reports.recent_reports(session, days)}


@router.get("/reports/growth", dependencies=[Depends(require_permission("read:master"))])
async def growth(
    days: int = Query(default=7, ge=1, le=366),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await reports.growth_report(session, days)


@admin_router.post("/daily")
async def build_daily(
    day: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Default: yesterday in Vietnam."""
    from datetime import timedelta

    d = day or (datetime.now(reports.VN_TZ).date() - timedelta(days=1))
    return {"day": d.isoformat(), "metrics": await reports.build_daily_report(session, d)}
