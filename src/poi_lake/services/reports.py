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
