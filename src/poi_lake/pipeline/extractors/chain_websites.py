"""Extract canonical fields from a chain store record (see adapters/chain_websites)."""

from __future__ import annotations

from poi_lake.pipeline.extractors.base import CanonicalFields, Extractor


class ChainWebsitesExtractor(Extractor):
    def extract(self, raw_payload: dict) -> CanonicalFields | None:
        name = str(raw_payload.get("name") or "").strip()
        if not name:
            return None
        try:
            location = (float(raw_payload["lat"]), float(raw_payload["lng"]))
        except (KeyError, TypeError, ValueError):
            location = None
        return CanonicalFields(
            name=name,
            location=location,
            address=raw_payload.get("address") or None,
            phone=raw_payload.get("phone") or None,
            raw_category=raw_payload.get("category") or None,
        )
