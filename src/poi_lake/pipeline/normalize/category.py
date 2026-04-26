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


# --- gosom ``category`` field → OpenOOH ---
# gosom emits Google Maps' user-facing category labels in the request lang
# (we use lang=vi). These are localized strings, not the primaryType used
# by the Places API. We match by accent-folded substring against this list.
# Order matters: longer / more specific keys first.
_GOSOM_CATEGORY_MAP: tuple[tuple[str, tuple[str, str | None]], ...] = (
    ("trung tam thuong mai", ("retail", "retail.shopping_malls")),
    ("cua hang tien loi", ("retail", "retail.convenience_stores")),
    ("cua hang dien may", ("retail", "retail.electronics")),
    ("cua hang dien thoai", ("retail", "retail.electronics")),
    ("cua hang quan ao", ("retail", "retail.apparel")),
    ("nha thuoc", ("retail", "retail.pharmacy")),
    ("hieu thuoc", ("retail", "retail.pharmacy")),
    ("tram xang", ("retail", "retail.gas_stations")),
    ("sieu thi", ("retail", "retail.grocery")),
    ("cua hang", ("retail", None)),
    ("benh vien", ("point_of_care", "point_of_care.hospitals")),
    ("phong kham", ("point_of_care", "point_of_care.doctor_offices")),
    ("nha thuoc", ("retail", "retail.pharmacy")),
    ("nha hang", ("hospitality", "hospitality.restaurants")),
    ("quan an", ("hospitality", "hospitality.restaurants")),
    ("quan ca phe", ("hospitality", "hospitality.cafes")),
    ("ca phe", ("hospitality", "hospitality.cafes")),
    ("cafe", ("hospitality", "hospitality.cafes")),
    ("coffee", ("hospitality", "hospitality.cafes")),
    ("quan bar", ("hospitality", "hospitality.bars")),
    ("quan an nhanh", ("hospitality", "hospitality.fast_food")),
    ("thuc an nhanh", ("hospitality", "hospitality.fast_food")),
    ("ngan hang", ("financial", "financial.banks")),
    ("may rut tien", ("financial", "financial.atms")),
    ("atm", ("financial", "financial.atms")),
    ("khach san", ("travel", "travel.hotels")),
    ("nha nghi", ("travel", "travel.hotels")),
    ("hostel", ("travel", "travel.hostels")),
    ("rap chieu phim", ("entertainment", "entertainment.cinema")),
    ("phong gym", ("sports_and_fitness", "sports_and_fitness.gyms")),
    ("trung tam the duc", ("sports_and_fitness", "sports_and_fitness.gyms")),
    ("san van dong", ("sports_and_fitness", "sports_and_fitness.stadiums")),
    ("truong dai hoc", ("education", "education.colleges_universities")),
    ("dai hoc", ("education", "education.colleges_universities")),
    ("truong hoc", ("education", "education.schools")),
    ("mau giao", ("education", "education.early_learning")),
    ("bao tang", ("leisure", "leisure.museums_galleries")),
    ("phong tranh", ("leisure", "leisure.museums_galleries")),
    ("cong vien", ("outdoor", "outdoor.parks")),
    ("salon toc", ("health_and_beauty", "health_and_beauty.salons")),
    ("salon lam dep", ("health_and_beauty", "health_and_beauty.salons")),
    ("spa", ("health_and_beauty", "health_and_beauty.spas")),
    ("tho cat toc", ("health_and_beauty", "health_and_beauty.barbers")),
    ("san bay", ("transit", "transit.airports")),
    ("ben xe", ("transit", "transit.bus_stations")),
    ("ga tau", ("transit", "transit.rail")),
    # Fallback English aliases that often appear in mixed-language gosom output:
    ("restaurant", ("hospitality", "hospitality.restaurants")),
    ("convenience store", ("retail", "retail.convenience_stores")),
    ("supermarket", ("retail", "retail.grocery")),
    ("shopping mall", ("retail", "retail.shopping_malls")),
    ("pharmacy", ("retail", "retail.pharmacy")),
    ("bank", ("financial", "financial.banks")),
    ("hotel", ("travel", "travel.hotels")),
    ("cinema", ("entertainment", "entertainment.cinema")),
    ("hospital", ("point_of_care", "point_of_care.hospitals")),
    ("school", ("education", "education.schools")),
)


# --- name-based fallback ---------------------------------------------------
# When a source returns an empty / unknown ``raw_category`` (gosom CSV often
# does this for non-business places like schools and government offices), we
# infer the category from the canonical name. Keys are accent-folded.
#
# **Rule ordering matters.** A Vietnamese place name almost always starts
# with the place type ("Bệnh viện X", "Trường ABC", "Khách sạn Y"), and the
# place type beats any institution mentioned later in the same string.
# Concretely: ``Bệnh viện Đại học Y Hà Nội`` is the *hospital* attached to
# Hanoi Medical University, not the university itself. So strong place-type
# prefixes (``benh vien``, ``phong kham``, ``khach san`` …) must be matched
# *before* the broader institution markers (``dai hoc``, ``truong``).
#
# Three groups, in match priority:
#   1. Place-type prefixes — when present, they always win.
#   2. Education markers — only if no place-type fired.
#   3. Generic / weaker fallbacks.
_NAME_INFERENCE_RULES: tuple[tuple[str, CategoryResult], ...] = (
    # ---- 1. Strong place-type prefixes -----------------------------------
    # Point of care
    ("benh vien", ("point_of_care", "point_of_care.hospitals")),
    ("hospital", ("point_of_care", "point_of_care.hospitals")),
    ("phong kham", ("point_of_care", "point_of_care.doctor_offices")),
    ("clinic", ("point_of_care", "point_of_care.doctor_offices")),
    ("nha si", ("point_of_care", "point_of_care.dentist_offices")),
    ("nha thuoc", ("retail", "retail.pharmacy")),
    ("hieu thuoc", ("retail", "retail.pharmacy")),
    # Financial
    ("ngan hang", ("financial", "financial.banks")),
    # Travel
    ("khach san", ("travel", "travel.hotels")),
    ("hotel", ("travel", "travel.hotels")),
    # Hospitality (food / drink)
    ("nha hang", ("hospitality", "hospitality.restaurants")),
    ("quan an", ("hospitality", "hospitality.restaurants")),
    ("restaurant", ("hospitality", "hospitality.restaurants")),
    # Government
    ("uy ban nhan dan", ("government", "government.municipal_buildings")),
    ("ubnd", ("government", "government.municipal_buildings")),
    ("cong an", ("government", "government.municipal_buildings")),
    ("thu vien", ("government", "government.libraries")),
    # Entertainment / Transit
    ("rap chieu phim", ("entertainment", "entertainment.cinema")),
    ("cgv", ("entertainment", "entertainment.cinema")),
    ("lotte cinema", ("entertainment", "entertainment.cinema")),
    ("san bay", ("transit", "transit.airports")),
    ("ben xe", ("transit", "transit.bus_stations")),
    ("ga tau", ("transit", "transit.rail")),
    # Convenience-store brand prefixes (BrandDetector handles the canonical
    # name; this just keeps the category populated when the brand row was
    # disabled).
    ("circle k", ("retail", "retail.convenience_stores")),
    ("vinmart", ("retail", "retail.convenience_stores")),
    ("winmart", ("retail", "retail.convenience_stores")),
    ("co.opmart", ("retail", "retail.grocery")),
    ("highlands coffee", ("hospitality", "hospitality.cafes")),

    # ---- 2. Education (only fires if no place-type matched) --------------
    # Composite forms first so they beat the bare ``truong`` / ``dai hoc``.
    ("truong dai hoc", ("education", "education.colleges_universities")),
    ("hoc vien", ("education", "education.colleges_universities")),
    ("university", ("education", "education.colleges_universities")),
    ("college", ("education", "education.colleges_universities")),
    ("dai hoc", ("education", "education.colleges_universities")),
    ("truong mam non", ("education", "education.early_learning")),
    ("truong mn ", ("education", "education.early_learning")),
    ("kindergarten", ("education", "education.early_learning")),
    ("preschool", ("education", "education.early_learning")),
    ("truong thpt", ("education", "education.schools")),
    ("truong thcs", ("education", "education.schools")),
    ("truong tieu hoc", ("education", "education.schools")),
    ("high school", ("education", "education.schools")),
    ("primary school", ("education", "education.schools")),
    ("truong", ("education", "education.schools")),  # generic ``Trường …``
    ("school", ("education", "education.schools")),

    # ---- 3. Generic / weaker fallbacks -----------------------------------
    ("quan ca phe", ("hospitality", "hospitality.cafes")),
    ("ca phe", ("hospitality", "hospitality.cafes")),
    ("cafe", ("hospitality", "hospitality.cafes")),
    ("coffee", ("hospitality", "hospitality.cafes")),
)


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

    def map_gosom(self, category_text: str | None) -> CategoryResult:
        if not category_text:
            return (None, None)
        from poi_lake.pipeline.normalize.text import normalize_text

        folded = normalize_text(category_text)
        if not folded:
            return (None, None)
        for needle, result in _GOSOM_CATEGORY_MAP:
            if needle in folded:
                return result
        return (None, None)

    def map(self, source_code: str, raw_category: str | None) -> CategoryResult:
        if source_code == "google_places":
            return self.map_google(raw_category)
        if source_code == "osm_overpass":
            return self.map_osm(raw_category)
        if source_code == "gosom_scraper":
            return self.map_gosom(raw_category)
        # Phase 2b will additionally register: vietmap, foody.
        return (None, None)

    def infer_from_name(self, name: str | None) -> CategoryResult:
        """Last-resort fallback when ``map(...)`` returned ``(None, None)``.

        Scans the accent-folded name for VN-specific keywords (``Trường``,
        ``Bệnh viện``, ``Ngân hàng`` …) plus a few English common ones.
        Returns the first rule that matches — order in ``_NAME_INFERENCE_RULES``
        encodes priority (longer / more specific patterns first).
        """
        if not name:
            return (None, None)
        from poi_lake.pipeline.normalize.text import normalize_text

        folded = normalize_text(name)
        if not folded:
            return (None, None)
        for needle, result in _NAME_INFERENCE_RULES:
            if needle in folded:
                return result
        return (None, None)

    def map_with_fallback(
        self, source_code: str, raw_category: str | None, name: str | None
    ) -> CategoryResult:
        """``map(...)`` first, then ``infer_from_name(name)`` if nothing stuck."""
        result = self.map(source_code, raw_category)
        if result == (None, None):
            return self.infer_from_name(name)
        return result
