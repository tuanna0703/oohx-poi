"""A chain's products seen by brand and by the company behind the brand.

Built on one row per product (today's price, how many stores list it), read
once and held for a few minutes: a chain lists a few thousand products, so
grouping them in Python is cheap and keeps the queries simple.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.catalog.brand_owners import BrandOwner, owner_of

CACHE_S = 600
COMPARE_LIMIT = 8  # brands or companies shown side by side


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    name: str
    owner: BrandOwner
    uom: str | None
    image_url: str | None
    group: str | None  # e.g. "Mỹ phẩm, chăm sóc cá nhân"
    category: str | None  # the narrowest, e.g. "Nước ngọt có ga"
    stores: int
    price: int | None  # what most stores charge today (sale price if on sale)
    regular: int | None  # most stores' list price
    on_sale: int  # stores selling it below list price


_cache: dict[str, tuple[float, list[Product], int]] = {}


async def _products(session: AsyncSession, chain: str) -> tuple[list[Product], int]:
    hit = _cache.get(chain)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1], hit[2]
    rows = (
        await session.execute(
            text(
                """
                SELECT p.sku, p.name, p.brand, p.uom, p.image_url, p.category_path,
                       count(*) AS stores,
                       mode() WITHIN GROUP (ORDER BY COALESCE(sp.sale_price, sp.price)) AS price,
                       mode() WITHIN GROUP (ORDER BY sp.price) AS regular,
                       count(*) FILTER (WHERE sp.sale_price < sp.price) AS on_sale
                FROM chain_store_products sp
                JOIN chain_stores s ON s.chain = sp.chain AND s.store_code = sp.store_code AND s.active
                JOIN chain_products p ON p.chain = sp.chain AND p.sku = sp.sku
                WHERE sp.chain = :c
                GROUP BY p.sku, p.name, p.brand, p.uom, p.image_url, p.category_path
                """
            ),
            {"c": chain},
        )
    ).all()
    out: list[Product] = []
    for r in rows:
        owner = owner_of(r.brand)
        if owner is None:
            continue
        path = list(r.category_path or [])
        out.append(
            Product(
                sku=r.sku, name=r.name, owner=owner, uom=r.uom,
                image_url=r.image_url if (r.image_url or "").startswith("https://") else None,
                group=path[2] if len(path) > 2 else (path[-1] if path else None),
                category=path[-1] if path else None,
                stores=int(r.stores),
                price=int(r.price) if r.price is not None else None,
                regular=int(r.regular) if r.regular is not None else None,
                on_sale=int(r.on_sale),
            )
        )
    stores = int(
        (
            await session.execute(
                text(
                    "SELECT count(DISTINCT sp.store_code) FROM chain_store_products sp "
                    "JOIN chain_stores s ON s.chain = sp.chain AND s.store_code = sp.store_code AND s.active "
                    "WHERE sp.chain = :c"
                ),
                {"c": chain},
            )
        ).scalar_one()
    )
    _cache[chain] = (time.monotonic(), out, stores)
    return out, stores


def _brand(o: BrandOwner) -> dict[str, Any]:
    return {"slug": o.slug, "name": o.name, "company": o.company, "company_slug": o.company_slug,
            "private_label": o.private_label}


def _product(p: Product) -> dict[str, Any]:
    return {"sku": p.sku, "name": p.name, "uom": p.uom, "image_url": p.image_url, "group": p.group,
            "category": p.category, "stores": p.stores, "price": p.price, "regular": p.regular,
            "on_sale": p.on_sale}


def _main(values: list[str | None]) -> str | None:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None


async def brands(session: AsyncSession, chain: str, company_slug: str | None = None) -> dict[str, Any]:
    """Every brand with products listed, most products first."""
    products, stores_total = await _products(session, chain)
    by: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if company_slug is None or p.owner.company_slug == company_slug:
            by[p.owner.slug].append(p)
    rows = [
        {**_brand(ps[0].owner), "skus": len(ps), "stores": max(p.stores for p in ps),
         "group": _main([p.group for p in ps]), "category": _main([p.category for p in ps])}
        for ps in by.values()
    ]
    rows.sort(key=lambda r: (-r["skus"], r["name"]))
    return {"chain": chain, "stores_total": stores_total, "brands": rows}


async def brand_detail(session: AsyncSession, chain: str, slug: str) -> dict[str, Any] | None:
    products, stores_total = await _products(session, chain)
    mine = [p for p in products if p.owner.slug == slug]
    if not mine:
        return None
    owner = mine[0].owner
    raws = sorted({p.owner.brand for p in mine})
    stores = int(
        (
            await session.execute(
                text(
                    """
                    SELECT count(DISTINCT sp.store_code)
                    FROM chain_store_products sp
                    JOIN chain_stores s ON s.chain = sp.chain AND s.store_code = sp.store_code AND s.active
                    JOIN chain_products p ON p.chain = sp.chain AND p.sku = sp.sku
                    WHERE sp.chain = :c AND upper(p.brand) = ANY(:b)
                    """
                ),
                {"c": chain, "b": raws},
            )
        ).scalar_one()
    )
    category = _main([p.category for p in mine])
    # Brands in the same narrow category, with how many of their products are in it.
    in_cat: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if p.category == category:
            in_cat[p.owner.slug].append(p)
    compare = sorted(
        ({**_brand(ps[0].owner), "skus": len(ps)} for ps in in_cat.values()),
        key=lambda r: (-r["skus"], r["name"]),
    )[:COMPARE_LIMIT]
    siblings: list[dict[str, Any]] = []
    if owner.company_slug:
        sib: dict[str, list[Product]] = defaultdict(list)
        for p in products:
            if p.owner.company_slug == owner.company_slug and p.owner.slug != slug:
                sib[p.owner.slug].append(p)
        siblings = sorted(({**_brand(ps[0].owner), "skus": len(ps)} for ps in sib.values()),
                          key=lambda r: (-r["skus"], r["name"]))
    prices = [p.price for p in mine if p.price is not None]
    return {
        "chain": chain,
        "brand": _brand(owner),
        "stores_total": stores_total,
        "stores": stores,
        "category": category,
        "group": _main([p.group for p in mine]),
        "on_sale_skus": sum(1 for p in mine if p.on_sale),
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "products": [_product(p) for p in sorted(mine, key=lambda p: (-p.stores, p.name))],
        "compare": compare,
        "siblings": siblings,
    }


async def company_detail(session: AsyncSession, chain: str, slug: str) -> dict[str, Any] | None:
    products, stores_total = await _products(session, chain)
    mine = [p for p in products if p.owner.company_slug == slug]
    if not mine:
        return None
    company = mine[0].owner.company
    # Each brand under the group most of its products are in.
    by_brand: dict[str, list[Product]] = defaultdict(list)
    for p in mine:
        by_brand[p.owner.slug].append(p)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ps in by_brand.values():
        groups[_main([p.group for p in ps]) or "Khác"].append({**_brand(ps[0].owner), "skus": len(ps)})
    group_rows = []
    for g, bs in groups.items():
        bs.sort(key=lambda r: (-r["skus"], r["name"]))
        # Rivals: companies by their products in this group (brands without a known company are left out).
        rivals: Counter[str] = Counter()
        slugs: dict[str, str] = {}
        for p in products:
            if p.group == g and p.owner.company:
                rivals[p.owner.company] += 1
                slugs[p.owner.company] = p.owner.company_slug or ""
        group_rows.append({
            "group": g,
            "skus": sum(b["skus"] for b in bs),
            "brands": bs,
            "rivals": [{"company": c, "company_slug": slugs[c], "skus": n}
                       for c, n in rivals.most_common(COMPARE_LIMIT)],
        })
    group_rows.sort(key=lambda r: -r["skus"])
    return {
        "chain": chain,
        "company": company,
        "company_slug": slug,
        "stores_total": stores_total,
        "brands": sum(len(g["brands"]) for g in group_rows),
        "skus": len(mine),
        "on_sale_skus": sum(1 for p in mine if p.on_sale),
        "groups": group_rows,
    }


def product_owner(brand: str | None) -> dict[str, Any] | None:
    o = owner_of(brand)
    return _brand(o) if o else None
