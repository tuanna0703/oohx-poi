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
