"""Six economic-administrative regions of Vietnam (Tổng cục Thống kê).

Used by the Ingestion Jobs UI / tile endpoint to let an operator pick
"Đồng bằng sông Hồng" with one click instead of multiselecting every
province in it. Regions are NOT stored in ``admin_units`` — they're a
front-of-house grouping over level-1 provinces, expanded to the
underlying province codes at request time.

Province codes follow GSO 2-digit standard (same as
``poi_lake.seeds.vn_admin_units.PROVINCES``).
"""

from __future__ import annotations

from typing import TypedDict


class Region(TypedDict):
    code: str
    name: str
    short_name: str
    province_codes: list[str]


# Order matches the conventional GSO "north → south" numbering.
REGIONS: list[Region] = [
    {
        "code": "R1",
        "name": "Trung du và miền núi phía Bắc",
        "short_name": "Northern midlands & mountains",
        "province_codes": [
            "02", "04", "06", "08", "10", "11", "12", "14",
            "15", "17", "19", "20", "24", "25",
        ],
    },
    {
        "code": "R2",
        "name": "Đồng bằng sông Hồng",
        "short_name": "Red River Delta",
        "province_codes": [
            "01", "22", "26", "27", "30", "31", "33", "34",
            "35", "36", "37",
        ],
    },
    {
        "code": "R3",
        "name": "Bắc Trung Bộ và Duyên hải miền Trung",
        "short_name": "North Central & Central coast",
        "province_codes": [
            "38", "40", "42", "44", "45", "46", "48", "49",
            "51", "52", "54", "56", "58", "60",
        ],
    },
    {
        "code": "R4",
        "name": "Tây Nguyên",
        "short_name": "Central Highlands",
        "province_codes": ["62", "64", "66", "67", "68"],
    },
    {
        "code": "R5",
        "name": "Đông Nam Bộ",
        "short_name": "Southeast",
        "province_codes": ["70", "72", "74", "75", "77", "79"],
    },
    {
        "code": "R6",
        "name": "Đồng bằng sông Cửu Long",
        "short_name": "Mekong Delta",
        "province_codes": [
            "80", "82", "83", "84", "86", "87", "89", "91",
            "92", "93", "94", "95", "96",
        ],
    },
]


REGIONS_BY_CODE: dict[str, Region] = {r["code"]: r for r in REGIONS}


def expand_region_codes(region_codes: list[str]) -> list[str]:
    """Return the union of province codes covered by the given regions.

    Unknown region codes are skipped silently so the caller can pass a
    mixed bag (regions + provinces) without pre-validating. Order is
    preserved by region order, with duplicates removed.
    """
    seen: dict[str, None] = {}
    for rc in region_codes:
        region = REGIONS_BY_CODE.get(rc)
        if not region:
            continue
        for pc in region["province_codes"]:
            seen.setdefault(pc, None)
    return list(seen.keys())
