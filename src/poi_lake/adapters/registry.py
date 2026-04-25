"""Adapter discovery.

The ``sources`` table stores an ``adapter_class`` field as a Python import
string, e.g. ``poi_lake.adapters.google_places:GooglePlacesAdapter``. The
registry resolves that string into a class and instantiates it with the
right :class:`AdapterConfig`.

This is a deliberate alternative to entry-points: with the adapters living
in-tree we don't need installed entry-points, and the import string in the
DB row is self-documenting.
"""

from __future__ import annotations

import importlib

from poi_lake.adapters.base import AdapterConfig, SourceAdapter
from poi_lake.config import get_settings


def load_adapter_class(adapter_class: str) -> type[SourceAdapter]:
    """Resolve ``module.path:ClassName`` into a class object."""
    if ":" not in adapter_class:
        raise ValueError(
            f"adapter_class must be 'module.path:ClassName', got {adapter_class!r}"
        )
    module_path, class_name = adapter_class.split(":", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"{class_name!r} not found in {module_path!r}")
    if not (isinstance(cls, type) and issubclass(cls, SourceAdapter)):
        raise TypeError(f"{adapter_class} is not a SourceAdapter subclass")
    return cls


_ENV_KEY_BY_SOURCE_CODE: dict[str, str] = {
    "google_places": "google_places_api_key",
    "vietmap": "vietmap_api_key",
}


def _resolve_api_key(source_code: str) -> str | None:
    """Pick the right env-injected secret for a source, if any."""
    settings = get_settings()
    attr = _ENV_KEY_BY_SOURCE_CODE.get(source_code)
    if attr is None:
        return None
    secret = getattr(settings, attr, None)
    return secret.get_secret_value() if secret is not None else None


def build_adapter_for_source(source_row: object) -> SourceAdapter:
    """Build an instantiated adapter from a ``Source`` ORM row.

    Pulls config from ``source.config`` JSONB and injects the API key from
    environment (we never store API keys in the DB plaintext).
    """
    cls = load_adapter_class(source_row.adapter_class)  # type: ignore[attr-defined]
    db_config: dict = dict(source_row.config or {})  # type: ignore[attr-defined]
    api_key = _resolve_api_key(source_row.code)  # type: ignore[attr-defined]

    config = AdapterConfig(
        api_key=api_key,
        rate_limit_per_second=db_config.pop("rate_limit_per_second", 1.0),
        timeout_seconds=db_config.pop("timeout_seconds", 30),
        extra=db_config,
    )
    return cls(config)
