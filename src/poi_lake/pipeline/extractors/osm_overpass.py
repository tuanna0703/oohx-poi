"""Extract canonical fields from an OSM Overpass element."""

from __future__ import annotations

from poi_lake.pipeline.extractors.base import CanonicalFields, Extractor

# OSM tag keys we treat as POI-defining, in priority order.
_CATEGORY_KEYS: tuple[str, ...] = ("amenity", "shop", "tourism", "office", "leisure")


class OSMOverpassExtractor(Extractor):
    def extract(self, raw_payload: dict) -> CanonicalFields | None:
        tags = raw_payload.get("tags") or {}
        name = (
            tags.get("name:vi")
            or tags.get("name")
            or tags.get("name:en")
            or ""
        ).strip()
        if not name:
            return None

        # Nodes carry lat/lon; ways/relations carry a centroid in `center`.
        kind = raw_payload.get("type")
        if kind == "node":
            lat = raw_payload.get("lat")
            lon = raw_payload.get("lon")
        else:
            center = raw_payload.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")
        location = (
            (float(lat), float(lon)) if lat is not None and lon is not None else None
        )

        address = self._compose_address(tags)
        phone = (tags.get("phone") or tags.get("contact:phone") or "").strip() or None
        website = (
            tags.get("website")
            or tags.get("contact:website")
            or tags.get("url")
            or ""
        ).strip() or None

        raw_category = None
        for key in _CATEGORY_KEYS:
            if key in tags:
                raw_category = f"{key}={tags[key]}"
                break

        return CanonicalFields(
            name=name,
            location=location,
            address=address,
            phone=phone,
            website=website,
            raw_category=raw_category,
        )

    @staticmethod
    def _compose_address(tags: dict) -> str | None:
        # OSM splits address across many `addr:*` tags. Compose in VN order.
        parts = [
            tags.get("addr:housenumber"),
            tags.get("addr:street"),
            tags.get("addr:subdistrict") or tags.get("addr:hamlet"),
            tags.get("addr:district"),
            tags.get("addr:city") or tags.get("addr:province"),
            tags.get("addr:country"),
        ]
        joined = ", ".join(p.strip() for p in parts if p)
        return joined or None
