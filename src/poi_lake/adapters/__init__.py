"""Source adapters — each one wraps an external POI source.

All adapters share the contract in :mod:`poi_lake.adapters.base`. Concrete
adapters are looked up via :func:`poi_lake.adapters.registry.load_adapter`
using the ``sources.adapter_class`` import string seeded in the DB.
"""

from poi_lake.adapters.base import (
    AdapterConfig,
    AdapterError,
    AdapterTransientError,
    RawPOIRecord,
    SourceAdapter,
)
from poi_lake.adapters.registry import build_adapter_for_source, load_adapter_class

__all__ = [
    "AdapterConfig",
    "AdapterError",
    "AdapterTransientError",
    "RawPOIRecord",
    "SourceAdapter",
    "build_adapter_for_source",
    "load_adapter_class",
]
