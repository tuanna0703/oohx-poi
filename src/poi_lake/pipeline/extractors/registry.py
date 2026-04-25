"""Extractor lookup by source code."""

from __future__ import annotations

from poi_lake.pipeline.extractors.base import Extractor
from poi_lake.pipeline.extractors.google_places import GooglePlacesExtractor
from poi_lake.pipeline.extractors.osm_overpass import OSMOverpassExtractor

_EXTRACTORS: dict[str, type[Extractor]] = {
    "google_places": GooglePlacesExtractor,
    "osm_overpass": OSMOverpassExtractor,
    # Phase 2b will register: gosom_scraper, vietmap, foody
}


def get_extractor(source_code: str) -> Extractor:
    cls = _EXTRACTORS.get(source_code)
    if cls is None:
        raise KeyError(f"no extractor registered for source code {source_code!r}")
    return cls()


def register_extractor(source_code: str, extractor_cls: type[Extractor]) -> None:
    _EXTRACTORS[source_code] = extractor_cls
