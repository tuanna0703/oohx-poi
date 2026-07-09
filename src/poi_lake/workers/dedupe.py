"""Dedupe worker — runs MergeService over a bounded slice of pending rows.

A pass handles at most ``DEDUPE_MAX_CLUSTERS_PER_PASS`` clusters and commits
every ``DEDUPE_COMMIT_EVERY`` of them, so it finishes inside the actor's
20-minute ``time_limit`` and keeps whatever it committed if it doesn't.
Only one pass runs at a time (Redis lock).

Triggered:
  * on demand via ``run_dedupe.send()`` (e.g. admin endpoint, post-batch);
  * on a periodic schedule (``DEDUPE_SCHEDULE_MINUTES``, default 15) by a
    tiny in-process timer thread that ``send()``s a tick message. Phase 7
    will swap that for a proper APScheduler / cron container.
"""

from __future__ import annotations

import asyncio
import logging

import dramatiq

from poi_lake.db import session_scope
from poi_lake.pipeline.dedupe import LLMResolver, MergeService

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


async def _run() -> dict[str, int]:
    from poi_lake.config import get_settings
    from poi_lake.db import get_engine, get_sessionmaker

    settings = get_settings()
    # Only enable the LLM resolver if we have a key; without it, NEEDS_LLM
    # pairs simply stay as separate masters until next run.
    resolver = LLMResolver() if settings.anthropic_api_key else None

    try:
        async with session_scope() as session:
            svc = MergeService(resolver=resolver)
            return await svc.dedupe_pending(
                session,
                max_clusters=settings.dedupe_max_clusters_per_pass,
                commit_every=settings.dedupe_commit_every,
            )
    finally:
        engine = get_engine()
        await engine.dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
