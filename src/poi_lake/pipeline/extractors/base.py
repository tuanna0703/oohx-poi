"""Extractor contract.

The extractor produces ``CanonicalFields`` from the source-native
``raw_payload``. The downstream normalizers operate on these canonical
fields, not on the raw payload.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class CanonicalFields:
    """Source-agnostic shape used by the normalize pipeline."""

    name: str
    location: tuple[float, float] | None  # (lat, lng)
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    raw_category: str | None = None  # source-native category string


class Extractor(ABC):
    """Pull ``CanonicalFields`` from a source-specific ``raw_payload``."""

    @abstractmethod
    def extract(self, raw_payload: dict) -> CanonicalFields | None:
        """Return canonical fields, or ``None`` if the payload has no usable name."""
