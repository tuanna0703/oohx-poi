"""One-shot backfill: stamp province/district/ward codes on existing
processed_pois and master_pois that pre-date the geo columns.

Idempotent — running it twice is a no-op (rows that already have a
province_code are skipped).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from poi_lake.db import session_scope
from poi_lake.observability import configure_logging

logger = logging.getLogger(__name__)


_PROCESSED_BACKFILL = text(
    """
    WITH points AS (
      SELECT id, ST_X(location::geometry) AS lng, ST_Y(location::geometry) AS lat
      FROM processed_pois WHERE province_code IS NULL
    ),
    matches AS (
      SELECT
        p.id AS proc_id, a.level, a.code,
        (a.lng_max - a.lng_min) * (a.lat_max - a.lat_min) AS area
      FROM points p
      JOIN admin_units a
        ON p.lng BETWEEN a.lng_min AND a.lng_max
       AND p.lat BETWEEN a.lat_min AND a.lat_max
    ),
    picked AS (
      SELECT
        proc_id,
        (ARRAY_AGG(code ORDER BY area ASC) FILTER (WHERE level = 1))[1] AS province_code,
        (ARRAY_AGG(code ORDER BY area ASC) FILTER (WHERE level = 2))[1] AS district_code,
        (ARRAY_AGG(code ORDER BY area ASC) FILTER (WHERE level = 3))[1] AS ward_code
      FROM matches GROUP BY proc_id
    )
    UPDATE processed_pois p
       SET province_code = picked.province_code,
           district_code = picked.district_code,
           ward_code     = picked.ward_code
      FROM picked
     WHERE p.id = picked.proc_id
    """
)

_MASTER_BACKFILL = text(
    """
    WITH points AS (
      SELECT id, ST_X(location::geometry) AS lng, ST_Y(location::geometry) AS lat
      FROM master_pois WHERE province_code IS NULL
    ),
    matches AS (
      SELECT
        p.id AS master_id, a.level, a.code,
        (a.lng_max - a.lng_min) * (a.lat_max - a.lat_min) AS area
      FROM points p
      JOIN admin_units a
        ON p.lng BETWEEN a.lng_min AND a.lng_max
       AND p.lat BETWEEN a.lat_min AND a.lat_max
    ),
    picked AS (
      SELECT
        master_id,
        (ARRAY_AGG(code ORDER BY area ASC) FILTER (WHERE level = 1))[1] AS province_code,
        (ARRAY_AGG(code ORDER BY area ASC) FILTER (WHERE level = 2))[1] AS district_code,
        (ARRAY_AGG(code ORDER BY area ASC) FILTER (WHERE level = 3))[1] AS ward_code
      FROM matches GROUP BY master_id
    )
    UPDATE master_pois m
       SET province_code = picked.province_code,
           district_code = picked.district_code,
           ward_code     = picked.ward_code
      FROM picked
     WHERE m.id = picked.master_id
    """
)


async def main() -> None:
    configure_logging()
    async with session_scope() as s:
        proc_result = await s.execute(_PROCESSED_BACKFILL)
        master_result = await s.execute(_MASTER_BACKFILL)
        await s.commit()
    print(f"processed_pois updated: {proc_result.rowcount}")
    print(f"master_pois updated:    {master_result.rowcount}")


if __name__ == "__main__":
    asyncio.run(main())
