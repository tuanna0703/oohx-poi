"""Per-source extractors."""

from __future__ import annotations

from poi_lake.pipeline.extractors import (
    GooglePlacesExtractor,
    OSMOverpassExtractor,
    get_extractor,
)


def test_google_extracts_basic() -> None:
    payload = {
        "id": "ChIJxxx",
        "displayName": {"text": "Highlands Coffee — Tràng Tiền"},
        "formattedAddress": "1 Tràng Tiền, Hoàn Kiếm, Hà Nội",
        "location": {"latitude": 21.0247, "longitude": 105.8556},
        "primaryType": "cafe",
        "nationalPhoneNumber": "024 1234 5678",
        "websiteUri": "https://highlandscoffee.com.vn",
    }
    cf = GooglePlacesExtractor().extract(payload)
    assert cf is not None
    assert cf.name == "Highlands Coffee — Tràng Tiền"
    assert cf.location == (21.0247, 105.8556)
    assert cf.address == "1 Tràng Tiền, Hoàn Kiếm, Hà Nội"
    assert cf.phone == "024 1234 5678"
    assert cf.website == "https://highlandscoffee.com.vn"
    assert cf.raw_category == "cafe"


def test_google_no_name_returns_none() -> None:
    assert GooglePlacesExtractor().extract({"id": "ChIJxxx"}) is None


def test_osm_node_with_tags() -> None:
    el = {
        "type": "node",
        "id": 12345,
        "lat": 21.0285,
        "lon": 105.8542,
        "tags": {
            "amenity": "cafe",
            "name": "Cộng Cà Phê",
            "addr:housenumber": "32",
            "addr:street": "Phan Đình Phùng",
            "addr:district": "Ba Đình",
            "addr:city": "Hà Nội",
            "phone": "+84901234567",
            "website": "https://congcaphe.com",
        },
    }
    cf = OSMOverpassExtractor().extract(el)
    assert cf is not None
    assert cf.name == "Cộng Cà Phê"
    assert cf.location == (21.0285, 105.8542)
    assert "Phan Đình Phùng" in (cf.address or "")
    assert "Ba Đình" in (cf.address or "")
    assert cf.phone == "+84901234567"
    assert cf.raw_category == "amenity=cafe"


def test_osm_way_uses_centroid() -> None:
    el = {
        "type": "way",
        "id": 99,
        "center": {"lat": 21.03, "lon": 105.85},
        "tags": {"amenity": "restaurant", "name": "Quán Ăn Ngon"},
    }
    cf = OSMOverpassExtractor().extract(el)
    assert cf is not None
    assert cf.location == (21.03, 105.85)


def test_osm_no_name_returns_none() -> None:
    el = {"type": "node", "id": 1, "lat": 0, "lon": 0, "tags": {"amenity": "cafe"}}
    assert OSMOverpassExtractor().extract(el) is None


def test_registry_lookup() -> None:
    assert isinstance(get_extractor("google_places"), GooglePlacesExtractor)
    assert isinstance(get_extractor("osm_overpass"), OSMOverpassExtractor)


def test_registry_unknown_source() -> None:
    import pytest

    with pytest.raises(KeyError):
        get_extractor("nope")
