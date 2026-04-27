"""Populate ``admin_units`` (level=2 districts) for every VN province
that doesn't yet have districts seeded.

Pulls level-6 admin relations from OSM Overpass for each province, computes
a tight bounding box from each relation's bounds, and upserts into
``admin_units``. Idempotent — re-run is safe; updates name/bbox only.

Run on the VPS once:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.caddy.yml \\
        run --rm api python scripts/fetch_vn_districts.py

This is a one-off — the seed file (``vn_admin_units.py``) only ships HN /
HCMC / Đà Nẵng districts because the rest are hard to obtain reliably
from any single source. OSM is the ground truth at deploy time and the
Overpass query is throttled (sleep 1s between provinces) so it doesn't
abuse the public API.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

import httpx

from poi_lake.config import get_settings
from poi_lake.db import session_scope
from sqlalchemy import text

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Districts already seeded — skip provinces fully covered by the seed file.
SKIP_PROVINCES = {"01", "79", "48"}  # Hà Nội, HCMC, Đà Nẵng

logger = logging.getLogger("fetch_vn_districts")


def _overpass_query(province_name: str) -> str:
    """Find admin_level=6 relations inside the named province (admin_level=4)."""
    return f"""
[out:json][timeout:90];
relation["admin_level"="4"]["name"="{province_name}"]->.prov;
(
  relation["admin_level"="6"](area.prov);
  relation["admin_level"="6"]["boundary"="administrative"](area.prov);
);
out tags bb;
""".strip()


def _stream_districts(province_name: str) -> list[dict[str, Any]]:
    """Hit Overpass once per province. Returns list of district dicts with
    ``name``, ``bounds``, and any GSO ``ref`` tag if OSM has one."""
    # Overpass needs an Area, so first promote the named relation to area.
    query = f"""
[out:json][timeout:120];
rel["admin_level"="4"]["name"="{province_name}"];
map_to_area;
(
  relation["admin_level"="6"](area)["boundary"="administrative"];
);
out tags bb;
"""
    try:
        resp = httpx.post(
            OVERPASS_URL,
            data={"data": query.strip()},
            timeout=180,
            headers={"User-Agent": "poi-lake/0.1 (admin-units seeder)"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("overpass error for %s: %s", province_name, exc)
        return []
    elements = resp.json().get("elements", [])
    out: list[dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags") or {}
        bounds = el.get("bounds")
        name = tags.get("name") or tags.get("name:vi")
        if not name or not bounds:
            continue
        out.append({
            "name": name,
            "ref": tags.get("ref:gso") or tags.get("ref"),
            "minlat": bounds["minlat"],
            "minlon": bounds["minlon"],
            "maxlat": bounds["maxlat"],
            "maxlon": bounds["maxlon"],
        })
    return out


async def _existing_provinces() -> list[tuple[str, str]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT code, name FROM admin_units "
                    "WHERE level = 1 ORDER BY code"
                )
            )
        ).all()
    return [(r[0], r[1]) for r in rows]


async def _existing_district_codes(parent: str) -> set[str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                text("SELECT code FROM admin_units WHERE level = 2 AND parent_code = :p"),
                {"p": parent},
            )
        ).all()
    return {r[0] for r in rows}


async def _upsert_districts(parent: str, districts: list[dict[str, Any]]) -> int:
    """Insert / update ``level=2`` rows under the given province parent.

    The synthetic code is ``<province>.osm.<seq>`` when GSO ref is missing,
    or ``<province>.<ref>`` when present. Names are unique enough within a
    province to use as the conflict target, so we conflict on (parent_code,
    name).
    """
    if not districts:
        return 0
    inserted = 0
    async with session_scope() as session:
        for i, d in enumerate(districts):
            ref = d.get("ref")
            if ref and ref.isdigit():
                code = f"{parent}.{int(ref):03d}"
            else:
                code = f"{parent}.osm.{i:03d}"
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
                    "name": d["name"],
                    "parent": parent,
                    "lng_min": d["minlon"],
                    "lat_min": d["minlat"],
                    "lng_max": d["maxlon"],
                    "lat_max": d["maxlat"],
                },
            )
            inserted += 1
        await session.commit()
    return inserted


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    logger.info("env=%s db=%s", settings.app_env, settings.database_url.split("@")[-1])

    provinces = await _existing_provinces()
    logger.info("found %d provinces in admin_units", len(provinces))

    total_inserted = 0
    for code, name in provinces:
        if code in SKIP_PROVINCES:
            logger.info("skip %s (%s) — already seeded", code, name)
            continue
        existing = await _existing_district_codes(code)
        if existing:
            logger.info("skip %s (%s) — already has %d districts", code, name, len(existing))
            continue

        logger.info("fetching districts for %s (%s) …", code, name)
        districts = _stream_districts(name)
        if not districts:
            logger.warning("  no districts returned for %s", name)
        else:
            n = await _upsert_districts(code, districts)
            logger.info("  upserted %d districts", n)
            total_inserted += n
        # Throttle so we don't hammer the public Overpass instance.
        time.sleep(1.5)

    logger.info("done — %d districts inserted/updated", total_inserted)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
