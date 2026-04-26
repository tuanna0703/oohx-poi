"""Pairwise similarity scoring between two ``processed_pois`` rows.

Composite score per spec section 6.2:

    score = 0.40 * cosine(name_embedding)
          + 0.25 * fuzz.token_set_ratio(address_normalized) / 100
          + 0.15 * (phone_e164 match)
          + 0.10 * (website_domain match)
          + 0.10 * (brand match)

Each component returns 1.0 on a strong match, 0.5 on neutral (one or both
sides missing the field), or a graded value for fuzzy fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz


_W_NAME = 0.40
_W_ADDR = 0.25
_W_PHONE = 0.15
_W_WEBSITE = 0.10
_W_BRAND = 0.10


@dataclass(slots=True, frozen=True)
class PairScore:
    composite: float
    name_similarity: float
    address_similarity: float
    phone_match: float
    website_match: float
    brand_match: float

    def to_dict(self) -> dict[str, float]:
        return {
            "composite": self.composite,
            "name": self.name_similarity,
            "address": self.address_similarity,
            "phone": self.phone_match,
            "website": self.website_match,
            "brand": self.brand_match,
        }


class PairSimilarityScorer:
    """Stateless. Pass two ProcessedPOI rows (or dicts with the same fields)."""

    def score(self, a: Any, b: Any) -> PairScore:
        name_sim = self._cosine(_get_embedding(a), _get_embedding(b))
        addr_sim = self._addr_similarity(_get(a, "address_normalized"), _get(b, "address_normalized"))
        phone = self._equality_or_neutral(_get(a, "phone_e164"), _get(b, "phone_e164"))
        domain = self._equality_or_neutral(_get(a, "website_domain"), _get(b, "website_domain"))
        brand = self._equality_or_neutral(_get(a, "brand"), _get(b, "brand"))

        composite = (
            _W_NAME * name_sim
            + _W_ADDR * addr_sim
            + _W_PHONE * phone
            + _W_WEBSITE * domain
            + _W_BRAND * brand
        )
        return PairScore(
            composite=round(composite, 4),
            name_similarity=round(name_sim, 4),
            address_similarity=round(addr_sim, 4),
            phone_match=phone,
            website_match=domain,
            brand_match=brand,
        )

    # --------------------------------------------------------------- components

    @staticmethod
    def _cosine(a: list[float] | None, b: list[float] | None) -> float:
        # Embeddings come from sentence-transformers with normalize=True, so
        # cosine = dot product. Avoid pulling numpy here — Python is plenty
        # fast for 384-dim vectors and keeps the import graph small.
        if not a or not b:
            return 0.5
        if len(a) != len(b):
            return 0.5
        return max(0.0, min(1.0, sum(x * y for x, y in zip(a, b))))

    @staticmethod
    def _addr_similarity(a: str | None, b: str | None) -> float:
        if not a or not b:
            return 0.5
        # rapidfuzz.token_set_ratio handles word-order and partial matches —
        # ideal for "1 Tràng Tiền, Hoàn Kiếm" vs "Tràng Tiền 1, Hà Nội".
        return fuzz.token_set_ratio(a, b) / 100.0

    @staticmethod
    def _equality_or_neutral(a: Any, b: Any) -> float:
        """1.0 if both present and equal; 0.0 if both present and different;
        0.5 if either side is missing — we don't know, so don't penalize."""
        if not a or not b:
            return 0.5
        return 1.0 if a == b else 0.0


def _get(row: Any, name: str) -> Any:
    """Read attribute or dict key; tolerant of either ORM rows or plain dicts."""
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _get_embedding(row: Any) -> list[float] | None:
    val = _get(row, "name_embedding")
    if val is None:
        return None
    # pgvector returns a list-like; numpy arrays also have __iter__.
    try:
        return [float(x) for x in val]
    except (TypeError, ValueError):
        return None
