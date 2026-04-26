"""FastAPI dependencies — DB session, admin auth, api-client auth.

  * ``require_admin``      — internal endpoints (X-Admin-Token == APP_SECRET_KEY)
  * ``require_api_client`` — consumer endpoints (X-API-Key in api_clients)
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.config import get_settings
from poi_lake.db import get_sessionmaker
from poi_lake.db.models import APIClient
from poi_lake.services.api_keys import hash_api_key


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


async def require_api_client(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> APIClient:
    """Resolve X-API-Key → enabled APIClient row.

    Raises 401 on missing/unknown key, 403 on disabled client. The api_keys
    table stores SHA-256(key) only, so the hash lookup is constant time
    against the unique index ``api_clients.api_key_hash``.
    """
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing X-API-Key")
    digest = hash_api_key(x_api_key)
    client = (
        await session.execute(
            select(APIClient).where(APIClient.api_key_hash == digest)
        )
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid X-API-Key")
    if not client.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="api client disabled")
    return client


def require_permission(name: str):
    """Dependency factory: require that the resolved client has ``name`` in
    its ``permissions`` array (or the wildcard ``*``).
    """

    async def _dep(client: APIClient = Depends(require_api_client)) -> APIClient:
        perms = client.permissions or []
        if name in perms or "*" in perms:
            return client
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"missing permission {name!r}",
        )

    return _dep
