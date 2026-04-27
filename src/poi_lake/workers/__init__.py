"""Dramatiq workers — async background jobs.

Configure the broker once at module import time so any code (API or worker
process) that imports actors gets the same broker. The Redis URL comes from
:class:`poi_lake.config.Settings`.

To run workers in a container:

    dramatiq poi_lake.workers
"""

from __future__ import annotations

import logging
import sys
import threading
import time

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, Callbacks, Pipelines, Retries, ShutdownNotifications, TimeLimit

from poi_lake.config import get_settings
from poi_lake.observability import configure_logging

logger = logging.getLogger(__name__)


def _configure_broker() -> RedisBroker:
    settings = get_settings()
    # Configure structlog at worker start so dramatiq's own log lines are
    # JSON-formatted in production.
    configure_logging(env=settings.app_env, level=settings.app_log_level)
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
from poi_lake.workers import crawl_planner  # noqa: E402, F401
from poi_lake.workers import dedupe  # noqa: E402, F401
from poi_lake.workers import ingest  # noqa: E402, F401  (side-effect import)
from poi_lake.workers import normalize  # noqa: E402, F401


def _start_dedupe_scheduler() -> None:
    """Tick ``run_dedupe.send()`` every ``DEDUPE_SCHEDULE_MINUTES``.

    Worker processes are forked separately, so each one ends up running
    this scheduler — without coordination we'd enqueue the message N times
    per tick. Use a Redis ``SETNX`` window key to dedupe across processes:
    only the first ticker in each window's slot wins and enqueues. Setting
    ``DEDUPE_SCHEDULE_MINUTES=0`` disables the scheduler entirely.
    """
    settings = get_settings()
    interval_min = settings.dedupe_schedule_minutes
    if interval_min <= 0:
        logger.info("dedupe scheduler disabled (DEDUPE_SCHEDULE_MINUTES=0)")
        return

    interval_s = interval_min * 60

    def _tick() -> None:
        try:
            import redis as _redis
            r = _redis.from_url(settings.redis_url)
            window = int(time.time() // interval_s)
            key = f"poi-lake:scheduler:dedupe:{window}"
            if r.set(key, "1", ex=interval_s + 10, nx=True):
                from poi_lake.workers.dedupe import run_dedupe
                run_dedupe.send()
                logger.info("scheduled dedupe tick enqueued (window=%d)", window)
        except Exception:  # noqa: BLE001
            logger.exception("dedupe scheduler tick failed")
        finally:
            t = threading.Timer(interval_s, _tick)
            t.daemon = True
            t.start()

    # First tick after one interval — don't slam dedupe at every worker
    # restart (deploys would cause a thundering herd otherwise).
    t = threading.Timer(interval_s, _tick)
    t.daemon = True
    t.start()
    logger.info("dedupe scheduler armed: every %d minute(s)", interval_min)


def _start_crawl_planner_scheduler() -> None:
    """Tick ``run_crawl_planner.send()`` every ``CRAWL_PLANNER_MINUTES``.

    Same Redis-SETNX coordination as the dedupe scheduler — only one of
    the worker processes wins the window so we don't multiply tick rate
    when ``--processes`` is bumped.
    """
    settings = get_settings()
    interval_min = settings.crawl_planner_minutes
    if interval_min <= 0:
        logger.info("crawl planner scheduler disabled (CRAWL_PLANNER_MINUTES=0)")
        return

    interval_s = interval_min * 60

    def _tick() -> None:
        try:
            import redis as _redis
            r = _redis.from_url(settings.redis_url)
            window = int(time.time() // interval_s)
            key = f"poi-lake:scheduler:crawl_planner:{window}"
            if r.set(key, "1", ex=interval_s + 10, nx=True):
                from poi_lake.workers.crawl_planner import run_crawl_planner
                run_crawl_planner.send()
                logger.info("scheduled crawl-planner tick enqueued (window=%d)", window)
        except Exception:  # noqa: BLE001
            logger.exception("crawl planner scheduler tick failed")
        finally:
            t = threading.Timer(interval_s, _tick)
            t.daemon = True
            t.start()

    t = threading.Timer(interval_s, _tick)
    t.daemon = True
    t.start()
    logger.info("crawl planner scheduler armed: every %d minute(s)", interval_min)


# Only the worker process should run schedulers — the API container also
# imports this module (admin endpoints reference run_ingestion_job).
_invoked_as = sys.argv[0] if sys.argv else ""
if "dramatiq" in _invoked_as:
    _start_dedupe_scheduler()
    _start_crawl_planner_scheduler()
