"""The 2025 wards and communes, and which one a point is in.

Since 2025-07-01 Vietnam has 34 provinces and 3,321 wards, communes and
special zones. A POI's ``ward_code`` is its ward in that list, the General
Statistics Office's code (as provinces.open-api.vn v2 has it: 20275 is
Phường An Hải, Đà Nẵng), found from its point against the wards'
boundaries: never from the address, which mostly still names a pre-2025
ward ("Phường 10") or district. A point in no ward (offshore, abroad)
gets none; one on a boundary gets either.

The boundaries: the GeoJSON export of thanglequoc/vietnamese-provinces-
database (MIT), derived from the reference map of the 2025 arrangement
published by the Ministry of Agriculture and Environment's cartography
house (sapnhap.bando.com.vn). ``SOURCE_URL`` pins one commit of it and
``SOURCE_SHA256`` its archive; ``scripts/load_admin_wards.py`` loads it.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SOURCE_COMMIT = "8b78ba5118715e1fa81769286724db79346abf52"
SOURCE_URL = (
    "https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/"
    f"{SOURCE_COMMIT}/json/vn_provinces_wards_geojson.zip"
)
SOURCE_SHA256 = "3e44d87ec775fea9159b2d8625add8d81aa7ea1dc8fe0d89b772b34d488353ee"
SOURCE = f"thanglequoc/vietnamese-provinces-database@{SOURCE_COMMIT[:12]}"
EXPECTED_WARDS = 3321
EXPECTED_PROVINCES = 34
# Pieces of at most this many vertices: a lookup reads a few short rings.
SUBDIVIDE_VERTICES = 128

# geojson/48_da_nang/48_da_nang.geojson, geojson/48_da_nang/wards/20275_an_hai.geojson
_PROVINCE_FILE = re.compile(r"(?:^|/)(\d{2})_[^/]+/\1_[^/]+\.geojson$")
_WARD_FILE = re.compile(r"(?:^|/)(\d{2})_[^/]+/wards/(\d{5})_[^/]+\.geojson$")


@dataclass(frozen=True, slots=True)
class Ward:
    code: str
    name: str
    full_name: str
    province_code: str
    province_name: str
    geometry: str  # GeoJSON (Multi)Polygon


def _feature(raw: bytes) -> dict:
    d = json.loads(raw)
    if d.get("type") == "FeatureCollection":
        feats = d.get("features") or []
        if len(feats) != 1:
            raise ValueError(f"{len(feats)} features, one expected")
        d = feats[0]
    return d


def parse_ward(raw: bytes, province_code: str, province_name: str, file_code: str) -> Ward:
    """One ward file of the export; raises ``ValueError`` when it is not one."""
    f = _feature(raw)
    props = f.get("properties") or {}
    code = str(props.get("code") or f.get("id") or "")
    if code != file_code:
        raise ValueError(f"code {code!r}, file says {file_code!r}")
    geom = f.get("geometry") or {}
    if geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{code}: geometry {geom.get('type')!r}")
    name = str(props.get("name") or "").strip()
    if not name:
        raise ValueError(f"{code}: no name")
    return Ward(
        code=code,
        name=name,
        full_name=str(props.get("fullName") or name).strip(),
        province_code=province_code,
        province_name=province_name,
        geometry=json.dumps(geom, separators=(",", ":")),
    )


def read_archive(fp: IO[bytes]) -> Iterator[Ward]:
    """Every ward of the export's zip, one file at a time; raises
    ``ValueError`` when the archive is not the whole list."""
    with zipfile.ZipFile(fp) as z:
        names = z.namelist()
        provinces: dict[str, str] = {}
        for n in names:
            m = _PROVINCE_FILE.search(n)
            if m:
                props = _feature(z.read(n)).get("properties") or {}
                provinces[m.group(1)] = str(props.get("name") or "").strip()
        if len(provinces) != EXPECTED_PROVINCES:
            raise ValueError(f"{len(provinces)} provinces, {EXPECTED_PROVINCES} expected")
        seen: set[str] = set()
        for n in names:
            m = _WARD_FILE.search(n)
            if not m:
                continue
            prov, code = m.groups()
            if prov not in provinces:
                raise ValueError(f"{n}: no province {prov}")
            if code in seen:
                raise ValueError(f"ward {code} twice")
            seen.add(code)
            yield parse_ward(z.read(n), prov, provinces[prov], code)
        if len(seen) != EXPECTED_WARDS:
            raise ValueError(f"{len(seen)} wards, {EXPECTED_WARDS} expected")


_INSERT_WARD = text(
    """
    INSERT INTO admin_wards (code, name, full_name, province_code, province_name, source, geom)
    VALUES (:code, :name, :full_name, :prov, :prov_name, :source,
            ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)), 3)))
    """
)
_INSERT_PARTS = text(
    f"""
    INSERT INTO admin_ward_parts (code, geom)
    SELECT w.code, d.geom
      FROM admin_wards w,
           LATERAL ST_Subdivide(w.geom, {SUBDIVIDE_VERTICES}) AS s(geom),
           LATERAL ST_Dump(ST_CollectionExtract(s.geom, 3)) AS d
     WHERE ST_GeometryType(d.geom) = 'ST_Polygon'
    """
)


async def load(session: AsyncSession, wards: Iterator[Ward]) -> int:
    """Replace the wards and their pieces with ``wards``, in the caller's
    transaction; the number loaded."""
    await session.execute(text("DELETE FROM admin_ward_parts"))
    await session.execute(text("DELETE FROM admin_wards"))
    n = 0
    for w in wards:
        await session.execute(_INSERT_WARD, {
            "code": w.code, "name": w.name, "full_name": w.full_name,
            "prov": w.province_code, "prov_name": w.province_name,
            "source": SOURCE, "geom": w.geometry,
        })
        n += 1
    await session.execute(_INSERT_PARTS)
    return n


# The ward a point is in; the lowest code when it is on a boundary, so the
# answer does not change from one run to the next.
WARD_AT_SQL = """
    SELECT p.code FROM admin_ward_parts p
     WHERE ST_Intersects(p.geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326))
     ORDER BY p.code LIMIT 1
"""
_WARD_AT = text(WARD_AT_SQL)


async def ward_at(session: AsyncSession, lat: float, lng: float) -> str | None:
    """The 2025 ward code at a point, or None."""
    return (await session.execute(_WARD_AT, {"lat": float(lat), "lng": float(lng)})).scalar()
