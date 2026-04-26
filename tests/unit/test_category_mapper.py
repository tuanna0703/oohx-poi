"""Source-native → OpenOOH category mapping."""

from __future__ import annotations

from poi_lake.pipeline.normalize import CategoryMapper


def test_google_known_types() -> None:
    m = CategoryMapper()
    assert m.map("google_places", "convenience_store") == ("retail", "retail.convenience_stores")
    assert m.map("google_places", "cafe") == ("hospitality", "hospitality.cafes")
    assert m.map("google_places", "atm") == ("financial", "financial.atms")
    assert m.map("google_places", "movie_theater") == ("entertainment", "entertainment.cinema")


def test_google_unknown_type() -> None:
    m = CategoryMapper()
    assert m.map("google_places", "made_up_thing") == (None, None)
    assert m.map("google_places", None) == (None, None)


def test_osm_amenity_and_shop() -> None:
    m = CategoryMapper()
    assert m.map("osm_overpass", "amenity=cafe") == ("hospitality", "hospitality.cafes")
    assert m.map("osm_overpass", "shop=convenience") == ("retail", "retail.convenience_stores")
    assert m.map("osm_overpass", "tourism=hotel") == ("travel", "travel.hotels")


def test_osm_office_generic_fallback() -> None:
    m = CategoryMapper()
    assert m.map("osm_overpass", "office=insurance") == (
        "office_buildings",
        "office_buildings.office_towers",
    )


def test_unknown_source_returns_nones() -> None:
    m = CategoryMapper()
    assert m.map("brand_new_source", "anything") == (None, None)


def test_gosom_vietnamese_categories() -> None:
    m = CategoryMapper()
    assert m.map("gosom_scraper", "Quán cà phê") == ("hospitality", "hospitality.cafes")
    assert m.map("gosom_scraper", "Cửa hàng tiện lợi") == ("retail", "retail.convenience_stores")
    assert m.map("gosom_scraper", "Nhà hàng") == ("hospitality", "hospitality.restaurants")
    assert m.map("gosom_scraper", "Ngân hàng") == ("financial", "financial.banks")
    assert m.map("gosom_scraper", "Khách sạn") == ("travel", "travel.hotels")


def test_gosom_english_fallback() -> None:
    m = CategoryMapper()
    assert m.map("gosom_scraper", "Restaurant") == ("hospitality", "hospitality.restaurants")
    assert m.map("gosom_scraper", "Pharmacy") == ("retail", "retail.pharmacy")


def test_gosom_unknown_category() -> None:
    m = CategoryMapper()
    assert m.map("gosom_scraper", "Some random thing") == (None, None)
    assert m.map("gosom_scraper", None) == (None, None)


# ---- name-inference fallback (gosom often returns empty raw_category) ----


def test_infer_education_from_vn_name() -> None:
    m = CategoryMapper()
    assert m.infer_from_name("Trường Đại học Kinh tế Quốc dân") == (
        "education", "education.colleges_universities"
    )
    assert m.infer_from_name("Học viện Báo chí và Tuyên truyền") == (
        "education", "education.colleges_universities"
    )
    assert m.infer_from_name("Trường THCS Láng Thượng") == (
        "education", "education.schools"
    )
    assert m.infer_from_name("Trường Mầm Non Hoa Linh") == (
        "education", "education.early_learning"
    )


def test_infer_education_from_english_name() -> None:
    m = CategoryMapper()
    assert m.infer_from_name("Hanoi International School") == (
        "education", "education.schools"
    )
    assert m.infer_from_name("Columbia Southern University Hà Nội") == (
        "education", "education.colleges_universities"
    )
    assert m.infer_from_name("Aqua-Tots Swim School") == (
        "education", "education.schools"
    )


def test_infer_other_categories() -> None:
    m = CategoryMapper()
    assert m.infer_from_name("Bệnh viện Bạch Mai") == (
        "point_of_care", "point_of_care.hospitals"
    )
    assert m.infer_from_name("Ngân hàng Vietcombank chi nhánh Cầu Giấy") == (
        "financial", "financial.banks"
    )
    assert m.infer_from_name("Cộng Cà Phê Phan Đình Phùng") == (
        "hospitality", "hospitality.cafes"
    )
    assert m.infer_from_name("Khách sạn Sofitel") == ("travel", "travel.hotels")


def test_infer_returns_none_for_generic() -> None:
    m = CategoryMapper()
    assert m.infer_from_name("Foo Bar") == (None, None)
    assert m.infer_from_name("") == (None, None)
    assert m.infer_from_name(None) == (None, None)


def test_map_with_fallback_uses_raw_first() -> None:
    """When raw_category resolves cleanly, the name-fallback isn't consulted."""
    m = CategoryMapper()
    # raw=cafe wins even though the name has "Trường"
    result = m.map_with_fallback(
        "gosom_scraper", "Quán cà phê", "Trường ABC"
    )
    assert result == ("hospitality", "hospitality.cafes")


def test_map_with_fallback_uses_name_when_raw_empty() -> None:
    """gosom returning an empty category falls back to the name heuristic."""
    m = CategoryMapper()
    result = m.map_with_fallback(
        "gosom_scraper", None, "Trường Đại học Thương mại"
    )
    assert result == ("education", "education.colleges_universities")
