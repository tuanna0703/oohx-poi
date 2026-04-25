"""Ingestion worker — runs IngestionService inside a Dramatiq actor.

Dramatiq actors are sync; we bridge to asyncio with ``asyncio.run`` per job.
That gives each job a fresh event loop and a fresh DB session, which is the
right isolation level for a background worker.
"""

from __future__ import annotations

import asyncio
import logging

import dramatiq

from poi_lake.adapters import AdapterTransientError
from poi_lake.db import session_scope
from poi_lake.services.ingestion import IngestionService

logger = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="ingest",
    max_retries=3,
    min_backoff=10_000,        # 10s
    max_backoff=600_000,       # 10 min
    time_limit=30 * 60_000,    # 30 min hard limit per job
)
def run_ingestion_job(job_id: int) -> None:
    """Entry point — schedule with ``run_ingestion_job.send(job_id)``."""
    logger.info("ingestion job %d: starting", job_id)
    try:
        result = asyncio.run(_run(job_id))
    except AdapterTransientError as exc:
        logger.warning("ingestion job %d transient: %s — retrying", job_id, exc)
        raise dramatiq.errors.Retry(message=str(exc)) from exc
    logger.info("ingestion job %d: done %r", job_id, result)


async def _run(job_id: int) -> dict[str, int]:
    async with session_scope() as session:
        svc = IngestionService(session)
        return await svc.run_job(job_id)
