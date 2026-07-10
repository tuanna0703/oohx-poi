"""Persistent circuit breaker for the LLM resolver.

When Anthropic rejects us for a reason retrying cannot fix — an exhausted
credit balance, a revoked key — the dedupe pass must **stop**, not carry on
without the resolver.

That distinction matters more than it looks. A ``NEEDS_LLM`` pair skipped by a
resolver-less pass is not deferred: ``_make_master`` writes each row to its own
master and marks it ``merged``, and the clusterer only ever looks at
``merge_status = 'pending'`` rows. Nothing revisits it. So "degrade and keep
going" silently manufactures duplicate masters, permanently.

The flag lives in Redis rather than the process, so every worker sees it and it
survives a restart. An operator clears it from the admin UI once the account is
funded again.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis_asyncio

from poi_lake.config import get_settings

logger = logging.getLogger(__name__)

LLM_DISABLED_KEY = "poi-lake:llm:disabled"


class LLMUnavailableError(RuntimeError):
    """Raised when the resolver fails for a reason retrying cannot fix."""


def is_fatal_llm_error(exc: BaseException) -> bool:
    """True when the API rejected us, not the request.

    Rate limits, timeouts, overloads and 5xx are transient — the next pass
    should try again. An empty wallet or a dead key is not.
    """
    try:
        import anthropic
    except ImportError:  # pragma: no cover - anthropic is a hard dependency
        return False

    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return True
    if isinstance(exc, anthropic.BadRequestError):
        # 400 is normally *our* bug, except for the billing case, which the API
        # reports as `invalid_request_error` with this message.
        blob = str(exc).lower()
        return "credit balance" in blob or "billing" in blob
    return False


async def _client(redis_client: Any | None = None) -> Any:
    if redis_client is not None:
        return redis_client
    return redis_asyncio.from_url(
        get_settings().redis_url, encoding="utf-8", decode_responses=True
    )


async def get_disabled(redis_client: Any | None = None) -> dict[str, str] | None:
    """Return ``{"reason": ..., "since": ...}`` when paused, else None."""
    rc = await _client(redis_client)
    raw = await rc.get(LLM_DISABLED_KEY)
    if not raw:
        return None
    try:
        state: dict[str, str] = json.loads(raw)
    except (ValueError, TypeError):
        # Never let a corrupt value wedge the pipeline shut with no explanation.
        return {"reason": str(raw), "since": "unknown"}
    return state


async def disable(reason: str, redis_client: Any | None = None) -> dict[str, str]:
    """Latch the breaker open. No TTL — only an operator clears it."""
    rc = await _client(redis_client)
    state = {
        "reason": reason[:500],
        "since": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    await rc.set(LLM_DISABLED_KEY, json.dumps(state))
    logger.warning("LLM resolver PAUSED — dedupe will not run until resumed: %s", reason)
    return state


async def enable(redis_client: Any | None = None) -> bool:
    """Clear the breaker. Returns True if it had been set."""
    rc = await _client(redis_client)
    removed = await rc.delete(LLM_DISABLED_KEY)
    if removed:
        logger.warning("LLM resolver RESUMED by operator")
    return bool(removed)
