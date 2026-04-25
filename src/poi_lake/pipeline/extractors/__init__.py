"""Per-source canonical-field extractors.

Each adapter has its own ``raw_payload`` shape; the extractor pulls the
canonical fields the rest of the pipeline expects. Resolved by source code.
"""

from poi_lake.pipeline.extractors.base import CanonicalFields, Extractor
from poi_lake.pipeline.extractors.google_places import GooglePlacesExtractor
from poi_lake.pipeline.extractors.osm_overpass import OSMOverpassExtractor
from poi_lake.pipeline.extractors.registry import get_extractor

__all__ = [
    "CanonicalFields",
    "Extractor",
    "GooglePlacesExtractor",
    "OSMOverpassExtractor",
    "get_extractor",
]
