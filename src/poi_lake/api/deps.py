"""FastAPI dependencies — DB session and simple admin auth.

Phase 2 has placeholder admin auth (compare X-Admin-Token to APP_SECRET_KEY).
Phase 5 will replace this with the api_clients table + permissions check.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.config import get_settings
from poi_lake.db import get_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    expected = get_settings().app_secret_key.get_secret_value()
    if x_admin_token is None or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid X-Admin-Token",
        )
