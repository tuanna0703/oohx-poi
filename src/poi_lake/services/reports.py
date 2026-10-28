"""The daily report: what changed in the lake over one day.

``build_daily_report`` runs at 07:00 Vietnam time and writes one row of
``daily_reports`` for the day that just ended there (``day``): crawl and
dedupe activity in that day, active stores per brand at the time, and for
each chain whose store list we read: its stores, products and price moves.
Day-over-day changes are left to the reader, from consecutive rows.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.catalog.chains import CHAINS

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TOP_PRICE_MOVES = 10


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """``day`` in Vietnam, as a UTC [start, end) range."""
    start = datetime.combine(day, time(0), tzinfo=VN_TZ).astimezone(UTC)
    return start, start + timedelta(days=1)


async def _scalar(session: AsyncSession, sql: str, **params: Any) -> int:
    return int((await session.execute(text(sql), params)).scalar_one() or 0)


async def _chain_metrics(session: AsyncSession, chain: str, day: date, start: datetime, end: datetime) -> dict[str, Any]:
    p = {"c": chain, "s": start, "e": end}
    out: dict[str, Any] = {
        "stores": await _scalar(session, "SELECT count(*) FROM chain_stores WHERE chain = :c AND active", **p),
        "new_stores": await _scalar(
            session,
            "SELECT count(*) FROM chain_stores WHERE chain = :c AND first_seen_at >= :s AND first_seen_at < :e",
            **p,
        ),
        "stores_with_products": await _scalar(
            session,
            "SELECT count(*) FROM chain_stores WHERE chain = :c AND active AND items_crawled_at IS NOT NULL",
            **p,
        ),
        "stores_read": await _scalar(
            session,
            "SELECT count(*) FROM chain_stores WHERE chain = :c AND items_crawled_at >= :s AND items_crawled_at < :e",
            **p,
        ),
        "skus": await _scalar(
            session, "SELECT count(DISTINCT sku) FROM chain_store_products WHERE chain = :c", **p
        ),
        "new_skus": await _scalar(
            session,
            "SELECT count(*) FROM chain_products WHERE chain = :c AND first_seen_at >= :s AND first_seen_at < :e",
            **p,
        ),
        "price_changes": await _scalar(
            session,
            """
            SELECT count(*) FROM chain_price_changes c
            WHERE c.chain = :c AND c.seen_at >= :s AND c.seen_at < :e AND c.price IS NOT NULL
              AND EXISTS (SELECT 1 FROM chain_price_changes o
                          WHERE o.chain = c.chain AND o.store_code = c.store_code
                            AND o.sku = c.sku AND o.seen_at < c.seen_at)
            """,
            **p,
        ),
        "delisted": await _scalar(
            session,
            """
            SELECT count(*) FROM chain_price_changes
            WHERE chain = :c AND seen_at >= :s AND seen_at < :e AND price IS NULL AND sale_price IS NULL
            """,
            **p,
        ),
    }
    # Promotions and the biggest moves, per product, from the daily summaries.
    prev = day - timedelta(days=1)
    dp = {"c": chain, "d": day, "p": prev}
    out["promos_started"] = await _scalar(
        session,
        """
        SELECT count(*) FROM chain_price_daily t JOIN chain_price_daily y
          ON y.chain = t.chain AND y.sku = t.sku AND y.day = :p
        WHERE t.chain = :c AND t.day = :d AND t.on_sale > 0 AND y.on_sale = 0
        """,
        **dp,
    )
    out["promos_ended"] = await _scalar(
        session,
        """
        SELECT count(*) FROM chain_price_daily t JOIN chain_price_daily y
          ON y.chain = t.chain AND y.sku = t.sku AND y.day = :p
        WHERE t.chain = :c AND t.day = :d AND t.on_sale = 0 AND y.on_sale > 0
        """,
        **dp,
    )
    moves = (
        await session.execute(
            text(
                """
                SELECT t.sku, p.name, p.brand, y.mode_price AS before, t.mode_price AS after
                FROM chain_price_daily t
                JOIN chain_price_daily y ON y.chain = t.chain AND y.sku = t.sku AND y.day = :p
                JOIN chain_products p ON p.chain = t.chain AND p.sku = t.sku
                WHERE t.chain = :c AND t.day = :d AND y.mode_price > 0
                  AND t.mode_price IS DISTINCT FROM y.mode_price
                ORDER BY abs(t.mode_price - y.mode_price) / y.mode_price DESC, t.sku
                LIMIT :n
                """
            ),
            {**dp, "n": TOP_PRICE_MOVES},
        )
    ).all()
    out["top_price_moves"] = [
        {"sku": m.sku, "name": m.name, "brand": m.brand, "before": int(m.before), "after": int(m.after)}
        for m in moves
    ]
    return out


async def build_daily_report(session: AsyncSession, day: date) -> dict[str, Any]:
    """Compute and upsert ``day``'s report; returns its metrics."""
    start, end = day_bounds(day)
    p = {"s": start, "e": end}
    jobs = {
        r.status: int(r.n)
        for r in (
            await session.execute(
                text(
                    "SELECT status, count(*) AS n FROM ingestion_jobs "
                    "WHERE created_at >= :s AND created_at < :e GROUP BY status"
                ),
                p,
            )
        ).all()
    }
    brands = {
        r.brand: int(r.n)
        for r in (
            await session.execute(
                text(
                    "SELECT brand, count(*) AS n FROM master_pois "
                    "WHERE status = 'active' AND brand IS NOT NULL GROUP BY brand"
                )
            )
        ).all()
    }
    metrics: dict[str, Any] = {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "crawl": {
            "jobs": jobs,
            "raw_rows": await _scalar(
                session, "SELECT count(*) FROM raw_pois WHERE fetched_at >= :s AND fetched_at < :e", **p
            ),
            "new_masters": await _scalar(
                session, "SELECT count(*) FROM master_pois WHERE created_at >= :s AND created_at < :e", **p
            ),
            "merged_away": await _scalar(
                session,
                "SELECT count(*) FROM master_pois WHERE status = 'merged_away' "
                "AND updated_at >= :s AND updated_at < :e",
                **p,
            ),
            "archived": await _scalar(
                session,
                "SELECT count(*) FROM master_pois WHERE status = 'archived' "
                "AND updated_at >= :s AND updated_at < :e",
                **p,
            ),
        },
        "masters_active": await _scalar(session, "SELECT count(*) FROM master_pois WHERE status = 'active'"),
        "brands": brands,
        "chains": {
            c.code: await _chain_metrics(session, c.code, day, start, end) for c in CHAINS.values()
        },
    }
    await session.execute(
        text(
            """
            INSERT INTO daily_reports (day, metrics) VALUES (:d, CAST(:m AS jsonb))
            ON CONFLICT (day) DO UPDATE SET metrics = EXCLUDED.metrics, created_at = now()
            """
        ),
        {"d": day, "m": json.dumps(metrics, ensure_ascii=False)},
    )
    await session.commit()
    return metrics


async def recent_reports(session: AsyncSession, days: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text("SELECT day, created_at, metrics FROM daily_reports ORDER BY day DESC LIMIT :n"),
            {"n": days},
        )
    ).all()
    return [{"day": r.day.isoformat(), "created_at": r.created_at.isoformat(), "metrics": r.metrics} for r in rows]


# ---- growth over a period, for bizmap's "Tăng trưởng dữ liệu" page ----

SERIES_DAYS = 14  # each brand's small per-day line
TOP_WARDS = 1000  # bizmap keeps only names in the official ward list


def _gain(end: dict[str, int] | None, start: dict[str, int] | None, key: str) -> int | None:
    """Stores of ``key`` gained between two snapshots, or None if one is missing."""
    if end is None or start is None:
        return None
    return end.get(key, 0) - start.get(key, 0)


def brand_growth(
    current: dict[str, int],
    snaps: dict[date, dict[str, int]],
    created: dict[str, dict[date, int]],
    created_before: dict[str, int],
    today: date,
    first: date,
    series_days: int = SERIES_DAYS,
) -> dict[str, dict[str, Any]]:
    """Per brand: stores at the start of the period, gained in it, and per day.

    A snapshot (``snaps[d]``: stores per brand when day ``d``'s report was
    built) counts a store the day it got the brand, whether it was found that
    day or long before. Where no snapshot is there (before reports began),
    the store counts on the day it entered the lake, under the brand it has now.
    """
    end_of = {**snaps, today: current}
    out: dict[str, dict[str, Any]] = {}
    for brand, now in current.items():
        start_snap = snaps.get(first - timedelta(days=1))
        start = start_snap.get(brand, 0) if start_snap is not None else created_before.get(brand, 0)
        series = []
        for i in range(series_days - 1, -1, -1):
            d = today - timedelta(days=i)
            g = _gain(end_of.get(d), snaps.get(d - timedelta(days=1)), brand)
            series.append(g if g is not None else created.get(brand, {}).get(d, 0))
        out[brand] = {"total": now, "start": start, "new": now - start, "series": series}
    return out


_GROWTH_SQL = """
WITH m AS MATERIALIZED (
  SELECT brand, province_code, openooh_category AS cat, openooh_subcategory AS sub,
         NULLIF(btrim(canonical_address_components->>'ward'), '') AS ward,
         (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d
  FROM master_pois WHERE status = 'active'
)
SELECT json_build_object(
  'active', (SELECT count(*) FROM m),
  'active_before', (SELECT count(*) FROM m WHERE d < :first),
  'branded_before', (SELECT count(*) FROM m WHERE d < :first AND brand IS NOT NULL),
  'daily', (SELECT coalesce(json_agg(x ORDER BY x.day), '[]') FROM (
      SELECT d AS day, count(*) AS total, count(brand) AS branded FROM m WHERE d >= :first GROUP BY d) x),
  'brands', (SELECT coalesce(json_agg(x), '[]') FROM (
      SELECT brand, count(*) AS total, count(*) FILTER (WHERE d < :first) AS before,
             array_agg(DISTINCT province_code) FILTER (WHERE province_code IS NOT NULL) AS provinces
      FROM m WHERE brand IS NOT NULL GROUP BY brand) x),
  'brand_days', (SELECT coalesce(json_agg(x), '[]') FROM (
      SELECT brand, d AS day, count(*) AS n FROM m
      WHERE brand IS NOT NULL AND d >= :series_from GROUP BY brand, d) x),
  'categories', (SELECT coalesce(json_agg(x), '[]') FROM (
      SELECT cat AS category, sub AS subcategory, count(*) AS total,
             count(*) FILTER (WHERE d >= :first) AS new, count(brand) AS branded
      FROM m GROUP BY cat, sub) x),
  'provinces', (SELECT coalesce(json_agg(x), '[]') FROM (
      SELECT province_code AS code, count(*) AS total,
             count(*) FILTER (WHERE d >= :first) AS new, count(brand) AS branded
      FROM m GROUP BY province_code) x),
  'wards', (SELECT coalesce(json_agg(x), '[]') FROM (
      SELECT province_code AS code, ward, count(*) AS total,
             count(*) FILTER (WHERE d >= :first) AS new, count(brand) AS branded
      FROM m WHERE ward IS NOT NULL GROUP BY province_code, ward
      HAVING count(*) FILTER (WHERE d >= :first) > 0
      ORDER BY 4 DESC LIMIT :top_wards) x)
)
"""


async def growth_report(session: AsyncSession, days: int, today: date | None = None) -> dict[str, Any]:
    """What the lake gained in the last ``days`` days (today included, Vietnam time)."""
    today = today or datetime.now(VN_TZ).date()
    first = today - timedelta(days=days - 1)
    series_from = today - timedelta(days=SERIES_DAYS - 1)
    g = (
        await session.execute(
            text(_GROWTH_SQL),
            {"first": first, "series_from": min(first, series_from), "top_wards": TOP_WARDS},
        )
    ).scalar_one()

    since = min(first, series_from) - timedelta(days=1)
    rows = (
        await session.execute(
            text("SELECT day, metrics->'brands' AS brands, metrics->'masters_active' AS active "
                 "FROM daily_reports WHERE day >= :s ORDER BY day"),
            {"s": since},
        )
    ).all()
    snaps = {r.day: {k: int(v) for k, v in (r.brands or {}).items()} for r in rows}
    start_snap = snaps.get(first - timedelta(days=1))
    start_row = next((r for r in rows if r.day == first - timedelta(days=1) and r.active is not None), None)

    created: dict[str, dict[date, int]] = {}
    for x in g["brand_days"]:
        created.setdefault(x["brand"], {})[date.fromisoformat(x["day"])] = int(x["n"])
    current = {b["brand"]: int(b["total"]) for b in g["brands"]}
    growth = brand_growth(
        current, snaps, created, {b["brand"]: int(b["before"]) for b in g["brands"]}, today, first
    )
    brands = [
        {"brand": b["brand"], **growth[b["brand"]], "provinces": sorted(b["provinces"] or [])}
        for b in g["brands"]
    ]
    branded = sum(current.values())

    new_brands = (
        await session.execute(
            text(
                """
                SELECT c.key, c.name, c.category, c.status, c.stores, c.decided_at,
                       (SELECT count(*) FROM master_pois p WHERE p.brand = c.name AND p.status = 'active') AS tagged
                FROM brand_candidates c
                WHERE c.status IN ('added', 'auto_added') AND c.decided_at >= :s
                ORDER BY c.decided_at DESC
                """
            ),
            {"s": day_bounds(first)[0]},
        )
    ).all()
    pending = await _scalar(session, "SELECT count(*) FROM brand_candidates WHERE status = 'pending'")

    return {
        "days": days,
        "from": first.isoformat(),
        "to": today.isoformat(),
        "totals": {
            "active": int(g["active"]),
            "active_start": int(start_row.active) if start_row is not None else int(g["active_before"]),
            "branded": branded,
            "branded_start": sum(start_snap.values()) if start_snap is not None else int(g["branded_before"]),
            "brands": len(current),
            "brands_start": len(start_snap) if start_snap is not None else sum(1 for b in g["brands"] if int(b["before"]) > 0),
        },
        "snapshots_from": rows[0].day.isoformat() if rows else None,
        "daily": [{"day": x["day"], "total": int(x["total"]), "branded": int(x["branded"])} for x in g["daily"]],
        "brand_counts": [{"day": r.day.isoformat(), "brands": len(snaps[r.day])} for r in rows if r.day >= first - timedelta(days=1)],
        "brands": brands,
        "categories": g["categories"],
        "provinces": g["provinces"],
        "wards": g["wards"],
        "new_brands": [
            {
                "key": n.key, "name": n.name, "category": n.category, "auto": n.status == "auto_added",
                "found": int(n.stores), "tagged": int(n.tagged), "decided_at": n.decided_at.isoformat(),
            }
            for n in new_brands
        ],
        "pending_brands": pending,
    }
