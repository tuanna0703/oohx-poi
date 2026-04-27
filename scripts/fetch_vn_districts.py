"""Populate ``admin_units`` (level=2 districts) for every VN province.

Two-stage approach:

1. Fetch the canonical district list per province from
   ``provinces.open-api.vn`` (codes + names + division_type).
2. For each district, look up its bounding box via Nominatim using a
   structured query like "Quận Hoàn Kiếm, Hà Nội, Việt Nam".

Why not OSM Overpass directly? VN OSM tags ``admin_level=6`` on a mix of
huyện AND xã (mistagged) and the boundaries of border provinces also
catch Chinese counties — neither bbox-based nor area-based Overpass
queries give clean district data.

Idempotent — re-runs only insert/update; existing rows are not deleted.

Run on the VPS once after deploy:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.caddy.yml \\
        run --rm api python scripts/fetch_vn_districts.py

Throttled (1.1s/Nominatim request) per the public-instance fair use
policy, so the full crawl takes ~12 minutes for ~700 districts.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from typing import Any

import httpx

from poi_lake.db import session_scope
from sqlalchemy import text

PROVINCES_API = "https://provinces.open-api.vn/api/v1/p/{code}?depth=2"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "poi-lake/0.1 (admin-units seeder; ops@oohx.net)"

# Provinces whose districts the seed file already ships — skip.
SKIP_PROVINCES = {"01", "79", "48"}

# provinces.open-api.vn uses 1-2 digit numeric codes for provinces;
# our admin_units uses GSO 2-digit zero-padded. Convert with int -> "%02d".

logger = logging.getLogger("fetch_vn_districts")


def _gso_code(api_code: int) -> str:
    """provinces.open-api.vn uses int codes; we store GSO 2-digit zero-padded."""
    return f"{int(api_code):02d}"


def _district_code(province_gso: str, district_api_code: int) -> str:
    """``<province>.<3-digit district code>`` — same shape as the seed file."""
    return f"{province_gso}.{int(district_api_code):03d}"


def _fetch_districts_for_province(prov_code: str) -> list[dict[str, Any]]:
    """Hit the provinces API once per province, return the district list."""
    url = PROVINCES_API.format(code=int(prov_code))
    try:
        resp = httpx.get(
            url, follow_redirects=True, timeout=20,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("  provinces-api failed for %s: %s", prov_code, exc)
        return []
    return resp.json().get("districts", []) or []


def _normalize_query(district_name: str, province_name: str) -> str:
    """Build a Nominatim query string. Strip any leading division-type prefix
    so ``Quận Ba Đình`` becomes ``Ba Đình`` (Nominatim handles either, but
    this matches better in some edge cases)."""
    return f"{district_name}, {province_name}, Việt Nam"


def _bbox_via_nominatim(query: str, retries: int = 3) -> list[float] | None:
    """Single Nominatim search with simple retry on 429 / 5xx."""
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "vn",
        "addressdetails": 0,
    }
    for attempt in range(retries):
        try:
            resp = httpx.get(
                NOMINATIM_URL, params=params, timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 5 * (2 ** attempt)
                logger.warning("    nominatim %s; retry in %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            arr = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("    nominatim error: %s", exc)
            return None
        if not arr:
            return None
        bb = arr[0].get("boundingbox")
        if not bb or len(bb) != 4:
            return None
        # Nominatim returns [lat_min, lat_max, lng_min, lng_max] as strings.
        try:
            lat_min, lat_max, lng_min, lng_max = (float(x) for x in bb)
        except (ValueError, TypeError):
            return None
        return [lng_min, lat_min, lng_max, lat_max]
    return None


async def _existing_provinces() -> list[tuple[str, str]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                text("SELECT code, name FROM admin_units WHERE level = 1 ORDER BY code")
            )
        ).all()
    return [(r[0], r[1]) for r in rows]


async def _existing_district_count(parent: str) -> int:
    async with session_scope() as session:
        n = (
            await session.execute(
                text("SELECT COUNT(*) FROM admin_units WHERE level=2 AND parent_code=:p"),
                {"p": parent},
            )
        ).scalar_one()
    return int(n)


async def _upsert_district(
    code: str, name: str, parent: str, bbox: list[float]
) -> None:
    lng_min, lat_min, lng_max, lat_max = bbox
    async with session_scope() as session:
        await session.execute(
            text(
                """
                INSERT INTO admin_units
                    (code, name, parent_code, level,
                     lng_min, lat_min, lng_max, lat_max)
                VALUES
                    (:code, :name, :parent, 2,
                     :lng_min, :lat_min, :lng_max, :lat_max)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    lng_min = EXCLUDED.lng_min,
                    lat_min = EXCLUDED.lat_min,
                    lng_max = EXCLUDED.lng_max,
                    lat_max = EXCLUDED.lat_max
                """
            ),
            {
                "code": code,
                "name": name,
                "parent": parent,
                "lng_min": lng_min,
                "lat_min": lat_min,
                "lng_max": lng_max,
                "lat_max": lat_max,
            },
        )
        await session.commit()


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    provinces = await _existing_provinces()
    logger.info("found %d provinces in admin_units", len(provinces))

    total_inserted = 0
    total_failed = 0
    for code, name in provinces:
        if code in SKIP_PROVINCES:
            logger.info("skip %s (%s) — already seeded by seed file", code, name)
            continue
        existing = await _existing_district_count(code)
        if existing > 0:
            logger.info("skip %s (%s) — already has %d districts", code, name, existing)
            continue

        api_districts = _fetch_districts_for_province(code)
        if not api_districts:
            logger.warning("%s (%s): provinces-api returned no districts", code, name)
            continue

        logger.info("fetching %d districts for %s (%s) via Nominatim …",
                    len(api_districts), code, name)
        for d in api_districts:
            d_name = d.get("name") or ""
            d_api_code = d.get("code")
            if not d_name or d_api_code is None:
                continue
            d_code = _district_code(code, d_api_code)
            query = _normalize_query(d_name, name)
            bbox = _bbox_via_nominatim(query)
            if not bbox:
                logger.warning("  miss: %s (no bbox from Nominatim)", d_name)
                total_failed += 1
                # Polite delay even on miss.
                time.sleep(1.1)
                continue
            await _upsert_district(d_code, d_name, code, bbox)
            total_inserted += 1
            time.sleep(1.1)
        logger.info("  done %s — running totals: ok=%d miss=%d",
                    name, total_inserted, total_failed)

    logger.info("=== finished: %d districts inserted, %d failed ===",
                total_inserted, total_failed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
