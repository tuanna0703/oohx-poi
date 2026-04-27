"""Priority ordering for OpenOOH codes during the gosom crawl sweep.

Lower number = crawled first. The planner orders ``crawl_plan`` rows by
``priority ASC, last_attempt_at NULLS FIRST``, so this lets us bias the
queue toward high-DOOH-value categories (retail / hospitality /
healthcare / financial — the chains DOOH advertisers actually care about)
before chewing through long-tail categories like ``industrial.farm``.

Codes not listed here get the default priority 100 (after every explicit
entry but before anything skipped). Add a code with priority < 0 to push
it to the very top.
"""

from __future__ import annotations

# Tier 1 — high commercial density, top priority.
TIER_1: list[str] = [
    # Retail chains & supermarkets — foundational for DOOH inventory.
    "retail.convenience_stores",
    "retail.grocery",
    "retail.shopping_malls",
    "retail.cafes",
    "retail.fashion",
    # Hospitality — restaurants + bars, very high foot traffic.
    "hospitality.casual_dining",
    "hospitality.quick_service_restaurants",
    "hospitality.coffee_shops",
    "hospitality.bars",
    "hospitality.hotels",
    # Healthcare — banks of regulated foot traffic.
    "point_of_care.hospitals",
    "point_of_care.urgent_care_clinics",
    "point_of_care.dentist_offices",
    "health_and_beauty.pharmacies",
    # Financial.
    "financial.banks",
    "financial.atms",
]

# Tier 2 — meaningful DOOH targets, second pass.
TIER_2: list[str] = [
    # More retail.
    "retail.gas_stations",
    "retail.electronics",
    "retail.home_improvement",
    "retail.bookstores",
    "retail.pet_stores",
    # Education.
    "education.colleges_universities",
    "education.schools",
    "education.daycare",
    # Transit anchors.
    "transit.airports",
    "transit.gas_stations",
    "transit.subway",
    "transit.rail",
    "transit.bus_stations",
    # Entertainment + leisure.
    "entertainment.cinemas",
    "entertainment.casinos",
    "leisure.parks",
    # Sports.
    "sports_and_fitness.gyms",
    "sports_and_fitness.stadiums",
    # Government anchors.
    "government.post_offices",
]

# Tier 3 — long tail; crawl after Tier 1+2 are mostly done.
TIER_3: list[str] = [
    # Travel + auxiliary.
    "travel.travel_agencies",
    "office_buildings.office_buildings",
    "residential.residential",
    "outdoor.outdoor",
    "roadside.roadside",
    # Industrial / farm — typically low POI density anyway.
    "industrial.industrial",
    "farm.farm",
]


def priority_map() -> dict[str, int]:
    """Return ``{openooh_code: priority}`` — lower number = sooner.

    Tier 1 starts at 10, tier 2 at 200, tier 3 at 500. Codes not present
    use the planner's default 100 (i.e. between tier 1 and tier 2).
    """
    out: dict[str, int] = {}
    for i, code in enumerate(TIER_1):
        out[code] = 10 + i
    for i, code in enumerate(TIER_2):
        out[code] = 200 + i
    for i, code in enumerate(TIER_3):
        out[code] = 500 + i
    return out


def priority_for(openooh_code: str, default: int = 100) -> int:
    return priority_map().get(openooh_code, default)
