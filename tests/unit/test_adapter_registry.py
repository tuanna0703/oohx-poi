"""Tests for adapter registry resolution."""

from __future__ import annotations

import pytest

from poi_lake.adapters import (
    AdapterConfig,
    SourceAdapter,
    load_adapter_class,
)
from poi_lake.adapters.google_places import GooglePlacesAdapter
from poi_lake.adapters.osm_overpass import OSMOverpassAdapter


def test_resolves_google_places() -> None:
    cls = load_adapter_class("poi_lake.adapters.google_places:GooglePlacesAdapter")
    assert cls is GooglePlacesAdapter
    assert issubclass(cls, SourceAdapter)


def test_resolves_osm_overpass() -> None:
    cls = load_adapter_class("poi_lake.adapters.osm_overpass:OSMOverpassAdapter")
    assert cls is OSMOverpassAdapter


def test_rejects_bad_format() -> None:
    with pytest.raises(ValueError, match="module.path:ClassName"):
        load_adapter_class("poi_lake.adapters.google_places.GooglePlacesAdapter")


def test_rejects_missing_class() -> None:
    with pytest.raises(ImportError):
        load_adapter_class("poi_lake.adapters.google_places:NoSuchAdapter")


def test_rejects_non_adapter() -> None:
    with pytest.raises(TypeError, match="not a SourceAdapter"):
        load_adapter_class("poi_lake.adapters.base:RawPOIRecord")


def test_google_adapter_requires_api_key() -> None:
    from poi_lake.adapters.base import AdapterError

    with pytest.raises(AdapterError, match="GOOGLE_PLACES_API_KEY"):
        GooglePlacesAdapter(AdapterConfig())
