"""Extract canonical fields from a gosom CSV row (already parsed to dict).

CSV columns we care about (gosom emits a longer list — we only pull the
ones the normalize pipeline expects):

    title, address, category, phone, website, latitude, longitude

Any of these may be empty strings (CSV doesn't have NULLs); the extractor
treats empty as missing.
"""

from __future__ import annotations

from poi_lake.pipeline.extractors.base import CanonicalFields, Extractor


def _nz(s: str | None) -> str | None:
    """Coerce empty string to ``None``."""
    if s is None:
        return None
    s = s.strip()
    return s or None


class GosomScraperExtractor(Extractor):
    def extract(self, raw_payload: dict) -> CanonicalFields | None:
        name = _nz(raw_payload.get("title"))
        if not name:
            return None

        lat_s = _nz(raw_payload.get("latitude"))
        lng_s = _nz(raw_payload.get("longitude"))
        location: tuple[float, float] | None = None
        if lat_s and lng_s:
            try:
                location = (float(lat_s), float(lng_s))
            except ValueError:
                location = None

        return CanonicalFields(
            name=name,
            location=location,
            address=_nz(raw_payload.get("address")),
            phone=_nz(raw_payload.get("phone")),
            website=_nz(raw_payload.get("website")),
            raw_category=_nz(raw_payload.get("category")),
        )
