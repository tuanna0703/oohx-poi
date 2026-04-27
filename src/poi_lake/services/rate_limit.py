"""Per-api-client rate limiting backed by Redis.

Each ``APIClient.rate_limit_per_minute`` is enforced with a fixed-window
counter keyed by ``rl:{client_id}:{epoch_minute}``. Counters expire after
the window passes, so we never accumulate state.

Why a fixed window and not a sliding one? Fixed windows take 1 INCR + 1
EXPIRE per request — cheap, lock-free, and the worst-case overshoot (2x
the limit at the window boundary) is fine for our use case (oohx
consumers, not adversarial public traffic).
"""

from __future__ import annotations

import logging
import time

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status

from poi_lake.api.deps import require_api_client
from poi_lake.config import get_settings
from poi_lake.db.models import APIClient

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return _redis


async def enforce_rate_limit(
    request: Request,
    client: APIClient = Depends(require_api_client),
) -> APIClient:
    """Increment the per-minute counter and reject when ``rate_limit_per_minute``
    is exceeded. Returns the resolved client so downstream deps can chain off
    of this instead of ``require_api_client``.

    Fail-open on Redis errors — we'd rather serve a few extra requests than
    take the API down because Redis hiccupped.
    """
    limit = client.rate_limit_per_minute or 0
    if limit <= 0:
        return client  # 0 = unlimited

    minute = int(time.time() // 60)
    key = f"rl:{client.id}:{minute}"
    try:
        r = _get_redis()
        async with r.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, 65)  # a bit longer than the window
            count, _ = await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limit: redis unavailable, fail-open: %s", exc)
        return client

    if count > limit:
        # Stamp Retry-After in the response — consumers should respect it.
        retry_after = 60 - int(time.time() % 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit {limit}/min exceeded for client {client.name!r}",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str((minute + 1) * 60),
            },
        )

    # Forward usage headers so consumers can self-throttle.
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, limit - count)),
        "X-RateLimit-Reset": str((minute + 1) * 60),
    }
    return client


def require_permission_with_rate_limit(name: str):
    """Drop-in replacement for ``api.deps.require_permission`` that also
    enforces the rate limit. Use this in the consumer-facing routers."""

    async def _dep(client: APIClient = Depends(enforce_rate_limit)) -> APIClient:
        perms = client.permissions or []
        if name in perms or "*" in perms:
            return client
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=f"missing permission {name!r}"
        )

    return _dep
