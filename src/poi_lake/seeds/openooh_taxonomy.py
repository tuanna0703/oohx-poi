"""OpenOOH Venue Taxonomy v1.1 — top-level + common sub-categories.

Full spec: https://github.com/openooh/venue-taxonomy-specification

We seed the 18 top-level categories + a useful subset of Vietnam-relevant
subcategories. Additional subcategories can be added in later migrations
without breaking foreign-key references (CategoryMapper in pipeline defaults
to the top-level when a sub isn't known yet).
"""

from __future__ import annotations

from typing import TypedDict


class CategoryRow(TypedDict):
    code: str
    name: str
    parent_code: str | None
    level: int


TAXONOMY: list[CategoryRow] = [
    # ---- 18 top-level categories (level 1) ----
    {"code": "transit", "name": "Transit", "parent_code": None, "level": 1},
    {"code": "retail", "name": "Retail", "parent_code": None, "level": 1},
    {"code": "outdoor", "name": "Outdoor", "parent_code": None, "level": 1},
    {"code": "roadside", "name": "Roadside", "parent_code": None, "level": 1},
    {"code": "education", "name": "Education", "parent_code": None, "level": 1},
    {"code": "health_and_beauty", "name": "Health & Beauty", "parent_code": None, "level": 1},
    {"code": "point_of_care", "name": "Point of Care", "parent_code": None, "level": 1},
    {"code": "entertainment", "name": "Entertainment", "parent_code": None, "level": 1},
    {"code": "leisure", "name": "Leisure", "parent_code": None, "level": 1},
    {"code": "government", "name": "Government", "parent_code": None, "level": 1},
    {"code": "office_buildings", "name": "Office Buildings", "parent_code": None, "level": 1},
    {"code": "residential", "name": "Residential", "parent_code": None, "level": 1},
    {"code": "financial", "name": "Financial", "parent_code": None, "level": 1},
    {"code": "travel", "name": "Travel", "parent_code": None, "level": 1},
    {"code": "sports_and_fitness", "name": "Sports & Fitness", "parent_code": None, "level": 1},
    {"code": "hospitality", "name": "Hospitality", "parent_code": None, "level": 1},
    {"code": "industrial", "name": "Industrial", "parent_code": None, "level": 1},
    {"code": "farm", "name": "Farm", "parent_code": None, "level": 1},

    # ---- Level-2 subcategories (representative subset) ----
    # Transit
    {"code": "transit.airports", "name": "Airports", "parent_code": "transit", "level": 2},
    {"code": "transit.bus_stations", "name": "Bus Stations", "parent_code": "transit", "level": 2},
    {"code": "transit.rail", "name": "Rail Stations", "parent_code": "transit", "level": 2},
    {"code": "transit.subway", "name": "Subway / Metro", "parent_code": "transit", "level": 2},
    {"code": "transit.taxi_ride_share", "name": "Taxi / Ride Share", "parent_code": "transit", "level": 2},
    {"code": "transit.ferry_terminals", "name": "Ferry Terminals", "parent_code": "transit", "level": 2},

    # Retail
    {"code": "retail.convenience_stores", "name": "Convenience Stores", "parent_code": "retail", "level": 2},
    {"code": "retail.grocery", "name": "Grocery / Supermarket", "parent_code": "retail", "level": 2},
    {"code": "retail.shopping_malls", "name": "Shopping Malls", "parent_code": "retail", "level": 2},
    {"code": "retail.gas_stations", "name": "Gas Stations", "parent_code": "retail", "level": 2},
    {"code": "retail.electronics", "name": "Consumer Electronics", "parent_code": "retail", "level": 2},
    {"code": "retail.pharmacy", "name": "Pharmacy", "parent_code": "retail", "level": 2},
    {"code": "retail.apparel", "name": "Apparel", "parent_code": "retail", "level": 2},
    {"code": "retail.dealerships", "name": "Auto Dealerships", "parent_code": "retail", "level": 2},
    {"code": "retail.liquor_stores", "name": "Liquor Stores", "parent_code": "retail", "level": 2},

    # Roadside
    {"code": "roadside.billboards", "name": "Billboards", "parent_code": "roadside", "level": 2},
    {"code": "roadside.bus_shelters", "name": "Bus Shelters", "parent_code": "roadside", "level": 2},
    {"code": "roadside.urban_panels", "name": "Urban Panels", "parent_code": "roadside", "level": 2},

    # Outdoor
    {"code": "outdoor.parks", "name": "Parks", "parent_code": "outdoor", "level": 2},
    {"code": "outdoor.beaches", "name": "Beaches", "parent_code": "outdoor", "level": 2},
    {"code": "outdoor.plazas", "name": "Plazas / Squares", "parent_code": "outdoor", "level": 2},

    # Education
    {"code": "education.colleges_universities", "name": "Colleges & Universities", "parent_code": "education", "level": 2},
    {"code": "education.schools", "name": "Primary & Secondary Schools", "parent_code": "education", "level": 2},
    {"code": "education.early_learning", "name": "Early Learning", "parent_code": "education", "level": 2},

    # Health & Beauty
    {"code": "health_and_beauty.salons", "name": "Salons", "parent_code": "health_and_beauty", "level": 2},
    {"code": "health_and_beauty.spas", "name": "Spas", "parent_code": "health_and_beauty", "level": 2},
    {"code": "health_and_beauty.barbers", "name": "Barbers", "parent_code": "health_and_beauty", "level": 2},

    # Point of Care
    {"code": "point_of_care.hospitals", "name": "Hospitals", "parent_code": "point_of_care", "level": 2},
    {"code": "point_of_care.doctor_offices", "name": "Doctor Offices", "parent_code": "point_of_care", "level": 2},
    {"code": "point_of_care.dentist_offices", "name": "Dentist Offices", "parent_code": "point_of_care", "level": 2},
    {"code": "point_of_care.veterinary_offices", "name": "Veterinary Offices", "parent_code": "point_of_care", "level": 2},

    # Entertainment
    {"code": "entertainment.cinema", "name": "Cinemas", "parent_code": "entertainment", "level": 2},
    {"code": "entertainment.theaters", "name": "Theaters", "parent_code": "entertainment", "level": 2},
    {"code": "entertainment.casinos", "name": "Casinos", "parent_code": "entertainment", "level": 2},
    {"code": "entertainment.nightclubs", "name": "Nightclubs", "parent_code": "entertainment", "level": 2},

    # Leisure
    {"code": "leisure.museums_galleries", "name": "Museums & Galleries", "parent_code": "leisure", "level": 2},
    {"code": "leisure.parks_attractions", "name": "Parks & Attractions", "parent_code": "leisure", "level": 2},
    {"code": "leisure.bowling_alleys", "name": "Bowling Alleys", "parent_code": "leisure", "level": 2},

    # Government
    {"code": "government.municipal_buildings", "name": "Municipal Buildings", "parent_code": "government", "level": 2},
    {"code": "government.libraries", "name": "Libraries", "parent_code": "government", "level": 2},

    # Office Buildings
    {"code": "office_buildings.office_towers", "name": "Office Towers", "parent_code": "office_buildings", "level": 2},
    {"code": "office_buildings.coworking", "name": "Coworking Spaces", "parent_code": "office_buildings", "level": 2},

    # Residential
    {"code": "residential.apartment_buildings", "name": "Apartment Buildings", "parent_code": "residential", "level": 2},
    {"code": "residential.condominiums", "name": "Condominiums", "parent_code": "residential", "level": 2},

    # Financial
    {"code": "financial.banks", "name": "Banks", "parent_code": "financial", "level": 2},
    {"code": "financial.atms", "name": "ATMs", "parent_code": "financial", "level": 2},

    # Travel
    {"code": "travel.hotels", "name": "Hotels", "parent_code": "travel", "level": 2},
    {"code": "travel.resorts", "name": "Resorts", "parent_code": "travel", "level": 2},
    {"code": "travel.hostels", "name": "Hostels", "parent_code": "travel", "level": 2},

    # Sports & Fitness
    {"code": "sports_and_fitness.gyms", "name": "Gyms", "parent_code": "sports_and_fitness", "level": 2},
    {"code": "sports_and_fitness.stadiums", "name": "Stadiums & Arenas", "parent_code": "sports_and_fitness", "level": 2},
    {"code": "sports_and_fitness.golf_courses", "name": "Golf Courses", "parent_code": "sports_and_fitness", "level": 2},

    # Hospitality
    {"code": "hospitality.restaurants", "name": "Restaurants", "parent_code": "hospitality", "level": 2},
    {"code": "hospitality.fast_food", "name": "Fast Food", "parent_code": "hospitality", "level": 2},
    {"code": "hospitality.cafes", "name": "Cafés", "parent_code": "hospitality", "level": 2},
    {"code": "hospitality.bars", "name": "Bars", "parent_code": "hospitality", "level": 2},

    # Industrial
    {"code": "industrial.factories", "name": "Factories", "parent_code": "industrial", "level": 2},
    {"code": "industrial.warehouses", "name": "Warehouses", "parent_code": "industrial", "level": 2},

    # Farm
    {"code": "farm.dairy_farms", "name": "Dairy Farms", "parent_code": "farm", "level": 2},
    {"code": "farm.livestock_farms", "name": "Livestock Farms", "parent_code": "farm", "level": 2},
]
