"""Find chains nobody has named yet among untagged places, score them, act.

Weekly (and on demand from bizmap's admin):

1. **Find.** Every active master without a brand has its name folded
   (lower case, no accents) and its shop-type prefix removed ("Siêu thị Mẹ
   & Bé Con Cưng" → "con cung …"). The first one, two and three words become
   candidate keys; a key seen in enough stores across enough provinces is a
   candidate. A longer key that keeps most of a shorter one's stores wins
   ("con cung" over "con"); keys made only of generic words or place names
   are dropped.
2. **Score** (0-100). Rules give up to 70: stores, provinces, how many of
   the matched stores share one category (purity), how often the name leads
   the store name rather than sitting in the middle (position), minus a
   penalty for a common word. An AI verdict gives up to 30 more; a "not a
   chain" verdict caps the score at 40.
   Rules alone cannot tell "Bia Hơi" from "Bia Craft": the AI decides, and
   a candidate it has not seen yet stays ``new`` (not shown for review).
3. **Decide.** Below ``reject_below``: rejected. At ``add_at`` or more, with
   enough stores and provinces and a positive AI verdict, and auto-add on:
   added, in a batch that can be undone. Everything else waits for review,
   and so does a name that looks generic however high it scores (a dish or
   kind of shop, a place, an owner's name before a shop type, stores of
   unrelated kinds): the AI calls "Gia Truyền", "Phin" or "Dubai" chains.

Adding a brand is what ``PUT /api/v1/brands/{name}`` does: dictionary row,
re-tag of the stores already crawled, a brand sweep (province first). Removing
one undoes all three, except for places already crawled.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.pipeline.normalize.openooh_keywords import BRAND_PREFIX
from poi_lake.services import app_settings, brand_ai
from poi_lake.services.brands import add_brand_sweeps, retag, upsert_brand

logger = logging.getLogger(__name__)

SWEEP_PRIORITY = -90
SEEN_MIN_STORES = 8       # below this a key is not even worth showing
SEEN_MIN_PROVINCES = 2
KEEP_FROM_SCORE = 35      # weaker candidates are not even stored
AI_PER_RUN = 300          # verdicts asked per run, best rule scores first (~$0.06 on Haiku 5.5)
LONGER_KEEPS = 0.6        # a longer key replaces a shorter one keeping this share of its stores

# Shop-type words a store name starts with before its brand (folded).
PREFIXES: tuple[tuple[str, ...], ...] = tuple(
    tuple(p.split())
    for p in sorted(
        [
            "cua hang", "sieu thi", "nha thuoc", "quay thuoc", "ngan hang tmcp", "ngan hang",
            "quan", "tiem", "shop", "cong ty tnhh", "cong ty co phan", "cong ty", "cty",
            "dai ly", "chi nhanh", "cn", "pgd", "phong giao dich", "atm", "showroom",
            "trung tam", "he thong", "nha hang", "ca phe", "cafe", "coffee", "tra sua",
            "khach san", "benh vien", "phong kham", "nha khoa", "cua hang tien loi",
            "sieu thi mini", "sieu thi dien may", "dien may", "thoi trang", "cua hang thoi trang",
            "me va be", "me be", "sieu thi me va be", "tram", "kho", "buu cuc", "van phong",
            # shop types that come before an owner's name, not a brand
            "tap hoa", "tiem tap hoa", "cua hang tap hoa", "hieu thuoc", "vat tu nong nghiep",
            "vat tu", "garage", "gara", "vlxd", "vat lieu xay dung", "uon toc", "hot toc",
            "cat toc", "salon toc", "tiem toc", "dien thoai", "cua hang dien thoai", "thuoc thu y",
            "nha tro", "phong tro", "nha nghi", "ubnd", "uy ban nhan dan", "cong an",
            "quy tin dung", "ngan hang chinh sach", "san van dong", "ban le xang dau", "cay xang",
            "cua hang xang dau", "truong", "truong mam non", "truong tieu hoc", "truong thcs",
            "truong thpt", "mam non", "nha van hoa", "tram y te", "quan an", "quan nhau",
            "com", "pho", "bun", "lau", "karaoke", "spa", "nail", "tiem vang", "hieu vang",
            "sua xe", "sua xe may", "rua xe", "my pham", "shop thoi trang", "nha sach",
            "van phong pham", "photo", "in an", "tiem banh", "banh mi", "nha may", "xuong",
            "cong ty tnhh mtv", "doanh nghiep tu nhan", "dntn", "hkd", "ho kinh doanh",
            "dai ly ve so", "tiem net", "phong gym", "phong tap", "nha hang hai san", "quan com",
        ],
        key=lambda p: -len(p.split()),
    )
)

# Words that alone never make a brand (folded).
_GENERIC_WORDS = """
    cua hang sieu thi nha thuoc quan tiem shop store cong ty dai ly chi nhanh cafe ca phe
    coffee tra sua com pho bun banh mi tap hoa xe may dien thoai thoi trang spa salon toc
    nail karaoke khach san hotel nghi benh vien phong kham khoa truong mam non atm ngan bank
    xang dau the gioi viet nam vn moi new mini mart market center tt tm va and an uong lau
    nuong hai san bia oc ga vit heo bo cua quan ao giay dep my pham thuoc tay rua xe sua chua
    gara oto o to hang hoa thuc pham rau qua trai cay hoa tuoi kinh mat vang bac da quy
    noi that go nhom kinh sat thep vat lieu xay dung dien nuoc may tinh laptop camera
    in an quang cao photo copy giat la ui may mac van phong pham sach nha sach do choi
    tre em me be bau hoc anh ngu tieng anh trung tam day gym yoga fitness the thao bong da
    tennis cau long bi a internet game net cho dem sieu re gia si le kho xuong nha may
    mot hai ba bon nam sau bay tam chin muoi so so1 so2 co hung thinh phat loc tai an phu
    minh hoang long duc huy thanh tuan anh linh hai ngoc phuong tien dat trung quang
    truong thcs thpt tieu mau giao ubnd xa chinh tin dong garage vlxd uon hot cat thu y tro
    nong vat tu di ban gia dinh nha tro san van ho ut kim son thuy hien thu hong ha khanh
    nguyen bao binh thang xuan mai yen phuc tuyet tan lan vinh ong hanh bich hieu cuong quoc
    nga nhu kieu chu huynh quynh tran le pham phan vu vo dang bui do ngo duong lam trinh dinh
    tam thao trang hoa hue nhung dung hang loan oanh van chi diep tuyen hung khoa kiet phat
    city food kids home house group mobile shop store express plus pro vip one top best
    hanoi saigon hcm da nang hue hai phong can tho
"""
GENERIC = frozenset(_GENERIC_WORDS.split())

# Words that say what kind of shop or dish it is, not whose it is (folded).
# Matched as whole words anywhere in the name; "tay" and "sua" only first
# ("[nhà thuốc] tây Minh Châu", "sữa DHA Max").
DESCRIPTORS: tuple[tuple[str, ...], ...] = tuple(
    tuple(p.split())
    for p in (
        "gia truyen", "bao cap", "phin", "dat chuan", "gpp", "dien lanh", "co dien", "de nui",
        "burger", "head", "the gioi", "di dong", "da khoa", "van hoa", "dac san", "truyen thong",
    )
)
FIRST_DESCRIPTORS = frozenset({"tay", "sua", "duoc"})
# Countries and big cities in a name: "Việt Pháp", "Dubai", "Dược Hà Nội".
FOREIGN_PLACES: tuple[tuple[str, ...], ...] = tuple(
    tuple(p.split())
    for p in ("dubai", "phap", "nhat ban", "han quoc", "thai lan", "trung quoc", "sai gon", "ha noi", "viet nam")
)
# Shop types after an owner's name: "Hoàng Barber", "An Coffee Tea", "K Market".
SHOP_TYPES = frozenset(
    ["barber", "coffee", "cafe", "tea", "store", "mart", "market", "shop", "salon", "hair", "spa", "house", "quan"]
)
MIXED_BELOW = 0.65  # share of a candidate's stores in its main kind of business


def _has(words: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return any(words[i: i + n] == phrase for i in range(len(words) - n + 1))


def generic_reason(words: tuple[str, ...], places: frozenset[str], group_purity: float | None) -> str | None:
    """Why a candidate's name looks generic rather than a chain's, or None."""
    if not words:
        return None
    for d in DESCRIPTORS:
        if _has(words, d):
            return f'chứa từ chỉ loại hàng, món ăn hoặc loại cửa hàng ("{" ".join(d)}")'
    if words[0] in FIRST_DESCRIPTORS:
        return f'bắt đầu bằng từ chỉ loại hàng ("{words[0]}")'
    for f in FOREIGN_PLACES:
        if _has(words, f):
            return f'chứa tên nước hoặc thành phố ("{" ".join(f)}")'
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            w = " ".join(words[i: i + n])
            if w in places and len(w) > 3:
                return f'chứa tên địa phương ("{w}")'
    if len(words) >= 2 and (words[0] in GENERIC or len(words[0]) == 1) and all(
        w in SHOP_TYPES or w in GENERIC for w in words[1:]
    ):
        return "giống tên chủ quán đi với loại cửa hàng"
    if group_purity is not None and group_purity < MIXED_BELOW:
        return f"chỉ {round(100 * group_purity)}% điểm khớp cùng một ngành, còn lại là ngành khác"
    return None


PLACE_PREFIXES = ("tinh", "thanh pho", "tp", "quan", "huyen", "thi xa", "thi tran", "phuong", "xa")

_WORD = re.compile(r"\w+", re.UNICODE)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def tokens(name: str) -> tuple[list[str], list[str]]:
    """``(folded, original)`` word lists of a store name."""
    # Some sources store decomposed accents (NFD); \w would split words there.
    original = _WORD.findall(unicodedata.normalize("NFC", name))
    return [fold(w) for w in original], original


def strip_prefix(folded: list[str]) -> int:
    """How many leading words are shop-type words ("siêu thị", "nhà thuốc" …)."""
    start = 0
    for _ in range(3):
        for p in PREFIXES:
            if tuple(folded[start: start + len(p)]) == p:
                start += len(p)
                break
        else:
            break
    return start


def keys_of(folded: list[str], start: int) -> list[tuple[str, ...]]:
    """The first one, two and three words after the prefix, as candidate keys."""
    out = []
    for n in (1, 2, 3):
        k = tuple(folded[start: start + n])
        if len(k) < n:
            break
        if all(w in GENERIC or w.isdigit() for w in k):
            continue
        if len("".join(k)) < 3:
            continue
        out.append(k)
    return out


def place_names(rows: list[str]) -> frozenset[str]:
    """Folded names of provinces and districts, without "tỉnh", "quận" …"""
    out = set()
    for name in rows:
        f = " ".join(tokens(name)[0])
        for p in PLACE_PREFIXES:
            if f.startswith(p + " "):
                f = f[len(p) + 1:]
                break
        out.add(f)
    return frozenset(out)


def prune_keys(counts: dict[tuple[str, ...], int]) -> set[tuple[str, ...]]:
    """Keep the most specific key of each family.

    "con cung" (446) beats "con" when it keeps most of its stores; "con cung
    ha" (12) loses to "con cung" because it doesn't.
    """
    keep = set(counts)
    for k in sorted(counts, key=len, reverse=True):
        if len(k) == 1 or k not in keep:
            continue
        parent = k[:-1]
        if parent not in counts:
            continue
        if counts[k] >= LONGER_KEEPS * counts[parent]:
            keep.discard(parent)
        else:
            keep.discard(k)
    return keep


def rule_score(stores: int, provinces: int, purity: float, position: float, common: bool) -> int:
    s = 20 * min(1.0, math.log10(max(stores, 1)) / math.log10(200))
    s += 10 * min(1.0, provinces / 10)
    s += 20 * purity
    s += 15 * position
    s += 5 if not common else -10
    return max(0, min(70, round(s)))


def total_score(rules: int, ai: dict[str, Any] | None) -> int:
    if not ai:
        return rules
    if not ai.get("is_chain"):
        return min(rules, 40)
    return min(100, rules + round(30 * float(ai.get("confidence") or 0)))


def decide(cand: dict[str, Any], settings: dict[str, Any]) -> str:
    if cand["score"] < settings["reject_below"]:
        return "auto_rejected"
    ai = cand.get("ai") or {}
    if (
        settings["auto_add"]
        and cand["score"] >= settings["add_at"]
        and cand["stores"] >= settings["min_stores"]
        and cand["provinces"] >= settings["min_provinces"]
        and ai.get("is_chain")
    ):
        return "auto_added"
    return "pending"


def signals_of(stores: int, provinces: int, purity: float, top_cat: str | None,
               position: float, common: bool, settings: dict[str, Any]) -> list[dict[str, Any]]:
    pct = lambda x: f"{round(100 * x)}%"  # noqa: E731
    return [
        {"label": "Độ thuần ngành", "good": purity >= 0.8,
         "text": f"{pct(purity)} điểm khớp cùng loại {top_cat or 'không rõ'}"},
        {"label": "Vị trí tên", "good": position >= 0.8,
         "text": f"tên thương hiệu đứng đầu ở {pct(position)} điểm có chứa nó"},
        {"label": "Từ thông dụng", "good": not common,
         "text": "là từ hay gặp trong tên cửa hàng khác, dễ khớp nhầm" if common else "không phải từ thông dụng"},
        {"label": "Độ phủ", "good": stores >= settings["min_stores"] and provinces >= settings["min_provinces"],
         "text": f"{stores} cửa hàng ở {provinces} tỉnh"},
    ]


async def find(session: AsyncSession, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidates among active untagged masters, with their evidence."""
    rows = (
        await session.execute(
            text(
                "SELECT id, canonical_name, province_code, "
                "COALESCE(openooh_subcategory, openooh_category) AS cat "
                "FROM master_pois WHERE status = 'active' AND brand IS NULL"
            )
        )
    ).all()
    places = place_names(
        [r[0] for r in (await session.execute(text("SELECT name FROM admin_units WHERE level IN (1, 2)"))).all()]
    )
    known = {
        " ".join(tokens(n)[0])
        for (n,) in (await session.execute(text("SELECT name FROM brands"))).all()
    }

    parsed = []
    counts: Counter[tuple[str, ...]] = Counter()
    for r in rows:
        folded, original = tokens(r.canonical_name or "")
        start = strip_prefix(folded)
        ks = keys_of(folded, start)
        parsed.append((r, folded, original, start, ks))
        counts.update(ks)

    floor = SEEN_MIN_STORES
    frequent = {k: n for k, n in counts.items() if n >= floor}
    keep = {k for k in prune_keys(frequent) if " ".join(k) not in places and " ".join(k) not in known}

    stats: dict[tuple[str, ...], dict[str, Any]] = defaultdict(
        lambda: {"ids": [], "prov": set(), "cats": Counter(), "orig": Counter(), "rows": []}
    )
    leads = {k[0] for k in keep}
    first_word: dict[str, list[int]] = defaultdict(list)  # word → parsed index, for "anywhere" matches
    for i, (r, folded, original, start, ks) in enumerate(parsed):
        for w in set(folded) & leads:
            first_word[w].append(i)
        for k in ks:
            if k in keep:
                s = stats[k]
                s["ids"].append(r.id)
                if r.province_code:
                    s["prov"].add(r.province_code)
                s["cats"][r.cat] += 1
                s["orig"][" ".join(original[start: start + len(k)])] += 1
                s["rows"].append(i)

    out = []
    for k, s in stats.items():
        stores, provinces = len(s["ids"]), len(s["prov"])
        if stores < floor or provinces < SEEN_MIN_PROVINCES:
            continue
        top_cat, top_n = s["cats"].most_common(1)[0]
        purity = top_n / stores
        groups: Counter[str] = Counter()
        for cat, n in s["cats"].items():
            groups[(cat or "").split(".")[0]] += n
        group_purity = groups.most_common(1)[0][1] / stores
        needle = " " + " ".join(k) + " "
        anywhere = [i for i in first_word[k[0]] if needle in " " + " ".join(parsed[i][1]) + " "]
        lead = set(s["rows"])
        position = stores / max(len(anywhere), stores)
        common = len(k) == 1 and (len(k[0]) <= 3 or len(anywhere) > 3 * stores)
        name = s["orig"].most_common(1)[0][0]

        samples = []
        for i in s["rows"][:5]:
            r = parsed[i][0]
            ok = r.cat == top_cat
            samples.append({"name": r.canonical_name, "category": r.cat, "ok": ok,
                            "why": None if ok else "khác ngành"})
        for i in [i for i in anywhere if i not in lead][:3]:
            r = parsed[i][0]
            samples.append({"name": r.canonical_name, "category": r.cat, "ok": False, "why": "tên nằm giữa"})

        rules = rule_score(stores, provinces, purity, position, common)
        if rules < KEEP_FROM_SCORE:
            continue
        out.append({
            "key": " ".join(k), "name": name, "stores": stores, "provinces": provinces,
            "category": (top_cat or "retail").split(".")[0], "sector": top_cat,
            "aliases": [a for a, _ in s["orig"].most_common(4) if a != name][:3],
            "search_term": name[:40], "rules": rules,
            "signals": signals_of(stores, provinces, purity, top_cat, position, common, settings),
            "samples": samples,
            "generic": generic_reason(k, places, group_purity),
        })
    out.sort(key=lambda c: -c["rules"])
    return out


async def _store(session: AsyncSession, c: dict[str, Any]) -> None:
    await session.execute(
        text(
            """
            INSERT INTO brand_candidates (key, name, category, aliases, search_term, stores,
                provinces, score, signals, samples, ai, status, updated_at)
            VALUES (:key, :name, :category, CAST(:aliases AS jsonb), :term, :stores, :provinces,
                :score, CAST(:signals AS jsonb), CAST(:samples AS jsonb), CAST(:ai AS jsonb),
                :status, NOW())
            ON CONFLICT (key) DO UPDATE SET
                stores = EXCLUDED.stores, provinces = EXCLUDED.provinces, score = EXCLUDED.score,
                signals = EXCLUDED.signals, samples = EXCLUDED.samples,
                ai = COALESCE(EXCLUDED.ai, brand_candidates.ai),
                status = EXCLUDED.status, updated_at = NOW()
            WHERE brand_candidates.status IN ('new', 'pending')
            """
        ),
        {
            "key": c["key"], "name": c["name"], "category": c["category"],
            "aliases": json.dumps(c["aliases"], ensure_ascii=False), "term": c["search_term"],
            "stores": c["stores"], "provinces": c["provinces"], "score": c["score"],
            "signals": json.dumps(c["signals"], ensure_ascii=False),
            "samples": json.dumps(c["samples"], ensure_ascii=False),
            "ai": json.dumps(c["ai"], ensure_ascii=False) if c.get("ai") else None,
            "status": c["status"],
        },
    )


async def run(session: AsyncSession, *, use_ai: bool = True) -> dict[str, Any]:
    """One discovery pass. Commits as it goes; returns what it did."""
    settings = await app_settings.discovery(session)
    found = await find(session, settings)
    decided = {
        r.key: (r.status, r.ai)
        for r in (await session.execute(text("SELECT key, status, ai FROM brand_candidates"))).all()
    }
    batch = "auto-" + datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y%m%d-%H%M")
    summary: Counter[str] = Counter()
    asked = 0
    ai_down = not use_ai
    # One candidate per chain: "Hoarosa" and "Hoarosa Shop Hoa", "DongA Bank" and
    # "Donga Bank" are the same once the AI names them. And a chain the
    # dictionary already has ("MB ATM" is MB Bank) is offered as a merge.
    known = {_folded(n): n for (n,) in (await session.execute(text("SELECT name FROM brands"))).all()}
    kept: dict[str, dict[str, Any]] = {}
    for c in found:
        status, ai = decided.get(c["key"], (None, None))
        if status not in (None, "new", "pending"):
            continue  # an admin (or an earlier run) already settled it
        c["ai"] = ai
        c["original"] = c["name"]
        if not ai and asked >= AI_PER_RUN:
            # Not this run's turn for an AI verdict: keep it out of the review list.
            c["score"], c["status"] = c["rules"], "new"
            await _store(session, c)
            summary["new"] += 1
            continue
        if not ai and not ai_down:
            asked += 1
            try:
                c["ai"] = (await brand_ai.verify(session, c, settings)).as_json()
            except brand_ai.AiUnavailableError as exc:
                # Budget spent or no provider: the rest of this run goes without.
                ai_down = True
                summary["ai_unavailable"] += 1
                logger.info("brand discovery: AI unavailable from %r on: %s", c["name"], exc)
        elif not ai:
            asked += 1
        if c["ai"]:
            c["name"] = c["ai"]["canonical_name"] or c["name"]
            c["category"] = c["ai"]["category"]
            c["search_term"] = c["ai"]["search_term"] or c["search_term"]
            c["aliases"] = list(dict.fromkeys([*c["ai"]["aliases"], *c["aliases"]]))[:5]
        c["score"] = total_score(c["rules"], c["ai"])
        c["status"] = decide(c, settings)
        why = c.get("generic") or generic_reason(tuple(tokens(c["name"])[0]), frozenset(), None)
        if why:
            c["signals"] = [*c["signals"], {"label": "Tên chung chung", "good": False,
                                            "text": f"{why}: cần người duyệt"}]
            if c["status"] == "auto_added":
                c["status"] = "pending"
        chain = _folded(c["name"])
        if c["ai"] and chain in known:
            c["ai"]["existing_brand"] = known[chain]
            if c["status"] == "auto_added":
                c["status"] = "pending"  # merging into a brand is an admin's call
        elif c["ai"] and chain in kept and c["status"] != "auto_rejected":
            first = kept[chain]
            c["status"] = "auto_rejected"
            c["signals"] = [*c["signals"], {"label": "Trùng", "good": False,
                                            "text": f"cùng chuỗi với ứng viên {first['name']}"}]
            await _add_alias(session, first["key"], c["original"])
        elif c["ai"] and c["status"] != "auto_rejected":
            kept[chain] = c
        await _store(session, c)
        if c["status"] == "auto_added":
            cid = (await session.execute(
                text("SELECT id FROM brand_candidates WHERE key = :k"), {"k": c["key"]}
            )).scalar_one()
            await apply(session, int(cid), batch=batch, status="auto_added")
        await session.commit()
        summary[c["status"]] += 1
    summary["found"] = len(found)
    logger.info("brand discovery: %s", dict(summary))
    return {**dict(summary), "batch": batch}


def _folded(name: str) -> str:
    return " ".join(tokens(name)[0])


async def _add_alias(session: AsyncSession, key: str, alias: str) -> None:
    """Another spelling of a kept candidate's chain, seen as a duplicate candidate."""
    await session.execute(
        text(
            """
            UPDATE brand_candidates SET aliases = aliases || to_jsonb(CAST(:a AS text))
            WHERE key = :k AND NOT aliases ? :a AND jsonb_array_length(aliases) < 10
            """
        ),
        {"k": key, "a": alias},
    )


async def _name_taken(session: AsyncSession, name: str) -> bool:
    return bool((await session.execute(
        text("SELECT 1 FROM brands WHERE lower(name) = lower(:n)"), {"n": name}
    )).first())


async def apply(session: AsyncSession, cid: int, *, batch: str, status: str,
                name: str | None = None, aliases: list[str] | None = None,
                term: str | None = None, category: str | None = None) -> dict[str, Any]:
    """Add the candidate as a brand: dictionary, re-tag, sweep. Caller commits."""
    row = (await session.execute(
        text("SELECT key, name, aliases, search_term, category FROM brand_candidates WHERE id = :i"), {"i": cid}
    )).one()
    found_as = row.name
    name = (name or row.name).strip()
    aliases = aliases if aliases is not None else list(row.aliases or [])
    term = (term or row.search_term or name).strip()[:40]
    category = category or row.category
    existing = (await session.execute(
        text("SELECT name, aliases FROM brands WHERE lower(name) = lower(:n)"), {"n": name}
    )).first()
    if existing:
        # A new spelling of a brand the dictionary has ("MB ATM" → MB Bank):
        # add the spellings, re-tag; its own sweeps already run.
        name = existing.name
        merged = list(dict.fromkeys([*(existing.aliases or []), *aliases, found_as]))
        merged = [a for a in merged if a.lower() != name.lower()][:20]
        await upsert_brand(session, name, merged, None)
        tagged = await retag(session, name)
        sweep = {"rows_added": 0}
    else:
        if found_as.lower() != name.lower() and found_as not in aliases:
            aliases = [*aliases, found_as]
        await upsert_brand(session, name, aliases, category)
        tagged = await retag(session, name)
        sweep = await add_brand_sweeps(session, [term], None, SWEEP_PRIORITY)
    await session.execute(
        text(
            """
            UPDATE brand_candidates SET name = :n, aliases = CAST(:a AS jsonb), search_term = :t,
                category = :c, status = :s, batch = :b, tagged = :g, sweep_rows = :r,
                decided_at = NOW(), updated_at = NOW()
            WHERE id = :i
            """
        ),
        {"n": name, "a": json.dumps(aliases, ensure_ascii=False), "t": term, "c": category,
         "s": status, "b": batch, "g": tagged["masters"], "r": sweep["rows_added"], "i": cid},
    )
    return {"brand": name, "tagged": tagged["masters"], "sweep_rows": sweep["rows_added"]}


async def remove(session: AsyncSession, cid: int) -> dict[str, Any]:
    """Undo ``apply``: untag, drop the dictionary row, cancel sweeps not yet
    run. Places already crawled stay in the lake. Caller commits."""
    row = (await session.execute(
        text("SELECT name, search_term, status FROM brand_candidates WHERE id = :i"), {"i": cid}
    )).one()
    if row.status not in ("added", "auto_added"):
        return {"removed": 0}
    merged_into = (await session.execute(
        text("SELECT ai->>'existing_brand' FROM brand_candidates WHERE id = :i"), {"i": cid}
    )).scalar_one()
    if merged_into:
        # The brand was there before: it stays, with the spellings and stores
        # the merge gave it (take a spelling out in the dictionary if it was wrong).
        await session.execute(
            text(
                """
                UPDATE brand_candidates SET status = 'removed', decided_at = NOW(), updated_at = NOW()
                WHERE id = :i
                """
            ),
            {"i": cid},
        )
        return {"removed": 1, "untagged": 0, "sweeps_cancelled": 0, "kept_brand": merged_into}
    p = {"b": row.name}
    masters = (await session.execute(text("UPDATE master_pois SET brand = NULL WHERE brand = :b"), p)).rowcount
    await session.execute(text("UPDATE processed_pois SET brand = NULL, brand_confidence = NULL WHERE brand = :b"), p)
    await session.execute(text("DELETE FROM brands WHERE name = :b"), p)
    cancelled = (await session.execute(
        text("DELETE FROM crawl_plan WHERE openooh_code = :c AND status = 'pending' AND cells_enqueued = 0"),
        {"c": BRAND_PREFIX + row.search_term},
    )).rowcount
    await session.execute(
        text("UPDATE crawl_plan SET status = 'paused' WHERE openooh_code = :c AND status IN ('pending', 'in_progress')"),
        {"c": BRAND_PREFIX + row.search_term},
    )
    await session.execute(
        text("UPDATE brand_candidates SET status = 'removed', decided_at = NOW(), updated_at = NOW() WHERE id = :i"),
        {"i": cid},
    )
    return {"removed": 1, "untagged": masters, "sweeps_cancelled": cancelled}


async def undo_batch(session: AsyncSession, batch: str) -> dict[str, Any]:
    ids = [r[0] for r in (await session.execute(
        text("SELECT id FROM brand_candidates WHERE batch = :b AND status IN ('added', 'auto_added')"), {"b": batch}
    )).all()]
    for cid in ids:
        await remove(session, int(cid))
    return {"removed": len(ids)}


async def rescan_brands(session: AsyncSession, days: int) -> int:
    """Re-open brand sweeps finished more than ``days`` ago, province first
    again, to catch stores opened since. Caller commits."""
    if days <= 0:
        return 0
    return (await session.execute(
        text(
            """
            UPDATE crawl_plan SET status = 'pending', sweep = 'district', cells_total = NULL,
                cells_enqueued = 0, cells_done = 0, cells_failed = 0, cells_skipped = 0,
                completed_at = NULL
            WHERE openooh_code LIKE :like AND status = 'done'
              AND completed_at < NOW() - make_interval(days => :d)
            """
        ),
        {"like": BRAND_PREFIX + "%", "d": days},
    )).rowcount
