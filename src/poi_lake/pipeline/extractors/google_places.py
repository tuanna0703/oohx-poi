"""Extract canonical fields from a Google Places (New) v1 ``raw_payload``."""

from __future__ import annotations

from poi_lake.pipeline.extractors.base import CanonicalFields, Extractor


class GooglePlacesExtractor(Extractor):
    def extract(self, raw_payload: dict) -> CanonicalFields | None:
        name = ((raw_payload.get("displayName") or {}).get("text") or "").strip()
        if not name:
            return None

        loc = raw_payload.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        location = (
            (float(lat), float(lng)) if lat is not None and lng is not None else None
        )

        # Prefer national over international — VN users get clean "024 xxxx xxxx"
        phone = (
            raw_payload.get("nationalPhoneNumber")
            or raw_payload.get("internationalPhoneNumber")
        )

        return CanonicalFields(
            name=name,
            location=location,
            address=(raw_payload.get("formattedAddress") or "").strip() or None,
            phone=(phone or "").strip() or None,
            website=(raw_payload.get("websiteUri") or "").strip() or None,
            raw_category=raw_payload.get("primaryType")
            or (raw_payload.get("types") or [None])[0],
        )
