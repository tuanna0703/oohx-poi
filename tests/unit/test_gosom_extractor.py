"""Gosom CSV-row extractor."""

from __future__ import annotations

from poi_lake.pipeline.extractors import GosomScraperExtractor, get_extractor


def test_basic_row() -> None:
    row = {
        "title": "Circle K - Bà Triệu",
        "address": "20 Bà Triệu, Hoàn Kiếm, Hà Nội",
        "category": "Cửa hàng tiện lợi",
        "phone": "1800 6915",
        "website": "https://circlek.com.vn",
        "latitude": "21.0250",
        "longitude": "105.8545",
        "review_count": "318",
    }
    cf = GosomScraperExtractor().extract(row)
    assert cf is not None
    assert cf.name == "Circle K - Bà Triệu"
    assert cf.location == (21.0250, 105.8545)
    assert cf.address == "20 Bà Triệu, Hoàn Kiếm, Hà Nội"
    assert cf.phone == "1800 6915"
    assert cf.website == "https://circlek.com.vn"
    assert cf.raw_category == "Cửa hàng tiện lợi"


def test_empty_strings_become_none() -> None:
    row = {"title": "Foo", "address": "", "phone": "  ", "website": None, "latitude": "", "longitude": ""}
    cf = GosomScraperExtractor().extract(row)
    assert cf is not None
    assert cf.address is None
    assert cf.phone is None
    assert cf.website is None
    assert cf.location is None


def test_no_title_returns_none() -> None:
    cf = GosomScraperExtractor().extract({"title": "", "category": "anything"})
    assert cf is None


def test_registry_resolves_gosom() -> None:
    assert isinstance(get_extractor("gosom_scraper"), GosomScraperExtractor)


def test_invalid_coords_drop_silently() -> None:
    row = {"title": "X", "latitude": "not-a-number", "longitude": "abc"}
    cf = GosomScraperExtractor().extract(row)
    assert cf is not None
    assert cf.location is None
