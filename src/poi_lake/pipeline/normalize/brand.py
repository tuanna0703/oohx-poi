"""Brand detection from a normalized name string.

Strategy:
  1. Pre-cache the ``brands`` table (rarely changes).
  2. Try regex match (highest priority — defined explicitly per brand).
  3. Fall back to substring match against the alias list (case + accent-fold).

Returns the canonical brand name + a confidence in [0, 1].
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.db.models import Brand
from poi_lake.pipeline.normalize.text import normalize_text

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


class BrandDetector:
    """Stateful: holds compiled regex + alias index. Reload via ``refresh``."""

    def __init__(self) -> None:
        self._brands: list[_CompiledBrand] = []

    async def refresh(self, session: AsyncSession) -> int:
        """Load enabled brands from DB and pre-compile patterns."""
        rows = (
            await session.execute(
                select(Brand).where(Brand.enabled.is_(True))
            )
        ).scalars().all()
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
            compiled.append(
                _CompiledBrand(name=row.name, aliases_folded=aliases, pattern=pattern)
            )
        self._brands = compiled
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

        # Then alias word-boundary match on the accent-folded form.
        # ``in folded`` was too loose — short aliases like "go" or "phe la"
        # matched inside unrelated words ("a good day" → "Big C", etc.).
        folded = normalize_text(name)
        if not folded:
            return None
        # Tokens of the candidate name; we accept a brand alias only when
        # all its tokens appear as whole words in the candidate.
        tokens = set(folded.split())
        for b in self._brands:
            for alias in b.aliases_folded:
                if not alias:
                    continue
                alias_tokens = alias.split()
                # Reject 1-character aliases entirely — too noisy.
                if any(len(t) < 2 for t in alias_tokens):
                    continue
                if all(t in tokens for t in alias_tokens):
                    conf = 0.9 if folded == alias else 0.75
                    return BrandMatch(name=b.name, confidence=conf)
        return None
