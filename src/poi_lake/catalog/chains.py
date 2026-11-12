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
    site: str | None = None  # where its prices are read ("pico.vn"); None: no catalog
    per_store: bool = False  # prices differ by store (WinMart), not one online price
    # Its store list (chain_stores) is every shop it has: a site may show
    # only the masters matched to it. WinMart's lists only the stores that
    # take online orders (~1,900 of ~4,300); Bách Hoá Xanh's is not refreshed.
    official_complete: bool = False


CHAINS: dict[str, Chain] = {
    "winmart": Chain(
        "winmart", "WinMart", ("WinMart+", "WinMart"),
        ("retail", "retail.convenience_stores"), products=True, site="winmart.vn", per_store=True,
    ),
    "bachhoaxanh": Chain(
        "bachhoaxanh", "Bách Hoá Xanh", ("Bach Hoa Xanh",),
        ("retail", "retail.grocery"), products=False,
    ),
    # Online prices only, at one virtual store (catalog/concung.py): no
    # store list, no store matched to a master.
    "concung": Chain(
        "concung", "Con Cưng", ("Con Cưng",),
        ("retail", "retail.mother_baby"), products=True, online_only=True, site="concung.com",
    ),
    # One online price across the chain (catalog/cellphones.py).
    "cellphones": Chain(
        "cellphones", "CellphoneS", ("CellphoneS",),
        ("retail", "retail.electronics"), products=True, online_only=True, site="cellphones.com.vn",
    ),
    # One online price across the chain (catalog/hoangha.py).
    "hoangha": Chain(
        "hoangha", "Hoàng Hà Mobile", ("Hoàng Hà Mobile",),
        ("retail", "retail.electronics"), products=True, online_only=True, site="hoanghamobile.com",
    ),
    # One online price across the chain (catalog/mediamart.py).
    "mediamart": Chain(
        "mediamart", "MediaMart", ("MediaMart",),
        ("retail", "retail.electronics"), products=True, online_only=True, site="mediamart.vn",
    ),
    # One online price across the chain, at a virtual store (catalog/picogroup.py);
    # Pico's own stores come from the group's store list.
    "pico": Chain(
        "pico", "Pico", ("Pico",),
        ("retail", "retail.electronics"), products=True, site="pico.vn", official_complete=True,
    ),
    # Its stores too come from the group's store list (nguyenkim.com/store).
    "nguyenkim": Chain(
        "nguyenkim", "Nguyễn Kim", ("Nguyen Kim",),
        ("retail", "retail.electronics"), products=True, site="nguyenkim.com", official_complete=True,
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
    if chain == "pico":
        # "76 Nguyễn Trãi - HN" → "Điện máy Pico 76 Nguyễn Trãi"
        rest = re.sub(r"\s+-\s+[A-Z]{2,3}$", "", name).strip()
        return f"Điện máy Pico {rest or street}".strip()
    if chain == "nguyenkim":
        # "TTMS SBS Nguyễn Kim Dĩ An", "CTY MTV Cần Thơ", "TTMS NK Nguyễn Duy
        # Trinh" → "Nguyễn Kim Dĩ An", "Nguyễn Kim Cần Thơ", "Nguyễn Kim Nguyễn Duy Trinh"
        rest = re.sub(r"^(?:TTMS(?:\s*&\s*Giải trí)?|SIS|CTY\s+MTV)\s+(?:SBS\s+)?", "", name.strip())
        rest = re.sub(r"^(?:Nguyễn\s+Kim|NK)\s+", "", rest).strip()
        return f"Nguyễn Kim {rest or street}".strip()
    return name
