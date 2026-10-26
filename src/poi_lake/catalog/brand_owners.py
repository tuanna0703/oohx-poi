"""Product brands as shoppers know them, and the company behind each.

Chains tag each product with a brand string ("COCA-COLA", "P/S", "WMHOME").
``brand_owners.csv`` gives the big ones a display name, the company that
owns them (Coca-Cola, Fanta and Sprite → The Coca-Cola Company) and whether
the brand is the chain's own label. A brand not listed keeps its own name,
title-cased, with no company. Slugs are what bizmap's URLs use.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from unidecode import unidecode

_CSV = Path(__file__).with_name("brand_owners.csv")
# Brand strings that mean "no brand": their products are left out of brand pages.
NO_BRAND = {"", "NOBRAND", "NO BRAND", "KHÔNG NHÃN HIỆU"}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unidecode(s.replace("+", " plus ").lower())).strip("-")


@dataclass(frozen=True, slots=True)
class BrandOwner:
    brand: str  # as the chain writes it
    name: str
    slug: str
    company: str | None
    company_slug: str | None
    private_label: bool


@cache
def _table() -> dict[str, BrandOwner]:
    out: dict[str, BrandOwner] = {}
    with _CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            raw = r["brand"].strip().upper()
            company = r["company"].strip() or None
            out[raw] = BrandOwner(
                brand=raw,
                name=r["name"].strip() or raw.title(),
                slug=slugify(r["name"].strip() or raw),
                company=company,
                company_slug=slugify(company) if company else None,
                private_label=r["private_label"].strip() == "1",
            )
    return out


def owner_of(brand: str | None) -> BrandOwner | None:
    """None for a missing or "no brand" string."""
    raw = (brand or "").strip().upper()
    if raw in NO_BRAND:
        return None
    known = _table().get(raw)
    if known:
        return known
    name = raw.title() if raw.isupper() else raw
    return BrandOwner(brand=raw, name=name, slug=slugify(raw), company=None, company_slug=None, private_label=False)
