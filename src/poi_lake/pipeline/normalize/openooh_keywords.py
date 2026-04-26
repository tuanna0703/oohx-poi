"""OpenOOH category code → search keywords for free-text POI sources.

The forward direction (source → OpenOOH) lives in :mod:`category`. This
module is the *reverse*: given an OpenOOH code (level-1 or level-2), what
text terms should we feed Google Maps / gosom / Foody to find matching
POIs?

Coverage is biased toward the Vietnamese market — most lists pair a few
English / Latin transliterations with the Vietnamese-language equivalents
that Google indexes for VN listings. Add more entries as needed; falling
through to ``DEFAULT`` is fine for code-paths that just need a hint.

Reference taxonomy: https://github.com/openooh/venue-taxonomy
"""

from __future__ import annotations


# Each value is a tuple so callers can rely on stable iteration order
# (we hand it to gosom verbatim — first keyword shows up first in the UI).
_KEYWORDS: dict[str, tuple[str, ...]] = {
    # ---------------- Hospitality ----------------
    "hospitality": (
        "restaurant", "cafe", "bar", "nha hang", "quan ca phe",
    ),
    "hospitality.restaurants": (
        "restaurant", "nha hang", "quan an", "Vietnamese restaurant",
    ),
    "hospitality.cafes": (
        "cafe", "coffee shop", "quan ca phe", "ca phe",
    ),
    "hospitality.bars": (
        "bar", "pub", "quan bar", "beer club",
    ),
    "hospitality.fast_food": (
        "fast food", "thuc an nhanh", "kfc", "lotteria", "mcdonalds", "burger king",
    ),

    # ---------------- Retail ----------------
    "retail": (
        "store", "shop", "cua hang", "sieu thi",
    ),
    "retail.convenience_stores": (
        "convenience store", "cua hang tien loi",
        "circle k", "gs25", "familymart", "7-eleven", "winmart+", "ministop",
    ),
    "retail.grocery": (
        "supermarket", "sieu thi", "co.opmart", "winmart", "lotte mart", "big c", "aeon",
    ),
    "retail.shopping_malls": (
        "shopping mall", "trung tam thuong mai", "vincom", "aeon mall", "lotte mall",
    ),
    "retail.gas_stations": (
        "gas station", "tram xang", "petrolimex", "shell", "caltex", "pvoil",
    ),
    "retail.electronics": (
        "electronics store", "cua hang dien may", "cua hang dien thoai",
        "thegioididong", "fpt shop", "dien may xanh", "nguyen kim",
    ),
    "retail.pharmacy": (
        "pharmacy", "nha thuoc", "hieu thuoc", "pharmacity", "long chau", "an khang",
    ),
    "retail.apparel": (
        "clothing store", "cua hang quan ao", "fashion shop", "thoi trang",
    ),
    "retail.dealerships": (
        "car dealer", "dai ly oto", "showroom o to",
    ),
    "retail.liquor_stores": (
        "liquor store", "cua hang ruou", "wine shop",
    ),

    # ---------------- Financial ----------------
    "financial": (
        "bank", "atm", "ngan hang",
    ),
    "financial.banks": (
        "bank", "ngan hang",
        "vietcombank", "vietinbank", "bidv", "agribank",
        "techcombank", "sacombank", "acb", "mb bank",
    ),
    "financial.atms": (
        "atm", "may rut tien",
    ),

    # ---------------- Health & Beauty ----------------
    "health_and_beauty": (
        "salon", "spa", "barber", "salon toc", "lam dep",
    ),
    "health_and_beauty.salons": (
        "hair salon", "beauty salon", "salon toc", "salon lam dep",
    ),
    "health_and_beauty.spas": (
        "spa", "massage", "day spa",
    ),
    "health_and_beauty.barbers": (
        "barber", "tho cat toc", "barber shop",
    ),

    # ---------------- Point of Care ----------------
    "point_of_care": (
        "hospital", "clinic", "benh vien", "phong kham",
    ),
    "point_of_care.hospitals": (
        "hospital", "benh vien",
    ),
    "point_of_care.doctor_offices": (
        "doctor", "clinic", "phong kham", "phong mach",
    ),
    "point_of_care.dentist_offices": (
        "dentist", "nha si", "phong kham nha khoa", "dental clinic",
    ),
    "point_of_care.veterinary_offices": (
        "veterinary", "vet", "phong kham thu y",
    ),

    # ---------------- Entertainment ----------------
    "entertainment": (
        "cinema", "theater", "rap chieu phim",
    ),
    "entertainment.cinema": (
        "cinema", "movie theater", "rap chieu phim", "cgv", "lotte cinema", "galaxy cinema",
    ),
    "entertainment.theaters": (
        "theatre", "nha hat", "live theater",
    ),
    "entertainment.casinos": (
        "casino", "song bac",
    ),
    "entertainment.nightclubs": (
        "nightclub", "club", "vu truong", "ho dem",
    ),

    # ---------------- Leisure ----------------
    "leisure": (
        "park", "museum", "attraction", "cong vien", "bao tang",
    ),
    "leisure.museums_galleries": (
        "museum", "art gallery", "bao tang", "phong tranh",
    ),
    "leisure.parks_attractions": (
        "tourist attraction", "amusement park", "diem du lich", "khu vui choi",
    ),
    "leisure.bowling_alleys": (
        "bowling alley", "san bowling",
    ),

    # ---------------- Travel ----------------
    "travel": (
        "hotel", "khach san", "nha nghi",
    ),
    "travel.hotels": (
        "hotel", "khach san",
    ),
    "travel.resorts": (
        "resort", "khu nghi duong",
    ),
    "travel.hostels": (
        "hostel", "nha nghi", "homestay",
    ),

    # ---------------- Sports & Fitness ----------------
    "sports_and_fitness": (
        "gym", "sports center", "phong gym", "trung tam the duc",
    ),
    "sports_and_fitness.gyms": (
        "gym", "fitness center", "phong gym", "phong tap", "california fitness",
    ),
    "sports_and_fitness.stadiums": (
        "stadium", "san van dong",
    ),
    "sports_and_fitness.golf_courses": (
        "golf course", "san golf",
    ),

    # ---------------- Education ----------------
    "education": (
        "school", "university", "truong hoc", "dai hoc",
    ),
    "education.colleges_universities": (
        "university", "college", "truong dai hoc", "hoc vien",
    ),
    "education.schools": (
        "school", "primary school", "high school", "truong tieu hoc", "truong trung hoc",
    ),
    "education.early_learning": (
        "preschool", "kindergarten", "mau giao", "mam non",
    ),

    # ---------------- Government ----------------
    "government": (
        "government office", "library", "uy ban", "thu vien",
    ),
    "government.municipal_buildings": (
        "city hall", "uy ban nhan dan", "courthouse", "police station", "cong an",
    ),
    "government.libraries": (
        "library", "thu vien",
    ),

    # ---------------- Office Buildings ----------------
    "office_buildings": (
        "office building", "toa nha van phong",
    ),
    "office_buildings.office_towers": (
        "office tower", "office building", "toa nha van phong",
    ),
    "office_buildings.coworking": (
        "coworking space", "shared office", "khong gian lam viec chung",
    ),

    # ---------------- Residential ----------------
    "residential": (
        "apartment", "chung cu", "can ho",
    ),
    "residential.apartment_buildings": (
        "apartment building", "chung cu", "can ho",
    ),
    "residential.condominiums": (
        "condominium", "condo", "can ho cao cap",
    ),

    # ---------------- Transit ----------------
    "transit": (
        "airport", "bus station", "san bay", "ben xe",
    ),
    "transit.airports": (
        "airport", "san bay",
    ),
    "transit.bus_stations": (
        "bus station", "ben xe",
    ),
    "transit.rail": (
        "train station", "ga tau", "ga xe lua",
    ),
    "transit.subway": (
        "metro station", "subway station", "ga metro",
    ),
    "transit.taxi_ride_share": (
        "taxi stand", "diem don taxi",
    ),
    "transit.ferry_terminals": (
        "ferry terminal", "ben pha",
    ),

    # ---------------- Outdoor ----------------
    "outdoor": (
        "park", "plaza", "cong vien", "quang truong",
    ),
    "outdoor.parks": (
        "park", "cong vien",
    ),
    "outdoor.beaches": (
        "beach", "bai bien",
    ),
    "outdoor.plazas": (
        "plaza", "square", "quang truong",
    ),

    # ---------------- Roadside ----------------
    "roadside": (
        "billboard", "bus shelter", "bien quang cao",
    ),
    "roadside.billboards": (
        "billboard", "bien quang cao",
    ),
    "roadside.bus_shelters": (
        "bus stop", "tram xe buyt",
    ),

    # ---------------- Industrial / Farm (sparse) ----------------
    "industrial.factories": (
        "factory", "nha may",
    ),
    "industrial.warehouses": (
        "warehouse", "kho hang",
    ),
    "farm.dairy_farms": (
        "dairy farm", "trai bo sua",
    ),
    "farm.livestock_farms": (
        "livestock farm", "trang trai",
    ),
}


_DEFAULT: tuple[str, ...] = ("shop", "restaurant")


def keywords_for_openooh(code: str | None) -> list[str]:
    """Return search terms appropriate for ``code``.

    Resolution order:
      1. Exact match on the full code (level-2 like ``retail.convenience_stores``).
      2. Fall back to the level-1 prefix (e.g. ``retail`` from ``retail.unknown_sub``).
      3. ``DEFAULT`` — better than nothing for fully-unknown codes.
    """
    if not code:
        return list(_DEFAULT)
    code = code.strip().lower()
    if code in _KEYWORDS:
        return list(_KEYWORDS[code])
    if "." in code:
        top = code.split(".", 1)[0]
        if top in _KEYWORDS:
            return list(_KEYWORDS[top])
    return list(_DEFAULT)


def is_openooh_code(value: str | None) -> bool:
    """Best-effort check: looks like a known OpenOOH code (level-1 or level-2)."""
    if not value:
        return False
    v = value.strip().lower()
    if v in _KEYWORDS:
        return True
    if "." in v and v.split(".", 1)[0] in _KEYWORDS:
        return True
    return False
