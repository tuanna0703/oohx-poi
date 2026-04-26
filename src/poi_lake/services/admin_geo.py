"""Spatial lookup: (lat, lng) → (province_code, district_code, ward_code).

Bbox-based approximation. We don't carry full polygons because:

  * Bboxes are enough for stamping a POI with its containing province /
    district. Points near a province border may pick the neighbour, but
    the worst case is a ~5% misattribution rate which is acceptable for
    aggregation queries.
  * Bbox-only storage keeps the lookup query a plain B-tree range scan
    — no PostGIS calls, no per-row geometry parsing. Fast enough to run
    inside the normalize pipeline for every record.

When two units' bboxes overlap a point (common at borders), we prefer
the smaller bbox per level — that's usually the more specific district
inside the broader province.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True, frozen=True)
class AdminLookup:
    province_code: str | None
    district_code: str | None
    ward_code: str | None


_LOOKUP_SQL = text(
    """
    WITH matches AS (
      SELECT
        level, code,
        (lng_max - lng_min) * (lat_max - lat_min) AS area
      FROM admin_units
      WHERE :lng BETWEEN lng_min AND lng_max
        AND :lat BETWEEN lat_min AND lat_max
    )
    SELECT
      (SELECT code FROM matches WHERE level = 1 ORDER BY area ASC LIMIT 1) AS province_code,
      (SELECT code FROM matches WHERE level = 2 ORDER BY area ASC LIMIT 1) AS district_code,
      (SELECT code FROM matches WHERE level = 3 ORDER BY area ASC LIMIT 1) AS ward_code
    """
)


async def lookup_admin(session: AsyncSession, lat: float, lng: float) -> AdminLookup:
    """Return the smallest-bbox admin unit per level containing the point."""
    row = (
        await session.execute(_LOOKUP_SQL, {"lat": float(lat), "lng": float(lng)})
    ).one()
    return AdminLookup(
        province_code=row.province_code,
        district_code=row.district_code,
        ward_code=row.ward_code,
    )
