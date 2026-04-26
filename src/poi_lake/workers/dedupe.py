"""Dedupe worker — runs MergeService on every pending row.

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
    logger.info("dedupe pass: starting")
    stats = asyncio.run(_run())
    logger.info("dedupe pass: done %r", stats)


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
            return await svc.dedupe_pending(session)
    finally:
        engine = get_engine()
        await engine.dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
