"""What every chain's store list yields."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CatalogError(Exception):
    """The chain's API answered something we cannot use."""


class CatalogTransientError(CatalogError):
    """Rate limited or temporarily down — retry later."""


@dataclass(frozen=True, slots=True)
class Store:
    code: str
    name: str
    store_type: str  # WinMart: VMT = supermarket, VMP = WinMart+; BHX: "BHX"
    group_code: str
    address: str
    lat: float
    lng: float
    region_code: str  # the chain's own province code: HCM, 1027…
    raw: dict[str, Any] = field(compare=False, repr=False)
    phone: str | None = None
    open_hours: str | None = None  # as the chain words it
