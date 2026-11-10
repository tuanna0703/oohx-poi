"""The chains whose own store lists we read, and how their stores enter the lake."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Chain:
    code: str
    label: str
    brands: tuple[str, ...]  # the brand names its masters carry
    category: tuple[str, str]  # OpenOOH (top, sub) for a store we add
    products: bool  # whether we read what each store lists online
    online_only: bool = False  # one price online, no store list (a virtual "online" store)


CHAINS: dict[str, Chain] = {
    "winmart": Chain(
        "winmart", "WinMart", ("WinMart+", "WinMart"),
        ("retail", "retail.convenience_stores"), products=True,
    ),
    "bachhoaxanh": Chain(
        "bachhoaxanh", "Bách Hoá Xanh", ("Bach Hoa Xanh",),
        ("retail", "retail.grocery"), products=False,
    ),
    # Online prices only, at one virtual store (catalog/concung.py): no
    # store list, no store matched to a master.
    "concung": Chain(
        "concung", "Con Cưng", ("Con Cưng",),
        ("retail", "retail.mother_baby"), products=True, online_only=True,
    ),
    # One online price across the chain (catalog/cellphones.py).
    "cellphones": Chain(
        "cellphones", "CellphoneS", ("CellphoneS",),
        ("retail", "retail.electronics"), products=True, online_only=True,
    ),
    # One online price across the chain (catalog/hoangha.py).
    "hoangha": Chain(
        "hoangha", "Hoàng Hà Mobile", ("Hoàng Hà Mobile",),
        ("retail", "retail.electronics"), products=True, online_only=True,
    ),
    # One online price across the chain (catalog/mediamart.py).
    "mediamart": Chain(
        "mediamart", "MediaMart", ("MediaMart",),
        ("retail", "retail.electronics"), products=True, online_only=True,
    ),
}


def display_name(chain: str, name: str, address: str | None, store_type: str | None) -> str:
    """The name a store added from a chain's list goes by — the brand first,
    so the brand detector tags it, then where it is."""
    street = (address or "").split(",", 1)[0].strip()
    if chain == "bachhoaxanh":
        rest = name[4:].strip() if name.upper().startswith("BHX ") else name
        return f"Bách hoá XANH {rest or street}".strip()
    if chain == "winmart":
        brand = "WinMart+" if store_type == "VMP" else "WinMart"
        # "WM+ THA 520 Nguyễn Trãi" → "520 Nguyễn Trãi": the chain's own name
        # for the store, without its brand and province code.
        rest = re.sub(r"^WM\+?\s+[A-Z]{3}\s+", "", name).strip()
        return f"{brand} {rest if rest and rest != name else street}".strip()
    return name
