"""Dramatiq workers — async background jobs.

Configure the broker once at module import time so any code (API or worker
process) that imports actors gets the same broker. The Redis URL comes from
:class:`poi_lake.config.Settings`.

To run workers in a container:

    dramatiq poi_lake.workers
"""

from __future__ import annotations

import logging

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, Callbacks, Pipelines, Retries, ShutdownNotifications, TimeLimit

from poi_lake.config import get_settings

logger = logging.getLogger(__name__)


def _configure_broker() -> RedisBroker:
    settings = get_settings()
    broker = RedisBroker(url=settings.redis_url)
    # Default middlewares minus Prometheus (will be added in Phase 7).
    broker.add_middleware(AgeLimit())
    broker.add_middleware(TimeLimit())
    broker.add_middleware(ShutdownNotifications())
    broker.add_middleware(Callbacks())
    broker.add_middleware(Pipelines())
    broker.add_middleware(Retries(max_retries=3, min_backoff=5_000, max_backoff=300_000))
    dramatiq.set_broker(broker)
    logger.info("dramatiq broker configured: %s", settings.redis_url)
    return broker


broker = _configure_broker()

# Importing the actor modules registers actors against the broker.
from poi_lake.workers import ingest  # noqa: E402, F401  (side-effect import)
from poi_lake.workers import normalize  # noqa: E402, F401
