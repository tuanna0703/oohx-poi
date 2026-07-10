"""Dedupe worker — runs MergeService over a bounded slice of pending rows.

A pass stops at ``DEDUPE_MAX_CLUSTERS_PER_PASS`` clusters or
``DEDUPE_MAX_SECONDS``, whichever comes first, and commits every
``DEDUPE_COMMIT_EVERY`` clusters — so it finishes inside the actor's 20-minute
``time_limit``, and keeps what it committed if it doesn't. The wall-clock bound
is the one that matters: 200 clusters take under a minute with the LLM resolver
off and over seventeen with it on. Only one pass runs at a time (Redis lock).

If Anthropic rejects us fatally (no credit, dead key) the pass **stops** and
latches a Redis breaker; it stays paused until an operator resumes it from the
admin UI. See ``pipeline/dedupe/llm_state.py`` for why degrading is not an option.

Triggered:
  * on demand via ``run_dedupe.send()`` (e.g. admin endpoint, post-batch);
  * on a periodic schedule (``DEDUPE_SCHEDULE_MINUTES``, default 15) by a
    tiny in-process timer thread that ``send()``s a tick message. Phase 7
    will swap that for a proper APScheduler / cron container.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import dramatiq

from poi_lake.db import session_scope
from poi_lake.pipeline.dedupe import LLMResolver, MergeService
from poi_lake.pipeline.dedupe.llm_state import (
    LLMUnavailableError,
    disable,
    get_disabled,
)

logger = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="dedupe",
    max_retries=2,
    min_backoff=30_000,
    max_backoff=900_000,
    time_limit=20 * 60_000,
)
def run_dedupe() -> None:
    """One dedupe pass, guarded so only one runs at a time.

    The worker runs ``--processes 2``, and a retry can overlap a scheduled
    tick. Two concurrent passes contend on the same rows: one sits ``idle in
    transaction`` computing embeddings while the other blocks on
    ``Lock: transactionid``, burning both execution slots. The lock's TTL
    exceeds ``time_limit`` so an interrupted pass cannot strand it.
    """
    from poi_lake.config import get_settings

    settings = get_settings()
    lock_ttl_s = 25 * 60  # > the 20-minute time_limit above

    import redis as _redis

    client = _redis.from_url(settings.redis_url)
    if not client.set("poi-lake:lock:dedupe", "1", ex=lock_ttl_s, nx=True):
        logger.info("dedupe pass: skipped — another pass holds the lock")
        return

    logger.info("dedupe pass: starting")
    try:
        stats = asyncio.run(_run())
        logger.info("dedupe pass: done %r", stats)
    finally:
        client.delete("poi-lake:lock:dedupe")


async def _run() -> dict[str, Any]:
    from poi_lake.config import get_settings
    from poi_lake.db import get_engine, get_sessionmaker

    settings = get_settings()

    # Latched open by an earlier fatal resolver failure? Do nothing until an
    # operator resumes from the admin UI. Carrying on without the LLM would
    # merge NEEDS_LLM pairs into separate masters, and nothing revisits those.
    paused = await get_disabled()
    if paused is not None:
        logger.warning(
            "dedupe pass: PAUSED since %s — %s",
            paused.get("since"),
            paused.get("reason"),
        )
        return {"skipped": "llm_paused", **paused}

    # No key configured at all is a deliberate resolver-free deployment (dev, or
    # an operator who accepts the quality trade-off). That is not a pause.
    resolver = LLMResolver() if settings.anthropic_api_key else None

    try:
        async with session_scope() as session:
            svc = MergeService(resolver=resolver)
            return await svc.dedupe_pending(
                session,
                max_clusters=settings.dedupe_max_clusters_per_pass,
                commit_every=settings.dedupe_commit_every,
                max_seconds=settings.dedupe_max_seconds,
            )
    except LLMUnavailableError as exc:
        state = await disable(str(exc))
        return {"paused": "llm_unavailable", **state}
    finally:
        engine = get_engine()
        await engine.dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
