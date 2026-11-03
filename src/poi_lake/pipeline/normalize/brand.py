"""Brand detection from a normalized name string.

Strategy:
  1. Pre-cache the ``brands`` table (rarely changes).
  2. Try regex match (highest priority — defined explicitly per brand).
  3. Fall back to the alias list: the name must carry an alias as a run of
     words, glued ("Win Mart" ~ "WinMart"), or in any order only when the
     alias has two or more words of 3+ letters (``brand_quality.name_matches``,
     the rule the daily brand quality check uses). Any order for short words
     tagged "La Cà Phê" as Phê La and every "Nhà thuốc gia truyền".

Returns the canonical brand name + a confidence in [0, 1].
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.db.models import Brand
from poi_lake.pipeline.normalize.text import normalize_text
from poi_lake.services.brand_quality import name_matches, words

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BrandMatch:
    name: str
    confidence: float


@dataclass(slots=True)
class _CompiledBrand:
    name: str
    aliases_folded: tuple[str, ...]
    pattern: re.Pattern[str] | None
    aliases: tuple[str, ...] = ()


class BrandDetector:
    """Stateful: holds compiled regex + alias index. Reload via ``refresh``."""

    # Brands are added at runtime (bizmap admin → PUT /api/v1/brands/{name});
    # a long-lived worker reloads the dictionary this often.
    MAX_AGE_S = 600

    def __init__(self) -> None:
        self._brands: list[_CompiledBrand] = []
        self._loaded_at = 0.0

    def is_stale(self) -> bool:
        return not self._brands or time.monotonic() - self._loaded_at > self.MAX_AGE_S

    def aliases_of(self, name: str) -> tuple[str, ...]:
        return next((b.aliases_folded for b in self._brands if b.name == name), ())

    async def refresh(self, session: AsyncSession, names: list[str] | None = None) -> int:
        """Load enabled brands (or just ``names``) from DB and pre-compile patterns."""
        query = select(Brand).where(Brand.enabled.is_(True))
        if names is not None:
            query = query.where(Brand.name.in_(names))
        rows = (await session.execute(query)).scalars().all()
        compiled: list[_CompiledBrand] = []
        for row in rows:
            pattern = None
            if row.match_pattern:
                try:
                    pattern = re.compile(row.match_pattern, re.IGNORECASE | re.UNICODE)
                except re.error as exc:
                    logger.warning(
                        "brand %r has invalid regex %r: %s",
                        row.name, row.match_pattern, exc,
                    )
            aliases = tuple(
                {normalize_text(a) for a in [row.name, *row.aliases] if a}
            )
            raw = tuple(dict.fromkeys(a for a in [row.name, *row.aliases] if a))
            compiled.append(
                _CompiledBrand(name=row.name, aliases_folded=aliases, pattern=pattern, aliases=raw)
            )
        self._brands = compiled
        self._loaded_at = time.monotonic()
        logger.info("brand detector loaded %d brands", len(compiled))
        return len(compiled)

    def detect(self, name: str) -> BrandMatch | None:
        """Return the first matching brand, preferring regex matches."""
        if not name or not self._brands:
            return None

        # Try regex matches first (high confidence).
        for b in self._brands:
            if b.pattern and b.pattern.search(name):
                return BrandMatch(name=b.name, confidence=0.95)

        # Then the aliases (the name itself included), whole words on the
        # accent-folded form. ``in folded`` was too loose — short aliases like
        # "go" matched inside unrelated words ("a good day" → "Big C", etc.).
        folded = words(name)
        if not folded:
            return None
        for b in self._brands:
            for alias in b.aliases:
                alias_words = words(alias).split()
                # Reject 1-character alias words entirely — too noisy.
                if not alias_words or any(len(t) < 2 for t in alias_words):
                    continue
                if name_matches(name, alias):
                    conf = 0.9 if folded == " ".join(alias_words) else 0.75
                    return BrandMatch(name=b.name, confidence=conf)
        return None
