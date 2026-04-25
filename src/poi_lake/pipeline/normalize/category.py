"""Map source-native category labels to the OpenOOH v1.1 taxonomy.

Two-level resolution: we always return at least an OpenOOH top-level code
(e.g. ``retail``); when the input matches a known sub-bucket we also return
the level-2 code (e.g. ``retail.convenience_stores``).

Inputs:
  * Google Places ``primaryType`` strings (e.g. ``"convenience_store"``).
  * OSM-style ``"key=value"`` strings (e.g. ``"amenity=cafe"``,
    ``"shop=convenience"``). The extractor for OSM produces this form.

Unknown inputs return ``(None, None)`` — the caller can leave the columns
NULL or fall back to an LLM in a later phase.
"""

from __future__ import annotations

# (top, sub) — sub may be None if only the top-level is known.
CategoryResult = tuple[str | None, str | None]


# --- Google Places (New) primaryType → OpenOOH ------------------------------
_GOOGLE_TYPE_MAP: dict[str, CategoryResult] = {
    # Hospitality
    "restaurant": ("hospitality", "hospitality.restaurants"),
    "cafe": ("hospitality", "hospitality.cafes"),
    "coffee_shop": ("hospitality", "hospitality.cafes"),
    "bar": ("hospitality", "hospitality.bars"),
    "pub": ("hospitality", "hospitality.bars"),
    "fast_food_restaurant": ("hospitality", "hospitality.fast_food"),
    "meal_takeaway": ("hospitality", "hospitality.fast_food"),
    "bakery": ("hospitality", "hospitality.cafes"),
    "food": ("hospitality", None),
    # Retail
    "convenience_store": ("retail", "retail.convenience_stores"),
    "supermarket": ("retail", "retail.grocery"),
    "grocery_store": ("retail", "retail.grocery"),
    "shopping_mall": ("retail", "retail.shopping_malls"),
    "department_store": ("retail", "retail.shopping_malls"),
    "gas_station": ("retail", "retail.gas_stations"),
    "electronics_store": ("retail", "retail.electronics"),
    "clothing_store": ("retail", "retail.apparel"),
    "car_dealer": ("retail", "retail.dealerships"),
    "liquor_store": ("retail", "retail.liquor_stores"),
    "pharmacy": ("retail", "retail.pharmacy"),
    "drugstore": ("retail", "retail.pharmacy"),
    "store": ("retail", None),
    # Financial
    "bank": ("financial", "financial.banks"),
    "atm": ("financial", "financial.atms"),
    # Health & beauty
    "beauty_salon": ("health_and_beauty", "health_and_beauty.salons"),
    "hair_salon": ("health_and_beauty", "health_and_beauty.salons"),
    "spa": ("health_and_beauty", "health_and_beauty.spas"),
    # Point of care
    "hospital": ("point_of_care", "point_of_care.hospitals"),
    "doctor": ("point_of_care", "point_of_care.doctor_offices"),
    "dentist": ("point_of_care", "point_of_care.dentist_offices"),
    "veterinary_care": ("point_of_care", "point_of_care.veterinary_offices"),
    # Entertainment
    "movie_theater": ("entertainment", "entertainment.cinema"),
    "night_club": ("entertainment", "entertainment.nightclubs"),
    "casino": ("entertainment", "entertainment.casinos"),
    # Leisure
    "museum": ("leisure", "leisure.museums_galleries"),
    "art_gallery": ("leisure", "leisure.museums_galleries"),
    "tourist_attraction": ("leisure", "leisure.parks_attractions"),
    "amusement_park": ("leisure", "leisure.parks_attractions"),
    # Travel
    "lodging": ("travel", "travel.hotels"),
    "hotel": ("travel", "travel.hotels"),
    "resort_hotel": ("travel", "travel.resorts"),
    "hostel": ("travel", "travel.hostels"),
    # Sports & fitness
    "gym": ("sports_and_fitness", "sports_and_fitness.gyms"),
    "stadium": ("sports_and_fitness", "sports_and_fitness.stadiums"),
    "golf_course": ("sports_and_fitness", "sports_and_fitness.golf_courses"),
    # Education
    "school": ("education", "education.schools"),
    "primary_school": ("education", "education.schools"),
    "secondary_school": ("education", "education.schools"),
    "university": ("education", "education.colleges_universities"),
    "preschool": ("education", "education.early_learning"),
    # Government
    "city_hall": ("government", "government.municipal_buildings"),
    "library": ("government", "government.libraries"),
    "courthouse": ("government", "government.municipal_buildings"),
    "police": ("government", "government.municipal_buildings"),
    # Transit
    "airport": ("transit", "transit.airports"),
    "bus_station": ("transit", "transit.bus_stations"),
    "train_station": ("transit", "transit.rail"),
    "subway_station": ("transit", "transit.subway"),
    "taxi_stand": ("transit", "transit.taxi_ride_share"),
    "ferry_terminal": ("transit", "transit.ferry_terminals"),
}


# --- OSM tag → OpenOOH ------------------------------------------------------
_OSM_TAG_MAP: dict[str, CategoryResult] = {
    # amenity
    "amenity=cafe": ("hospitality", "hospitality.cafes"),
    "amenity=restaurant": ("hospitality", "hospitality.restaurants"),
    "amenity=fast_food": ("hospitality", "hospitality.fast_food"),
    "amenity=bar": ("hospitality", "hospitality.bars"),
    "amenity=pub": ("hospitality", "hospitality.bars"),
    "amenity=biergarten": ("hospitality", "hospitality.bars"),
    "amenity=food_court": ("hospitality", "hospitality.fast_food"),
    "amenity=ice_cream": ("hospitality", "hospitality.cafes"),
    "amenity=bank": ("financial", "financial.banks"),
    "amenity=atm": ("financial", "financial.atms"),
    "amenity=hospital": ("point_of_care", "point_of_care.hospitals"),
    "amenity=clinic": ("point_of_care", "point_of_care.doctor_offices"),
    "amenity=doctors": ("point_of_care", "point_of_care.doctor_offices"),
    "amenity=dentist": ("point_of_care", "point_of_care.dentist_offices"),
    "amenity=veterinary": ("point_of_care", "point_of_care.veterinary_offices"),
    "amenity=pharmacy": ("retail", "retail.pharmacy"),
    "amenity=fuel": ("retail", "retail.gas_stations"),
    "amenity=cinema": ("entertainment", "entertainment.cinema"),
    "amenity=theatre": ("entertainment", "entertainment.theaters"),
    "amenity=nightclub": ("entertainment", "entertainment.nightclubs"),
    "amenity=casino": ("entertainment", "entertainment.casinos"),
    "amenity=university": ("education", "education.colleges_universities"),
    "amenity=college": ("education", "education.colleges_universities"),
    "amenity=school": ("education", "education.schools"),
    "amenity=kindergarten": ("education", "education.early_learning"),
    "amenity=library": ("government", "government.libraries"),
    "amenity=townhall": ("government", "government.municipal_buildings"),
    "amenity=courthouse": ("government", "government.municipal_buildings"),
    "amenity=police": ("government", "government.municipal_buildings"),
    "amenity=post_office": ("retail", None),
    "amenity=bus_station": ("transit", "transit.bus_stations"),
    "amenity=ferry_terminal": ("transit", "transit.ferry_terminals"),
    "amenity=taxi": ("transit", "transit.taxi_ride_share"),
    # shop
    "shop=convenience": ("retail", "retail.convenience_stores"),
    "shop=supermarket": ("retail", "retail.grocery"),
    "shop=mall": ("retail", "retail.shopping_malls"),
    "shop=department_store": ("retail", "retail.shopping_malls"),
    "shop=electronics": ("retail", "retail.electronics"),
    "shop=mobile_phone": ("retail", "retail.electronics"),
    "shop=clothes": ("retail", "retail.apparel"),
    "shop=car": ("retail", "retail.dealerships"),
    "shop=alcohol": ("retail", "retail.liquor_stores"),
    "shop=hairdresser": ("health_and_beauty", "health_and_beauty.barbers"),
    "shop=beauty": ("health_and_beauty", "health_and_beauty.salons"),
    # tourism
    "tourism=hotel": ("travel", "travel.hotels"),
    "tourism=hostel": ("travel", "travel.hostels"),
    "tourism=motel": ("travel", "travel.hotels"),
    "tourism=guest_house": ("travel", "travel.hotels"),
    "tourism=museum": ("leisure", "leisure.museums_galleries"),
    "tourism=gallery": ("leisure", "leisure.museums_galleries"),
    "tourism=attraction": ("leisure", "leisure.parks_attractions"),
    "tourism=theme_park": ("leisure", "leisure.parks_attractions"),
    # leisure
    "leisure=fitness_centre": ("sports_and_fitness", "sports_and_fitness.gyms"),
    "leisure=sports_centre": ("sports_and_fitness", "sports_and_fitness.gyms"),
    "leisure=stadium": ("sports_and_fitness", "sports_and_fitness.stadiums"),
    "leisure=golf_course": ("sports_and_fitness", "sports_and_fitness.golf_courses"),
    "leisure=park": ("outdoor", "outdoor.parks"),
    "leisure=bowling_alley": ("leisure", "leisure.bowling_alleys"),
    # office (generic)
    "office=*": ("office_buildings", "office_buildings.office_towers"),
}


class CategoryMapper:
    """Resolve a source-native category string into an OpenOOH (top, sub)."""

    def map_google(self, primary_type: str | None) -> CategoryResult:
        if not primary_type:
            return (None, None)
        return _GOOGLE_TYPE_MAP.get(primary_type.lower(), (None, None))

    def map_osm(self, key_value: str | None) -> CategoryResult:
        if not key_value:
            return (None, None)
        kv = key_value.lower()
        if kv in _OSM_TAG_MAP:
            return _OSM_TAG_MAP[kv]
        # Generic fallback for any office=*
        if kv.startswith("office="):
            return _OSM_TAG_MAP["office=*"]
        return (None, None)

    def map(self, source_code: str, raw_category: str | None) -> CategoryResult:
        if source_code == "google_places":
            return self.map_google(raw_category)
        if source_code == "osm_overpass":
            return self.map_osm(raw_category)
        # Phase 2b sources will register their own mappers.
        return (None, None)
