"""Whether each tagged brand is really a chain, its industry, and its twins.

The brand detector tags a store when every word of an alias appears in its
name, so a common phrase makes a "chain" out of unrelated shops: "Phở Gia
Truyền" held 1,355 stores, mostly "Nhà thuốc gia truyền". And a brand's
category is the code most of its stores got from the per-store keyword
classifier, which files Vinamilk under electronics. This module checks each
brand against its own stores' names, with no person in the loop:

1. Name check: the share of its stores whose name carries the brand's name
   (or a trusted alias: not one made of only some of the brand's own words).
   Under ``MIN_NAME_SHARE`` it is a detector mistake, not a chain ("hide").
2. Industry: Vietnamese trade keywords in the store names (and the brand's
   own name) vote; the vote and the lake's code give the industry and how
   sure we are (high / medium / low).
3. Merge: brands whose names differ only by a format word ("MB ATM",
   "MB Smartbank") are one brand; the one with most stores leads.

The rules are a port of bizmap.live's ``src/lib/brandQuality.ts`` (same
lists, thresholds and tests), so bizmap can read the result here instead of
computing its own. ``run`` only records: it writes ``brand_quality`` and
never changes a tag.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

Verdict = Literal["keep", "hide"]
Confidence = Literal["high", "medium", "low"]

# ---------------------------------------------------------------------------
# Industries: bizmap's grouping of OpenOOH codes (src/lib/brands.ts INDUSTRIES).
# Keep the two in step.

INDUSTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "supermarket": ("Siêu thị & tiện lợi", ("retail.grocery", "retail.convenience_stores", "retail.traditional_grocery", "retail.markets")),
    "mother-baby": ("Mẹ & bé", ("retail.mother_baby",)),
    "pharmacy": ("Nhà thuốc", ("retail.pharmacy",)),
    "clinic": ("Bệnh viện, phòng khám", ("point_of_care",)),
    "electronics": ("Điện máy, điện thoại", ("retail.electronics",)),
    "telecom": ("Viễn thông", ("retail.telecom",)),
    "bank": ("Ngân hàng & ATM", ("financial.banks", "financial.atms", "financial")),
    "consumer-finance": ("Cầm đồ, cho vay", ("financial.pawn_shops",)),
    "fuel": ("Xăng dầu", ("retail.gas_stations",)),
    "vehicles": ("Xe máy, ô tô, trạm sạc", ("retail.motorbike_dealers", "retail.dealerships", "transit.ev_charging", "retail.auto_repair", "retail.motorbike_repair")),
    "mall": ("Trung tâm thương mại", ("retail.shopping_malls",)),
    "fashion": ("Thời trang & trang sức", ("retail.apparel", "retail.jewelry", "retail.eyewear")),
    "cosmetics": ("Mỹ phẩm", ("retail.cosmetics", "health_and_beauty")),
    "coffee": ("Cà phê", ("hospitality.cafes",)),
    "milk-tea": ("Trà sữa, kem", ("hospitality.milk_tea",)),
    "restaurant": ("Nhà hàng & quán nhậu", ("hospitality.restaurants", "hospitality.beer_halls", "hospitality.bars", "hospitality")),
    "fast-food": ("Đồ ăn nhanh & bánh", ("hospitality.fast_food",)),
    "courier": ("Giao nhận", ("retail.courier",)),
    "education": ("Giáo dục", ("education",)),
    "leisure": ("Thể thao, lưu trú, giải trí", ("sports_and_fitness", "travel", "entertainment", "leisure")),
    "misc": ("Chưa phân ngành", ()),
}


def label(industry: str) -> str:
    return INDUSTRIES[industry][0]


def industry_for_code(code: str | None) -> str | None:
    """OpenOOH code ("retail.mother_baby", or a level-1 "education") → industry."""
    if not code:
        return None
    for ind, (_, codes) in INDUSTRIES.items():
        if code in codes:
            return ind
    top = code.split(".")[0]
    for ind, (_, codes) in INDUSTRIES.items():
        if top in codes:
            return ind
    return None


# ---------------------------------------------------------------------------
# Names

_NON_WORD = re.compile(r"[^a-z0-9]+")


def fold(s: str) -> str:
    """No accents, đ → d, lower case (bizmap's ``fold``)."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))
    return s.replace("đ", "d").replace("Đ", "d").lower()


def words(s: str) -> str:
    """Lower case, no accents, words separated by single spaces."""
    return _NON_WORD.sub(" ", fold(s)).strip()


def _compact(w: str) -> str:
    return w.replace(" ", "")


# Words a chain's name carries that its shops often leave out:
# "The Coffee House", "Trung Nguyên Legend".
NAME_FILLER = frozenset({"the", "viet", "nam", "vietnam", "store", "shop", "legend", "official", "jsc", "company"})


def core_words(brand_name: str) -> str:
    """The brand's name without filler words; the whole name when nothing else is left."""
    every = words(brand_name).split(" ")
    core = [w for w in every if w not in NAME_FILLER]
    return " ".join(core or every)


def name_matches(store_name: str, brand_name: str) -> bool:
    """Does a store's name carry the brand's name?

    As a run of words (full name or core), glued ("Win Mart" ~ "WinMart"),
    or, for a core of two or more words of 3+ letters, those words in any
    order ("HEAD Honda" ~ "Honda HEAD"; "La Cà Phê" is not "Phê La").
    """
    s = words(store_name)
    padded = f" {s} "
    tokens = set(s.split(" "))
    for b in dict.fromkeys([words(brand_name), core_words(brand_name)]):
        if not b:
            continue
        if f" {b} " in padded:
            return True
        # Glued or split spellings, only for names long enough not to hide inside other words.
        bc = _compact(b)
        if len(bc) >= 5 and bc in _compact(s):
            return True
        parts = b.split(" ")
        if len(parts) >= 2 and all(len(w) >= 3 for w in parts) and all(w in tokens for w in parts):
            return True
    return False


def carries(store_name: str, brand_name: str, aliases: Sequence[str] = ()) -> bool:
    """Does a store's name carry the brand's name or one of its trusted aliases?"""
    return any(name_matches(store_name, b) for b in (brand_name, *aliases))


def name_share(store_names: Sequence[str], brand_name: str, aliases: Sequence[str] = ()) -> float:
    """Share of names that carry the brand's name (or a trusted alias), 0–1."""
    if not store_names:
        return 0.0
    return sum(1 for n in store_names if carries(n, brand_name, aliases)) / len(store_names)


def partial_aliases(brand_name: str, aliases: Sequence[str]) -> list[str]:
    """Aliases made of only some of the brand's own words: "Galaxy" for
    "Galaxy Cinema", "gia truyền" for "Phở Gia Truyền". They tag every
    "Karaoke Galaxy" and "Nhà thuốc gia truyền", so they are not trusted."""
    own = set(words(brand_name).split())
    out = []
    for a in aliases:
        w = set(words(a).split()) - {""}
        if w and w < own:
            out.append(a)
    return out


def trusted_aliases(brand_name: str, aliases: Sequence[str]) -> list[str]:
    bad = set(partial_aliases(brand_name, aliases))
    return [a for a in aliases if a not in bad]


# ---------------------------------------------------------------------------
# Industry keywords: folded word sequences, most specific industries first;
# a name counts for the first industry it matches. "misc" holds trades with
# no industry of their own (building materials, furniture…).

INDUSTRY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("clinic", ("nha khoa", "phong kham", "benh vien", "da khoa", "clinic", "dental", "xet nghiem", "tham my vien", "y te")),
    ("pharmacy", ("nha thuoc", "quay thuoc", "thuoc tay", "thuoc nam", "dong y", "pharmacy", "pharma", "duoc")),
    ("milk-tea", ("tra sua", "milk tea", "milktea", "tra chanh", "kem")),
    ("coffee", ("ca phe", "cafe", "coffee", "caphe", "cf")),
    ("fast-food", ("ga ran", "pizza", "burger", "banh mi", "bakery", "mi cay", "fast food", "banh ngot", "chicken")),
    ("restaurant", ("nha hang", "pho", "bun", "com", "lau", "nuong", "bia", "quan an", "restaurant", "bbq", "hai san", "chay", "oc")),
    ("consumer-finance", ("cam do", "cho vay", "tai chinh", "credit", "finance")),
    ("bank", ("ngan hang", "bank", "atm", "phong giao dich", "pgd", "smartbank")),
    ("fuel", ("xang dau", "cay xang", "tram xang", "petrolimex", "petro", "pvoil", "xang")),
    ("vehicles", ("xe may", "xe dien", "o to", "oto", "tram sac", "sua xe", "garage", "head", "honda", "yamaha", "vinfast", "yadea", "piaggio", "suzuki", "toyota", "hyundai", "mazda", "kia", "ford", "mitsubishi", "thaco", "lop xe", "phu tung")),
    ("telecom", ("vien thong", "lap mang", "internet", "viettel", "vinaphone", "mobifone", "sim")),
    ("electronics", ("dien may", "dien thoai", "di dong", "mobile", "laptop", "may tinh", "dien tu", "dien lanh", "phone")),
    ("mother-baby", ("me va be", "me be", "baby", "kids", "sua", "bim")),
    ("supermarket", ("sieu thi", "minimart", "mini mart", "mart", "tap hoa", "tien loi", "bach hoa", "thuc pham", "food", "cho")),
    ("mall", ("trung tam thuong mai", "mall", "plaza", "shopping center")),
    ("cosmetics", ("my pham", "cosmetic", "cosmetics", "skincare", "nuoc hoa")),
    ("fashion", ("thoi trang", "fashion", "trang suc", "vang bac", "kinh mat", "giay dep", "tui xach", "dong ho", "quan ao")),
    ("courier", ("express", "giao hang", "buu dien", "buu cuc", "chuyen phat", "logistics", "van chuyen", "post")),
    ("education", ("mam non", "truong", "anh ngu", "tieng anh", "english", "hoc vien", "dao tao", "school", "academy")),
    ("leisure", ("karaoke", "gym", "fitness", "yoga", "khach san", "hotel", "homestay", "cinema", "rap phim", "barber", "salon", "toc", "massage", "spa", "billiards", "bida")),
    # Single words that are also place names (Sơn, Gò, Kính…) are left out.
    # "Tôn" alone is also a street (Tôn Đức Thắng): only as roofing sheet.
    ("misc", ("thep", "ton thep", "ton lop", "ton hoa sen", "vat lieu", "gach", "noi that", "nem", "xi mang", "hoa tuoi", "son nha", "sat thep")),
)


def industry_of_name(name: str) -> str | None:
    """The industry a name's keywords point to, or None."""
    w = f" {words(name)} "
    for industry, keys in INDUSTRY_KEYWORDS:
        if any(f" {k} " in w for k in keys):
            return industry
    return None


def industry_of_brand_name(name: str) -> str | None:
    """A keyword glued into a brand's own name: "LPBank", "MediaMart".

    Weaker than a whole word (MediaMart sells electronics): it only confirms
    an industry the store names or the lake already point to.
    """
    glued = _compact(words(name))
    for industry, keys in INDUSTRY_KEYWORDS:
        if any(" " not in k and len(k) >= 4 and glued.endswith(k) for k in keys):
            return industry
    return None


@dataclass(slots=True)
class Vote:
    industry: str | None
    share: float
    shares: dict[str, float] = field(default_factory=dict)


def industry_vote(store_names: Sequence[str]) -> Vote:
    """The industry most store names point to, the share of all names that do, and every industry's share."""
    votes: dict[str, int] = {}
    for n in store_names:
        i = industry_of_name(n)
        if i:
            votes[i] = votes.get(i, 0) + 1
    best, most = None, 0
    for i, n in votes.items():  # first seen wins a tie, as in bizmap
        if n > most:
            best, most = i, n
    total = len(store_names) or 1
    return Vote(best, most / len(store_names) if store_names else 0.0, {i: n / total for i, n in votes.items()})


# ---------------------------------------------------------------------------
# Merging brand formats

# Words that name a format of a brand, not the brand: "MB ATM", "Hoa Sen Home".
GENERIC_SUFFIX = frozenset({"atm", "smartbank", "home", "store", "shop", "official", "express", "chi nhanh", "cn"})


def canonical_name(name: str) -> str:
    """The brand a name stands for, for merging: "MB ATM", "MB Smartbank" → "mb"."""
    parts = words(name).split(" ")
    while len(parts) > 1 and parts[-1] in GENERIC_SUFFIX:
        parts.pop()
    while len(parts) > 1 and parts[0] in GENERIC_SUFFIX:
        parts.pop(0)
    return " ".join(parts)


def display_name(name: str) -> str:
    """The name without a trailing format word: "Viettel Store" → "Viettel",
    "DongA Bank ATM" → "DongA Bank"; "ATM" stays."""
    parts = name.split()
    while len(parts) > 1 and words(parts[-1]) in GENERIC_SUFFIX:
        parts.pop()
    return " ".join(parts)


@dataclass(slots=True)
class Chain:
    key: str
    name: str
    industry: str
    count: int


def merge_groups(chains: Iterable[Chain]) -> list[list[Chain]]:
    """Brands to merge: same canonical name and industry; the one with most stores leads."""
    groups: dict[str, list[Chain]] = {}
    for c in chains:
        groups.setdefault(f"{canonical_name(c.name)}|{c.industry}", []).append(c)
    return [sorted(g, key=lambda c: -c.count) for g in groups.values() if len(g) > 1]


# ---------------------------------------------------------------------------
# The decision

MIN_NAME_SHARE = 0.4  # below this a "chain" is a detector mistake
STRONG_VOTE = 0.5  # a keyword vote this strong decides the industry on its own
MIN_VOTE = 0.15  # below this a vote is noise (a stray word in a few names)


@dataclass(slots=True)
class Decision:
    verdict: Verdict
    industry: str
    confidence: Confidence
    name_share: float
    vote: Vote
    reason: str  # one line, Vietnamese, for admin pages


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def decide(
    brand_name: str, store_names: Sequence[str], lake_industry: str | None, aliases: Sequence[str] = ()
) -> Decision:
    """What to do with one brand. ``lake_industry`` is what the lake's code says;
    ``aliases`` are its trusted spellings ("Ngân hàng Quân Đội ATM" for MB ATM).

    "keep" + "high" may enter rankings; "medium" shows in its industry; "low"
    shows as misc. No store names is no evidence: nothing is hidden or re-filed.
    """
    if not store_names:
        return Decision("keep", lake_industry or "misc", "low", 0.0, Vote(None, 0.0), "Chưa có danh sách cửa hàng")
    share = name_share(store_names, brand_name, aliases)
    vote = industry_vote(store_names)
    own = industry_of_name(brand_name)
    glued = industry_of_brand_name(brand_name)
    if share < MIN_NAME_SHARE:
        return Decision(
            "hide", vote.industry or lake_industry or "misc", "low", share, vote,
            f"Chỉ {_pct(share)} cửa hàng mang tên “{brand_name}”: không phải một chuỗi",
        )
    lake = lake_industry if lake_industry and lake_industry != "misc" else None
    v = vote.industry if vote.industry and vote.share >= MIN_VOTE else None
    lake_share = vote.shares.get(lake, 0.0) if lake else 0.0
    industry: str
    confidence: Confidence
    if own and own in (lake, v):
        named: str | None = own
    elif glued and glued in (lake, v):
        named = glued
    else:
        named = None
    if named:
        industry, confidence = named, "high"
        reason = f"Tên chuỗi chỉ ngành {label(named)}, {'poi-lake' if named == lake else 'tên cửa hàng'} đồng ý"
    elif v and vote.share >= STRONG_VOTE:
        industry = v
        confidence = "high" if v in (lake, own) or vote.share >= 0.7 else "medium"
        reason = f"{_pct(vote.share)} tên cửa hàng chỉ ngành {label(v)}"
    elif own:
        industry, confidence = own, "medium"
        reason = f"Tên chuỗi chỉ ngành {label(own)}"
    elif v and v != lake and vote.share >= 0.2 and lake_share < vote.share / 3:
        # The lake's code finds almost no support in the names; a clear rival does.
        industry, confidence = v, "medium"
        reason = f"{_pct(vote.share)} tên cửa hàng chỉ ngành {label(v)}, mã poi-lake chỉ {_pct(lake_share)}"
    elif lake:
        industry = lake
        confidence = "high" if v == lake else "medium"
        reason = "Theo mã ngành poi-lake" + (f", {_pct(vote.share)} tên cửa hàng đồng ý" if v == lake else "")
    elif v:
        industry, confidence = v, "medium"
        reason = f"{_pct(vote.share)} tên cửa hàng chỉ ngành {label(v)}"
    else:
        industry, confidence, reason = "misc", "low", "Không xác định được ngành"
    if industry == "misc":
        confidence = "low"
    return Decision("keep", industry, confidence, share, vote, reason)


# ---------------------------------------------------------------------------
# Recording it

_LOAD_SQL = """
SELECT m.brand,
       array_agg(m.canonical_name) FILTER (WHERE m.canonical_name IS NOT NULL) AS names,
       COALESCE(
           mode() WITHIN GROUP (ORDER BY m.openooh_subcategory)
               FILTER (WHERE m.openooh_subcategory IS NOT NULL),
           (SELECT x.category FROM brands x WHERE x.name = m.brand)
       ) AS code,
       (SELECT x.aliases FROM brands x WHERE x.name = m.brand) AS aliases
FROM master_pois m
WHERE m.status = 'active' AND m.brand IS NOT NULL {only}
GROUP BY m.brand
"""

_UPSERT_SQL = """
INSERT INTO brand_quality (brand, display_name, verdict, industry, confidence, name_share,
                           vote, lake_code, reason, stores, matching_stores, computed_at)
VALUES (:brand, :display, :verdict, :industry, :confidence, :share,
        CAST(:vote AS jsonb), :code, :reason, :stores, :matching, NOW())
ON CONFLICT (brand) DO UPDATE SET
    display_name = EXCLUDED.display_name, verdict = EXCLUDED.verdict,
    industry = EXCLUDED.industry, confidence = EXCLUDED.confidence,
    name_share = EXCLUDED.name_share, vote = EXCLUDED.vote, lake_code = EXCLUDED.lake_code,
    reason = EXCLUDED.reason, stores = EXCLUDED.stores,
    matching_stores = EXCLUDED.matching_stores, computed_at = NOW()
"""


async def run(session: AsyncSession, names: Sequence[str] | None = None) -> dict[str, Any]:
    """Assess every tagged brand (or just ``names``) and record it in ``brand_quality``.

    Records only: no tag, category or brand row is changed. A full run also
    drops rows of brands no longer tagged anywhere. Merge suggestions are
    recomputed over every kept brand each time. The caller commits.
    """
    only = "AND m.brand = ANY(:names)" if names is not None else ""
    params: dict[str, Any] = {"names": list(names)} if names is not None else {}
    rows = (await session.execute(text(_LOAD_SQL.format(only=only)), params)).all()
    stats = {"brands": 0, "hide": 0, "high": 0, "medium": 0, "low": 0}
    for r in rows:
        store_names = [n for n in (r.names or []) if n and n.strip()]
        trusted = trusted_aliases(r.brand, list(r.aliases or []))
        d = decide(r.brand, store_names, industry_for_code(r.code), trusted)
        matching = sum(1 for n in store_names if carries(n, r.brand, trusted))
        await session.execute(
            text(_UPSERT_SQL),
            {
                "brand": r.brand, "display": display_name(r.brand), "verdict": d.verdict,
                "industry": d.industry, "confidence": d.confidence, "share": round(d.name_share, 4),
                "vote": json.dumps({"industry": d.vote.industry, "share": round(d.vote.share, 4),
                                    "shares": {k: round(v, 4) for k, v in d.vote.shares.items()}}),
                "code": r.code, "reason": d.reason, "stores": len(store_names), "matching": matching,
            },
        )
        stats["brands"] += 1
        stats["hide" if d.verdict == "hide" else d.confidence] += 1
    if names is None:
        await session.execute(
            text("DELETE FROM brand_quality WHERE brand <> ALL(:seen)"), {"seen": [r.brand for r in rows]}
        )
    stats["merged"] = await _merge_suggestions(session)
    logger.info("brand quality %s", stats)
    return stats


async def _merge_suggestions(session: AsyncSession) -> int:
    kept = (await session.execute(
        text("SELECT brand, industry, stores FROM brand_quality WHERE verdict = 'keep'")
    )).all()
    into: dict[str, str] = {}
    for group in merge_groups(Chain(r.brand, r.brand, r.industry, r.stores) for r in kept):
        for member in group[1:]:
            into[member.key] = group[0].key
    await session.execute(text(
        "UPDATE brand_quality SET merge_into = NULL WHERE merge_into IS NOT NULL AND NOT (brand = ANY(:b))"
    ), {"b": list(into)})
    for brand, leader in into.items():
        await session.execute(
            text("UPDATE brand_quality SET merge_into = :l WHERE brand = :b"), {"l": leader, "b": brand}
        )
    return len(into)
