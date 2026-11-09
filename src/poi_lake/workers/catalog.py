"""Chain catalog actors — queue ``catalog``, consumed by the ``catalog`` worker.

``refresh_winmart`` sweeps WinMart's store list, matches it to masters, then
queues ``crawl_winmart_store`` for every store in ``CATALOG_WINMART_REGIONS``;
``queue_winmart_items`` re-reads those stores' listings alone (daily, for
prices), and ``snapshot_prices`` folds today's prices into ``chain_price_daily``.
``refresh_bachhoaxanh`` reads Bách Hoá Xanh's store list and matches it; that
chain has no product crawl. ``refresh_concung`` reads Con Cưng's online prices
(its category pages) into one virtual store; off unless
``CATALOG_CONCUNG_ENABLED``. ``refresh_cellphones`` reads part of
CellphoneS's product pages each day into its virtual store, the pages read
longest ago first.
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

from poi_lake.catalog import bachhoaxanh, cellphones, concung
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


CONCUNG_SAVE_EVERY = 25  # categories read between two saves
CONCUNG_MAX_FAILURES = 20  # categories in a row that fail: the site is down, stop


@dramatiq.actor(queue_name="catalog", max_retries=1, min_backoff=600_000, time_limit=6 * 3600_000)
def refresh_concung() -> None:
    result = _run(_refresh_concung)
    logger.info("concung refresh: %r", result)


async def _refresh_concung() -> dict:
    """Every category page's products at the virtual online store.

    Saved every ``CONCUNG_SAVE_EVERY`` categories under one timestamp, so a
    run cut short keeps what it read. Retiring waits for the end of the run:
    a product that left one category may turn up in a later one. Only
    categories whose page arrived whole retire what they no longer list, and a
    run that stops early retires nothing."""
    now = datetime.now(UTC)
    label = CHAINS[concung.CHAIN].label
    async with session_scope() as s:
        store = await catalog.ensure_online_store(s, concung.CHAIN, label)
    stats = {"categories": 0, "whole": 0, "cut": 0, "failed": 0, "items": 0, "removed": 0}
    interval = get_settings().catalog_concung_interval_s
    async with concung.ConCungClient(min_interval_s=interval) as cc:
        urls = await cc.categories()
        stats["categories"] = len(urls)
        batch: dict[str, concung.Item] = {}
        whole: list[str] = []
        failures = 0

        async def save(retire: list[str]) -> None:
            async with session_scope() as s:
                saved = await catalog.save_items(
                    s, concung.CHAIN, store, list(batch.values()), answered_menus=retire, now=now
                )
            stats["items"] += saved["items"]
            stats["removed"] += saved["removed"]
            batch.clear()

        for i, url in enumerate(urls, 1):
            try:
                items, is_whole = await cc.category(url)
            except concung.CatalogError as exc:  # transient errors included
                stats["failed"] += 1
                failures += 1
                logger.info("concung %s: %s", url, exc)
                if failures >= CONCUNG_MAX_FAILURES:
                    stats["stopped"] = f"{failures} categories failed in a row"
                    break
                continue
            failures = 0
            stats["whole" if is_whole else "cut"] += 1
            for it in items:
                batch.setdefault(it.sku, it)
            # A whole page may retire what its category no longer lists; an
            # empty one may be a changed layout, so it retires nothing.
            # (Its menu is the category's shown name, the slug when it has none.)
            if is_whole and items and items[0].menu:
                whole.append(items[0].menu)
            if i % CONCUNG_SAVE_EVERY == 0:
                await save([])
        # Retire only now, and only when the run read its categories to the end.
        await save([] if "stopped" in stats else whole)
        stats["requests"] = cc.requests
    return stats


CELLPHONES_SAVE_EVERY = 100  # pages read between two saves
CELLPHONES_MAX_FAILURES = 30  # pages in a row that fail: the site is down, stop
# Below this share of the products already known, the sitemaps are taken as
# partial and retire nothing.
CELLPHONES_SITEMAP_SHARE = 0.9


def cellphones_order(slugs: list[str], read: dict[str, datetime]) -> list[str]:
    """Pages never read first (in the sitemap's order), then those read
    longest ago."""
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(slugs, key=lambda s: read.get(s, epoch))


@dramatiq.actor(queue_name="catalog", max_retries=0, time_limit=4 * 3600_000)
def refresh_cellphones() -> None:
    result = _run(_refresh_cellphones)
    logger.info("cellphones refresh: %r", result)


async def _refresh_cellphones() -> dict:
    """Up to ``CATALOG_CELLPHONES_PAGES`` product pages, for at most
    ``CATALOG_CELLPHONES_MINUTES``, at the virtual online store.

    A page with a price lists the product; a page with none (out of stock,
    "contact us") or gone takes it off the listing; a product the sitemaps no
    longer list leaves it too, when every sitemap file was read."""
    settings = get_settings()
    now = datetime.now(UTC)
    deadline = now + timedelta(minutes=settings.catalog_cellphones_minutes)
    label = CHAINS[cellphones.CHAIN].label
    async with session_scope() as s:
        store = await catalog.ensure_online_store(s, cellphones.CHAIN, label)
        read = await catalog.read_times(s, cellphones.CHAIN)
    stats = {"products": 0, "read": 0, "listed": 0, "unpriced": 0, "gone": 0, "skipped": 0,
             "failed": 0, "removed": 0}
    async with cellphones.CellphoneSClient(min_interval_s=settings.catalog_cellphones_interval_s) as cp:
        slugs, whole = await cp.products()
        stats["products"] = len(slugs)
        listed: dict[str, cellphones.Item] = {}
        unpriced: dict[str, cellphones.Item] = {}
        gone: list[str] = []
        failures = 0

        async def save() -> None:
            async with session_scope() as s:
                if unpriced:
                    await catalog.save_products(s, cellphones.CHAIN, list(unpriced.values()), now=now)
                stats["removed"] += await catalog.retire_skus(
                    s, cellphones.CHAIN, store, now=now, skus=[*gone, *unpriced]
                )
                await s.commit()
                saved = await catalog.save_items(
                    s, cellphones.CHAIN, store, list(listed.values()), answered_menus=[], now=now
                )
            stats["listed"] += saved["items"]
            listed.clear()
            unpriced.clear()
            gone.clear()

        for slug in cellphones_order(slugs, read)[: settings.catalog_cellphones_pages]:
            if datetime.now(UTC) >= deadline:
                stats["stopped"] = "time"
                break
            try:
                it = await cp.product(slug)
            except cellphones.GoneError:
                gone.append(cellphones.sku_of(slug))
                stats["gone"] += 1
                continue
            except cellphones.CatalogError as exc:  # transient errors included
                stats["failed"] += 1
                failures += 1
                logger.info("cellphones %s: %s", slug, exc)
                if failures >= CELLPHONES_MAX_FAILURES:
                    stats["stopped"] = f"{failures} pages failed in a row"
                    break
                continue
            failures = 0
            stats["read"] += 1
            if it is None:
                stats["skipped"] += 1  # used goods, or no product on the page
                gone.append(cellphones.sku_of(slug))
            elif it.price is None:
                stats["unpriced"] += 1
                unpriced[it.sku] = it
            else:
                listed[it.sku] = it
            if stats["read"] % CELLPHONES_SAVE_EVERY == 0:
                await save()
        await save()
        # Products the sitemaps no longer list, once every file was read and
        # the list is not suspiciously short against what we know.
        if whole and slugs and len(slugs) >= CELLPHONES_SITEMAP_SHARE * len(read):
            async with session_scope() as s:
                stats["removed"] += await catalog.retire_skus(
                    s, cellphones.CHAIN, store, now=now, keep_only=[cellphones.sku_of(x) for x in slugs]
                )
                await s.commit()
        stats["sitemaps_whole"] = whole
        stats["requests"] = cp.requests
    return stats


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
