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


def test_unimplemented_adapter_says_so() -> None:
    with pytest.raises(ImportError, match="not implemented yet"):
        load_adapter_class("poi_lake.adapters.vietmap:VietmapAdapter")


def test_missing_third_party_import_is_not_relabelled(monkeypatch) -> None:
    """An adapter that exists but imports a missing package keeps its own error."""
    import importlib

    def fake_import(name: str):
        raise ModuleNotFoundError("No module named 'some_dep'", name="some_dep")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ModuleNotFoundError, match="some_dep"):
        load_adapter_class("poi_lake.adapters.google_places:GooglePlacesAdapter")


def test_google_adapter_requires_api_key() -> None:
    from poi_lake.adapters.base import AdapterError

    with pytest.raises(AdapterError, match="GOOGLE_PLACES_API_KEY"):
        GooglePlacesAdapter(AdapterConfig())
