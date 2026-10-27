"""Weekly brand discovery (``services/brand_discovery.py``).

Runs on the ``dedupe`` queue of the main worker: it reads every untagged
master's name at once, which the catalog worker's 384 MB would not hold.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import dramatiq

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="dedupe", max_retries=0, time_limit=45 * 60_000)
def discover_brands() -> None:
    stats = asyncio.run(_run())
    logger.info("brand discovery done %r", stats)


async def _run() -> dict[str, Any]:
    from poi_lake.db import get_engine, get_sessionmaker, session_scope
    from poi_lake.services.brand_discovery import run

    try:
        async with session_scope() as session:
            return await run(session)
    finally:
        await get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
