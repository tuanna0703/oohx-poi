"""Chain catalog storage: store lists, matching stores to masters, products.

A chain's store is matched to the nearest active master of the chain's brand
within ``MATCH_M``; each master goes to at most one store (closest pair
first), so two stores in one mall do not share a pin.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.catalog.winmart import Item, Store, product_url
from poi_lake.pipeline.normalize.text import normalize_text

logger = logging.getLogger(__name__)

MATCH_M = 150.0
SEED_GRID_DEG = 0.03  # ~3 km: one sweep seed per cell
BATCH = 500


def _point(lat: float, lng: float) -> str:
    return f"SRID=4326;POINT({lng} {lat})"


def _money(v: float | None) -> int | None:
    return round(v) if v is not None else None


async def seed_points(
    session: AsyncSession, chain: str, brands: Sequence[str]
) -> list[tuple[float, float]]:
    """Where to start a store sweep: stores we know, and the brand's masters."""
    rows = (
        await session.execute(
            text(
                """
                SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
                FROM chain_stores WHERE chain = :chain
                UNION ALL
                SELECT ST_Y(location::geometry), ST_X(location::geometry)
                FROM master_pois WHERE status = 'active' AND brand = ANY(:brands)
                """
            ),
            {"chain": chain, "brands": list(brands)},
        )
    ).all()
    cells: dict[tuple[int, int], tuple[float, float]] = {}
    for lat, lng in rows:
        cells.setdefault((round(lat / SEED_GRID_DEG), round(lng / SEED_GRID_DEG)), (lat, lng))
    return list(cells.values())


async def save_stores(
    session: AsyncSession, chain: str, stores: Iterable[Store], *, now: datetime, complete: bool
) -> dict[str, int]:
    """Upsert the sweep's stores. ``complete`` (the sweep found the chain's
    published total) also retires stores the sweep no longer sees."""
    before = (
        await session.execute(text("SELECT count(*) FROM chain_stores WHERE chain = :c"), {"c": chain})
    ).scalar_one()
    rows = [
        {
            "chain": chain, "code": s.code, "name": s.name, "type": s.store_type,
            "grp": s.group_code, "addr": s.address, "region": s.region_code,
            "loc": _point(s.lat, s.lng), "raw": json.dumps(s.raw, ensure_ascii=False), "now": now,
        }
        for s in stores
    ]
    for i in range(0, len(rows), BATCH):
        await session.execute(
            text(
                """
                INSERT INTO chain_stores (chain, store_code, name, store_type, group_code,
                    address, region_code, location, raw, first_seen_at, last_seen_at, active)
                VALUES (:chain, :code, :name, :type, :grp, :addr, :region,
                    ST_GeogFromText(:loc), CAST(:raw AS jsonb), :now, :now, true)
                ON CONFLICT (chain, store_code) DO UPDATE SET
                    name = EXCLUDED.name, store_type = EXCLUDED.store_type,
                    group_code = EXCLUDED.group_code, address = EXCLUDED.address,
                    region_code = EXCLUDED.region_code, location = EXCLUDED.location,
                    raw = EXCLUDED.raw, last_seen_at = EXCLUDED.last_seen_at, active = true
                """
            ),
            rows[i : i + BATCH],
        )
    retired = 0
    if complete:
        retired = (
            await session.execute(
                text(
                    "UPDATE chain_stores SET active = false "
                    "WHERE chain = :c AND active AND last_seen_at < :now"
                ),
                {"c": chain, "now": now},
            )
        ).rowcount
    await session.commit()
    after = (
        await session.execute(text("SELECT count(*) FROM chain_stores WHERE chain = :c"), {"c": chain})
    ).scalar_one()
    return {"seen": len(rows), "new": int(after - before), "retired": int(retired or 0)}


async def match_stores(
    session: AsyncSession, chain: str, brands: Sequence[str], *, max_m: float = MATCH_M
) -> dict[str, int]:
    pairs = (
        await session.execute(
            text(
                """
                SELECT s.store_code, m.id, ST_Distance(s.location, m.location) AS d
                FROM chain_stores s
                CROSS JOIN LATERAL (
                    SELECT id, location FROM master_pois m
                    WHERE m.status = 'active' AND m.brand = ANY(:brands)
                      AND ST_DWithin(m.location, s.location, :max_m)
                    ORDER BY m.location <-> s.location
                    LIMIT 5
                ) m
                WHERE s.chain = :chain AND s.active
                ORDER BY d
                """
            ),
            {"chain": chain, "brands": list(brands), "max_m": max_m},
        )
    ).all()
    taken_stores: set[str] = set()
    taken_masters: set[Any] = set()
    links = []
    for store_code, master_id, d in pairs:
        if store_code in taken_stores or master_id in taken_masters:
            continue
        taken_stores.add(store_code)
        taken_masters.add(master_id)
        links.append({"chain": chain, "code": store_code, "m": master_id, "d": float(d)})

    await session.execute(
        text("UPDATE chain_stores SET master_id = NULL, match_m = NULL WHERE chain = :chain"),
        {"chain": chain},
    )
    for i in range(0, len(links), BATCH):
        await session.execute(
            text(
                "UPDATE chain_stores SET master_id = :m, match_m = :d "
                "WHERE chain = :chain AND store_code = :code"
            ),
            links[i : i + BATCH],
        )
    await session.commit()
    return {"matched": len(links)}


async def summary(session: AsyncSession, chain: str, brands: Sequence[str]) -> dict[str, Any]:
    s = (
        await session.execute(
            text(
                """
                SELECT count(*) FILTER (WHERE active) AS stores,
                       count(*) FILTER (WHERE active AND master_id IS NOT NULL) AS matched,
                       count(*) FILTER (WHERE active AND items_crawled_at IS NOT NULL) AS crawled,
                       max(last_seen_at) AS last_sweep,
                       max(items_crawled_at) AS last_items
                FROM chain_stores WHERE chain = :chain
                """
            ),
            {"chain": chain},
        )
    ).one()
    by_type = dict(
        (
            await session.execute(
                text(
                    "SELECT coalesce(store_type, ''), count(*) FROM chain_stores "
                    "WHERE chain = :chain AND active GROUP BY 1"
                ),
                {"chain": chain},
            )
        ).all()
    )
    masters = (
        await session.execute(
            text("SELECT count(*) FROM master_pois WHERE status = 'active' AND brand = ANY(:b)"),
            {"b": list(brands)},
        )
    ).scalar_one()
    products, listings = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM chain_products WHERE chain = :c), "
                "(SELECT count(*) FROM chain_store_products WHERE chain = :c)"
            ),
            {"c": chain},
        )
    ).one()
    return {
        "chain": chain,
        "stores": int(s.stores),
        "stores_by_type": {k: int(v) for k, v in by_type.items()},
        "stores_matched": int(s.matched),
        "brand_masters": int(masters),
        "brand_masters_unmatched": int(masters) - int(s.matched),
        "stores_crawled": int(s.crawled),
        "products": int(products),
        "listings": int(listings),
        "last_sweep": s.last_sweep,
        "last_items": s.last_items,
    }


async def stores_to_crawl(session: AsyncSession, chain: str, regions: Sequence[str] | None) -> list[str]:
    """Active stores in ``regions`` (None = all), least recently crawled first."""
    rows = (
        await session.execute(
            text(
                """
                SELECT store_code FROM chain_stores
                WHERE chain = :chain AND active
                  AND (CAST(:regions AS text[]) IS NULL OR region_code = ANY(CAST(:regions AS text[])))
                ORDER BY items_crawled_at NULLS FIRST, store_code
                """
            ),
            {"chain": chain, "regions": list(regions) if regions is not None else None},
        )
    ).scalars().all()
    return list(rows)


async def save_items(
    session: AsyncSession,
    chain: str,
    store_code: str,
    items: Sequence[Item],
    *,
    answered_menus: Sequence[str],
    now: datetime,
) -> dict[str, int]:
    """Replace one store's listing. Rows of menus that failed this time stay."""
    products = [
        {
            "chain": chain, "sku": it.sku, "no": it.item_no, "name": it.name,
            "folded": normalize_text(it.name), "brand": it.brand, "barcode": it.barcode,
            "uom": it.uom, "path": list(it.category_path), "seo": it.seo_name, "now": now,
        }
        for it in items
    ]
    listings = [
        {
            "chain": chain, "store": store_code, "sku": it.sku, "menu": it.menu,
            "price": _money(it.price), "sale": _money(it.sale_price), "now": now,
        }
        for it in items
    ]
    for i in range(0, len(products), BATCH):
        await session.execute(
            text(
                """
                INSERT INTO chain_products (chain, sku, item_no, name, name_folded, brand,
                    barcode, uom, category_path, seo_name, first_seen_at, last_seen_at)
                VALUES (:chain, :sku, :no, :name, :folded, :brand, :barcode, :uom,
                    CAST(:path AS text[]), :seo, :now, :now)
                ON CONFLICT (chain, sku) DO UPDATE SET
                    item_no = EXCLUDED.item_no, name = EXCLUDED.name,
                    name_folded = EXCLUDED.name_folded, brand = EXCLUDED.brand,
                    barcode = EXCLUDED.barcode, uom = EXCLUDED.uom,
                    category_path = EXCLUDED.category_path, seo_name = EXCLUDED.seo_name,
                    last_seen_at = EXCLUDED.last_seen_at
                """
            ),
            products[i : i + BATCH],
        )
    for i in range(0, len(listings), BATCH):
        await session.execute(
            text(
                """
                INSERT INTO chain_store_products (chain, store_code, sku, menu, price, sale_price, seen_at)
                VALUES (:chain, :store, :sku, :menu, :price, :sale, :now)
                ON CONFLICT (chain, store_code, sku) DO UPDATE SET
                    menu = EXCLUDED.menu, price = EXCLUDED.price,
                    sale_price = EXCLUDED.sale_price, seen_at = EXCLUDED.seen_at
                """
            ),
            listings[i : i + BATCH],
        )
    gone = (
        await session.execute(
            text(
                """
                DELETE FROM chain_store_products
                WHERE chain = :chain AND store_code = :store AND seen_at < :now
                  AND (menu IS NULL OR menu = ANY(:menus))
                """
            ),
            {"chain": chain, "store": store_code, "now": now, "menus": list(answered_menus)},
        )
    ).rowcount
    await session.execute(
        text(
            """
            UPDATE chain_stores SET items_crawled_at = :now,
                items_count = (SELECT count(*) FROM chain_store_products
                               WHERE chain = :chain AND store_code = :store)
            WHERE chain = :chain AND store_code = :store
            """
        ),
        {"chain": chain, "store": store_code, "now": now},
    )
    await session.commit()
    return {"items": len(items), "removed": int(gone or 0)}


_LIKE_ESCAPE = re.compile(r"([\\%_])")


async def master_products(
    session: AsyncSession,
    master_id: Any,
    *,
    q: str | None = None,
    menu: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """What the chain store linked to ``master_id`` lists online."""
    store = (
        await session.execute(
            text(
                """
                SELECT chain, store_code, name, address, store_type, group_code,
                       items_crawled_at, items_count
                FROM chain_stores WHERE master_id = :id AND active
                ORDER BY match_m LIMIT 1
                """
            ),
            {"id": master_id},
        )
    ).one_or_none()
    out: dict[str, Any] = {"store": None, "menus": [], "items": [], "total": 0,
                           "page": page, "per_page": per_page}
    if store is None:
        return out
    out["store"] = {
        "chain": store.chain, "store_code": store.store_code, "name": store.name,
        "address": store.address, "store_type": store.store_type,
        "crawled_at": store.items_crawled_at, "items_count": store.items_count,
    }
    if store.items_crawled_at is None:
        return out

    key = {"chain": store.chain, "store": store.store_code}
    out["menus"] = [
        {"name": m, "count": int(n)}
        for m, n in (
            await session.execute(
                text(
                    "SELECT menu, count(*) FROM chain_store_products "
                    "WHERE chain = :chain AND store_code = :store AND menu IS NOT NULL "
                    "GROUP BY menu ORDER BY count(*) DESC, menu"
                ),
                key,
            )
        ).all()
    ]

    clauses = ["sp.chain = :chain", "sp.store_code = :store"]
    params: dict[str, Any] = dict(key)
    if menu:
        clauses.append("sp.menu = :menu")
        params["menu"] = menu
    for j, token in enumerate(normalize_text(q or "").split()[:6]):
        clauses.append(f"p.name_folded LIKE :t{j} ESCAPE '\\'")
        params[f"t{j}"] = "%" + _LIKE_ESCAPE.sub(r"\\\1", token) + "%"
    where = " AND ".join(clauses)
    join = "FROM chain_store_products sp JOIN chain_products p ON p.chain = sp.chain AND p.sku = sp.sku"
    out["total"] = int(
        (await session.execute(text(f"SELECT count(*) {join} WHERE {where}"), params)).scalar_one()
    )
    rows = (
        await session.execute(
            text(
                f"""
                SELECT p.sku, p.name, p.brand, p.barcode, p.uom, p.category_path, p.seo_name,
                       sp.menu, sp.price, sp.sale_price
                {join} WHERE {where}
                ORDER BY sp.menu, p.name, p.sku
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": per_page, "offset": (page - 1) * per_page},
        )
    ).all()
    out["items"] = [
        {
            "sku": r.sku, "name": r.name, "brand": r.brand, "barcode": r.barcode, "uom": r.uom,
            "category_path": list(r.category_path or []), "menu": r.menu,
            "price": int(r.price) if r.price is not None else None,
            "sale_price": int(r.sale_price) if r.sale_price is not None else None,
            "url": product_url(r.seo_name, store.store_code, store.group_code or "")
            if r.seo_name else None,
        }
        for r in rows
    ]
    return out
