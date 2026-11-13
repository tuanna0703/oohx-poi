"""Stamp the 2025 ward (``ward_code``) on processed and master POIs from
their points, after ``scripts/load_admin_wards.py``.

    python scripts/backfill_ward_codes.py [--dry-run] [--batch 5000]

In batches, each its own short transaction (a long one starves the dedupe
queue). Rows whose ward is already right are left alone, so it can run
again after a reload of the boundaries; a point in no ward gets NULL.
``updated_at`` and ``version`` are left alone: the ward is derived from
the point, not a change to the place.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from poi_lake.db import session_scope
from poi_lake.observability import configure_logging

logger = logging.getLogger(__name__)

_WARD = """
    (SELECT p.code FROM admin_ward_parts p
      WHERE ST_Intersects(p.geom, t.location::geometry)
      ORDER BY p.code LIMIT 1)
"""


def _batch_sql(table: str, key: str, key_type: str, dry_run: bool) -> str:
    picked = f"""
      WITH batch AS (
        SELECT t.{key} AS k, {_WARD} AS ward
          FROM {table} t
         WHERE t.{key} > CAST(CAST(:after AS text) AS {key_type})
         ORDER BY t.{key} LIMIT :n
      )
    """
    if dry_run:
        return picked + """
      SELECT (SELECT k::text FROM batch ORDER BY k DESC LIMIT 1) AS last, COUNT(*) AS seen,
             COUNT(*) FILTER (WHERE ward IS NOT NULL) AS found,
             COUNT(*) FILTER (WHERE x.ward_code IS DISTINCT FROM batch.ward) AS changed
        FROM batch JOIN """ + f"{table} x ON x.{key} = batch.k"
    return picked + f""",
      upd AS (
        UPDATE {table} x SET ward_code = batch.ward
          FROM batch
         WHERE x.{key} = batch.k AND x.ward_code IS DISTINCT FROM batch.ward
        RETURNING 1
      )
      SELECT (SELECT k::text FROM batch ORDER BY k DESC LIMIT 1) AS last, (SELECT COUNT(*) FROM batch) AS seen,
             (SELECT COUNT(*) FROM batch WHERE ward IS NOT NULL) AS found,
             (SELECT COUNT(*) FROM upd) AS changed
    """


async def _run(table: str, key: str, key_type: str, start: str, n: int, dry_run: bool) -> None:
    sql = text(_batch_sql(table, key, key_type, dry_run))
    after = start
    seen = found = changed = 0
    while True:
        async with session_scope() as s:
            row = (await s.execute(sql, {"after": after, "n": n})).one()
            await s.commit()
        if not row.seen:
            break
        seen, found, changed = seen + row.seen, found + row.found, changed + row.changed
        after = row.last
        if seen % (n * 20) == 0:
            logger.info("%s: %d read, %d in a ward, %d changed", table, seen, found, changed)
    verb = "would change" if dry_run else "changed"
    print(f"{table}: {seen} read, {found} in a ward, {seen - found} in none, {changed} {verb}")


async def main() -> None:
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=5000)
    args = ap.parse_args()
    async with session_scope() as s:
        wards = (await s.execute(text("SELECT COUNT(*) FROM admin_wards"))).scalar()
    if not wards:
        raise SystemExit("admin_wards is empty: run scripts/load_admin_wards.py first")
    await _run("master_pois", "id", "uuid", "00000000-0000-0000-0000-000000000000", args.batch, args.dry_run)
    await _run("processed_pois", "id", "bigint", "0", args.batch, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
