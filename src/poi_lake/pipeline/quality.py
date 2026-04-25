"""Quality scoring — 6 weighted factors, output in [0, 1].

Used downstream by:
  * dedupe (Phase 4) when picking canonical values per field;
  * AdTRUE inventory ranking once master_pois are exposed via API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True, frozen=True)
class QualityFactors:
    completeness: float       # fraction of canonical fields populated
    freshness: float          # decay from fetched_at
    source_reliability: float # static per-source baseline
    address_confidence: float # from AddressNormalizer
    phone_valid: float        # 0 or 1 (phone parsed to E.164)
    has_coordinates: float    # 0 or 1


# Per-source reliability priors (lower is less trusted).
# Picked from operational experience with each source on VN data.
_SOURCE_RELIABILITY: dict[str, float] = {
    "google_places": 0.90,
    "vietmap": 0.80,
    "gosom_scraper": 0.75,
    "osm_overpass": 0.70,
    "foody": 0.65,
}


# Weights for the composite score. Sum to 1.0 — easier to reason about.
_WEIGHTS: dict[str, float] = {
    "completeness": 0.25,
    "freshness": 0.10,
    "source_reliability": 0.15,
    "address_confidence": 0.20,
    "phone_valid": 0.10,
    "has_coordinates": 0.20,
}


# How many days until freshness decays to ~0.5. POI metadata (hours, brand,
# phone) drifts on a months-to-years scale, not days.
_FRESHNESS_HALF_LIFE_DAYS = 180.0


class QualityScorer:
    """Stateless scorer. ``score`` returns the composite + per-factor breakdown."""

    def score(
        self,
        *,
        source_code: str,
        fetched_at: datetime,
        has_name: bool,
        has_address: bool,
        has_phone: bool,
        has_website: bool,
        has_coordinates: bool,
        has_category: bool,
        address_confidence: float,
        phone_valid: bool,
    ) -> tuple[float, QualityFactors]:
        completeness = sum(
            [has_name, has_address, has_phone, has_website, has_coordinates, has_category]
        ) / 6.0

        factors = QualityFactors(
            completeness=round(completeness, 3),
            freshness=round(self._freshness(fetched_at), 3),
            source_reliability=_SOURCE_RELIABILITY.get(source_code, 0.5),
            address_confidence=round(max(0.0, min(1.0, address_confidence)), 3),
            phone_valid=1.0 if phone_valid else 0.0,
            has_coordinates=1.0 if has_coordinates else 0.0,
        )
        composite = sum(
            getattr(factors, k) * w for k, w in _WEIGHTS.items()
        )
        return round(composite, 3), factors

    @staticmethod
    def _freshness(fetched_at: datetime) -> float:
        """Exponential decay with half-life ``_FRESHNESS_HALF_LIFE_DAYS``."""
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - fetched_at).total_seconds() / 86400.0)
        return math.pow(0.5, age_days / _FRESHNESS_HALF_LIFE_DAYS)
