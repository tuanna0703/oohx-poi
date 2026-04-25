"""Deterministic content hashing for raw POI payloads.

Used by the IngestionService to dedup ``raw_pois`` inserts: the unique
constraint is ``(source_id, source_poi_id, content_hash)`` so the same
payload won't be inserted twice, while a real change (different hash) is
preserved as a new row.

The hash is intentionally over the entire ``raw_payload`` — adapters decide
what to include; the service treats the dict as opaque.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(payload: dict[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON form of ``payload``.

    Canonicalization:
      * Sort keys at every depth.
      * Compact separators (no spaces).
      * UTF-8 encoding (we keep Unicode characters literal — important for VN names).
    """
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(obj: Any) -> Any:
    # Tuples (e.g. coordinates) → list; sets → sorted list; everything else fails loudly.
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"non-JSON-serializable type {type(obj).__name__} in raw payload")
