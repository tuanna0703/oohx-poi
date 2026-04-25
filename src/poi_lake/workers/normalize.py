"""Normalize worker — turns one raw_poi into one processed_poi.

The IngestionService calls ``run_normalize_raw_poi.send(raw_poi_id)`` after
each successful insert; this actor consumes those messages.
"""

from __future__ import annotations

import asyncio
import logging

import dramatiq

from poi_lake.db import session_scope
from poi_lake.pipeline.orchestrator import NormalizePipeline

logger = logging.getLogger(__name__)


# Module-level pipeline so a worker process pre-loads brand cache + model
# once and re-uses across many calls.
_pipeline: NormalizePipeline | None = None


def _get_pipeline() -> NormalizePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = NormalizePipeline()
    return _pipeline


@dramatiq.actor(
    queue_name="normalize",
    max_retries=3,
    min_backoff=5_000,
    max_backoff=300_000,
    time_limit=10 * 60_000,
)
def run_normalize_raw_poi(raw_poi_id: int) -> None:
    asyncio.run(_run(raw_poi_id))


async def _run(raw_poi_id: int) -> None:
    from poi_lake.db import get_engine, get_sessionmaker
    try:
        async with session_scope() as session:
            pipeline = _get_pipeline()
            if not pipeline.brand_detector._brands:  # type: ignore[attr-defined]
                await pipeline.warm_up(session)
            result = await pipeline.process(session, raw_poi_id)
        if result is None:
            logger.info("normalize: raw_poi %d skipped", raw_poi_id)
        else:
            logger.info("normalize: raw_poi %d → processed_poi %d", raw_poi_id, result)
    finally:
        # Each Dramatiq invocation gets its own asyncio.run() event loop.
        # The cached engine's connections are bound to the loop that created
        # them, so we must dispose + clear the cache so the next invocation
        # re-creates the engine on its own fresh loop.
        engine = get_engine()
        await engine.dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
