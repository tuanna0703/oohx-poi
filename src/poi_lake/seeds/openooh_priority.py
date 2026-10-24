"""Priority ordering for OpenOOH codes during the gosom crawl sweep.

Lower number = crawled first. The planner orders ``crawl_plan`` rows by
``priority ASC, last_attempt_at NULLS FIRST``, so this biases the queue toward
the categories OOHX and bizmap.live use most: the chains people walk into
every day (convenience, grocery, pharmacy, electronics, coffee, fast food,
banks) and the venues that carry DOOH screens.

Every code here must be a real ``openooh_categories`` code. Until 2026-10-24
several were not (``health_and_beauty.pharmacies`` for ``retail.pharmacy``,
``hospitality.coffee_shops`` for ``hospitality.cafes`` …), so pharmacies and
cafés silently fell to the default and queued behind bars and dentists.

Codes not listed get ``DEFAULT_PRIORITY`` — after tiers 1 and 2, before tier 3.
``POST /admin/crawl-plan/reprioritize`` re-applies this map to existing rows
without touching their progress.
"""

from __future__ import annotations

# Tier 1 — chains and everyday foot traffic; what bizmap shows first.
TIER_1: list[str] = [
    "retail.convenience_stores",
    "retail.grocery",
    "retail.pharmacy",
    "retail.electronics",
    "hospitality.cafes",
    "hospitality.fast_food",
    "financial.banks",
    "financial.atms",
    "retail.shopping_malls",
    "retail.gas_stations",
    "hospitality.restaurants",
    "retail.apparel",
]

# Tier 2 — meaningful DOOH venues, second pass.
TIER_2: list[str] = [
    "point_of_care.hospitals",
    "point_of_care.doctor_offices",
    "point_of_care.dentist_offices",
    "health_and_beauty.salons",
    "health_and_beauty.spas",
    "health_and_beauty.barbers",
    "sports_and_fitness.gyms",
    "education.schools",
    "education.early_learning",
    "education.colleges_universities",
    "travel.hotels",
    "travel.hostels",
    "hospitality.bars",
    "entertainment.nightclubs",
    "entertainment.cinema",
    "transit.airports",
    "transit.bus_stations",
    "residential.apartment_buildings",
    "retail.dealerships",
    "retail.liquor_stores",
]

# Tier 3 — long tail and the broad level-1 codes, whose keywords overlap the
# subcategories above.
TIER_3: list[str] = [
    "retail",
    "hospitality",
    "health_and_beauty",
    "point_of_care",
    "financial",
    "education",
    "entertainment",
    "leisure",
    "travel",
    "sports_and_fitness",
    "transit",
    "residential",
    "office_buildings",
    "government",
    "outdoor",
    "roadside",
    "industrial",
    "farm",
]

DEFAULT_PRIORITY = 300


def priority_map() -> dict[str, int]:
    """Return ``{openooh_code: priority}`` — lower number = sooner.

    Tier 1 starts at 10, tier 2 at 100, tier 3 at 500; unlisted codes get
    ``DEFAULT_PRIORITY`` (between tiers 2 and 3).
    """
    out: dict[str, int] = {}
    for i, code in enumerate(TIER_1):
        out[code] = 10 + i
    for i, code in enumerate(TIER_2):
        out[code] = 100 + i
    for i, code in enumerate(TIER_3):
        out[code] = 500 + i
    return out


def priority_for(openooh_code: str, default: int = DEFAULT_PRIORITY) -> int:
    return priority_map().get(openooh_code, default)
