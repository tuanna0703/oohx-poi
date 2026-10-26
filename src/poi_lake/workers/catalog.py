"""Chain catalog actors — queue ``catalog``, consumed by the ``catalog`` worker.

``refresh_winmart`` sweeps WinMart's store list, matches it to masters, then
queues ``crawl_winmart_store`` for every store in ``CATALOG_WINMART_REGIONS``;
``queue_winmart_items`` re-reads those stores' listings alone (daily, for
prices), and ``snapshot_prices`` folds today's prices into ``chain_price_daily``.
``refresh_bachhoaxanh`` reads Bách Hoá Xanh's store list and matches it; that
chain has no product crawl.
One worker thread and ``CATALOG_MIN_INTERVAL_S`` between requests keep us at
about one request a second against winmart.vn.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import dramatiq

from poi_lake.catalog import bachhoaxanh
from poi_lake.catalog.base import CatalogTransientError
from poi_lake.catalog.chains import CHAINS
from poi_lake.catalog.winmart import CHAIN, WinMartClient
from poi_lake.config import get_settings
from poi_lake.db import session_scope
from poi_lake.services import catalog

logger = logging.getLogger(__name__)

WINMART_BRANDS = CHAINS[CHAIN].brands
BHX_BRANDS = CHAINS[bachhoaxanh.CHAIN].brands
# Below this share of the chain's published total, a sweep is treated as
# partial and retires no store.
COMPLETE_SHARE = 0.97
RECRAWL_AFTER = timedelta(hours=12)
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _run[T](coro_fn: Callable[[], Awaitable[T]]) -> T:
    """asyncio.run with a fresh engine, as in the ingest worker."""
    from poi_lake.db import get_engine, get_sessionmaker

    async def main() -> T:
        try:
            return await coro_fn()
        finally:
            await get_engine().dispose()
            get_engine.cache_clear()
            get_sessionmaker.cache_clear()

    return asyncio.run(main())


def regions() -> list[str] | None:
    raw = get_settings().catalog_winmart_regions.strip()
    if raw == "*":
        return None
    return [r.strip().upper() for r in raw.split(",") if r.strip()]


@dramatiq.actor(queue_name="catalog", max_retries=2, time_limit=4 * 3600_000)
def refresh_winmart(crawl_items: bool = True) -> None:
    result = _run(lambda: _refresh_winmart(crawl_items))
    logger.info("winmart refresh: %r", result)


async def _refresh_winmart(crawl_items: bool) -> dict:
    now = datetime.now(UTC)
    async with session_scope() as s:
        seeds = await catalog.seed_points(s, CHAIN, WINMART_BRANDS)
    async with WinMartClient(min_interval_s=get_settings().catalog_min_interval_s) as wm:
        total = await wm.store_total()
        stores, sweep = await wm.sweep_stores(seeds)
    complete = bool(total) and len(stores) >= COMPLETE_SHARE * total
    async with session_scope() as s:
        saved = await catalog.save_stores(s, CHAIN, stores.values(), now=now, complete=complete)
        matched = await catalog.match_stores(s, CHAIN, WINMART_BRANDS)
        codes = await catalog.stores_to_crawl(s, CHAIN, regions()) if crawl_items else []
    for code in codes:
        crawl_winmart_store.send(code)
    return {"published_total": total, "seeds": len(seeds), **sweep, **saved, **matched,
            "complete": complete, "queued": len(codes)}


@dramatiq.actor(queue_name="catalog", max_retries=2, time_limit=10 * 60_000)
def queue_winmart_items() -> None:
    """Re-read every store's listing (prices) without sweeping the store list."""
    result = _run(_queue_winmart_items)
    logger.info("winmart items queued: %r", result)


async def _queue_winmart_items() -> dict:
    async with session_scope() as s:
        codes = await catalog.stores_to_crawl(s, CHAIN, regions())
    for code in codes:
        crawl_winmart_store.send(code)
    return {"queued": len(codes)}


@dramatiq.actor(queue_name="catalog", max_retries=2, time_limit=10 * 60_000)
def snapshot_prices() -> None:
    """Today's price summary per product (Vietnam's calendar day), upserted."""
    result = _run(_snapshot_prices)
    logger.info("price snapshot: %r", result)


async def _snapshot_prices() -> dict:
    now = datetime.now(UTC)
    day = now.astimezone(VN_TZ).date()
    out = {}
    async with session_scope() as s:
        for chain in (c.code for c in CHAINS.values() if c.products):
            out[chain] = await catalog.snapshot_prices(s, chain, day, now)
    return {"day": day.isoformat(), **out}


@dramatiq.actor(queue_name="catalog", max_retries=2, time_limit=30 * 60_000)
def build_daily_report() -> None:
    """Yesterday's report (Vietnam's calendar), written into daily_reports."""
    result = _run(_build_daily_report)
    logger.info("daily report: %r", result)


async def _build_daily_report() -> dict:
    from poi_lake.services import reports

    day = datetime.now(VN_TZ).date() - timedelta(days=1)
    async with session_scope() as s:
        m = await reports.build_daily_report(s, day)
    return {"day": day.isoformat(), "new_masters": m["crawl"]["new_masters"]}


@dramatiq.actor(queue_name="catalog", max_retries=2, min_backoff=600_000, time_limit=60 * 60_000)
def refresh_bachhoaxanh() -> None:
    result = _run(_refresh_bachhoaxanh)
    logger.info("bachhoaxanh refresh: %r", result)


async def _refresh_bachhoaxanh() -> dict:
    now = datetime.now(UTC)
    async with bachhoaxanh.BHXClient(min_interval_s=get_settings().catalog_min_interval_s) as bhx:
        stores, stats = await bhx.all_stores()
    # Every province answered: a store no longer listed has closed.
    complete = stats["failed_provinces"] == 0 and bool(stores)
    async with session_scope() as s:
        saved = await catalog.save_stores(
            s, bachhoaxanh.CHAIN, stores.values(), now=now, complete=complete
        )
        matched = await catalog.match_stores(s, bachhoaxanh.CHAIN, BHX_BRANDS)
    return {**stats, **saved, **matched, "complete": complete}


@dramatiq.actor(queue_name="catalog", max_retries=3, min_backoff=60_000, time_limit=30 * 60_000)
def crawl_winmart_store(store_code: str) -> None:
    try:
        result = _run(lambda: _crawl_winmart_store(store_code))
    except CatalogTransientError as exc:
        logger.warning("winmart store %s transient: %s — retrying", store_code, exc)
        raise dramatiq.errors.Retry(message=str(exc)) from exc
    logger.info("winmart store %s: %r", store_code, result)


async def _crawl_winmart_store(store_code: str) -> dict:
    from sqlalchemy import text

    now = datetime.now(UTC)
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT group_code, items_crawled_at FROM chain_stores "
                    "WHERE chain = :c AND store_code = :s AND active"
                ),
                {"c": CHAIN, "s": store_code},
            )
        ).one_or_none()
    if row is None:
        return {"skipped": "not an active store"}
    # A refresh on demand and the weekly one can queue the same store twice.
    if row.items_crawled_at is not None and now - row.items_crawled_at < RECRAWL_AFTER:
        return {"skipped": "crawled recently"}
    group = row.group_code or ""
    async with WinMartClient(min_interval_s=get_settings().catalog_min_interval_s) as wm:
        menus = await wm.menus()
        items, failed = await wm.store_items(store_code, group, menus)
    answered = [name for _, name in menus if name not in failed]
    async with session_scope() as s:
        saved = await catalog.save_items(s, CHAIN, store_code, items, answered_menus=answered, now=now)
    return {**saved, "failed_menus": failed}
