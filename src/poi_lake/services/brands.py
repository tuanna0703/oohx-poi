"""Add a chain without a code change: dictionary entry, re-tag, brand sweep.

bizmap.live's admin adds chains (WiNON, King Food Mart, Sói Biển …) through
``PUT /api/v1/brands/{name}``, which runs the three steps here:

  1. ``upsert_brand`` — the ``brands`` row the BrandDetector matches names
     against (whole-word, accent-folded aliases; no regex needed);
  2. ``retag`` — stamp the brand on rows already crawled: 72 "Kingfoodmart …"
     masters sat untagged on 2026-10-25;
  3. ``add_brand_sweeps`` — a district sweep per province searching the chain
     by name, as ``POST /admin/crawl-plan/brands`` does.

Workers pick the new dictionary up within ``BrandDetector.MAX_AGE_S``.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.pipeline.normalize.brand import BrandDetector
from poi_lake.pipeline.normalize.openooh_keywords import BRAND_PREFIX

logger = logging.getLogger(__name__)

# Accent-folded letter → every Vietnamese form of it, both cases, so Postgres
# can pre-filter names with a regex (the database has no unaccent).
_VARIANTS = {
    "a": "aàáạảãâầấậẩẫăằắặẳẵ",
    "e": "eèéẹẻẽêềếệểễ",
    "i": "iìíịỉĩ",
    "o": "oòóọỏõôồốộổỗơờớợởỡ",
    "u": "uùúụủũưừứựửữ",
    "y": "yỳýỵỷỹ",
    "d": "dđ",
}


def accent_regex(folded_word: str) -> str:
    """``"soi"`` → a POSIX regex matching "soi", "Sói", "SÓI", "sòi" …"""
    out = []
    for ch in folded_word:
        if ch in _VARIANTS:
            v = _VARIANTS[ch]
            out.append(f"[{v}{v.upper()}]")
        elif ch.isalnum():
            out.append(f"[{ch}{ch.upper()}]" if ch.isalpha() else ch)
        else:
            out.append("\\" + ch if not ch.isspace() else "\\s+")
    return "".join(out)


async def upsert_brand(
    session: AsyncSession, name: str, aliases: list[str], category: str | None
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO brands (name, aliases, category, country, match_pattern, enabled)
            VALUES (:name, :aliases, :category, 'VN', NULL, true)
            ON CONFLICT (name) DO UPDATE SET
                aliases = EXCLUDED.aliases,
                category = COALESCE(EXCLUDED.category, brands.category),
                enabled = true
            """
        ),
        {"name": name, "aliases": aliases, "category": category},
    )


async def retag(session: AsyncSession, name: str) -> dict[str, int]:
    """Stamp ``name`` on untagged processed rows and active masters it matches.

    Rows already carrying another brand keep it. A master is tagged when its
    canonical name matches or one of its rows now carries the brand.
    """
    detector = BrandDetector()
    await detector.refresh(session, names=[name])
    aliases = detector.aliases_of(name)
    if not aliases:
        return {"processed": 0, "masters": 0}
    # Pre-filter in SQL — every word of some alias somewhere in the name —
    # then let the detector decide on the few candidates.
    clauses, params = [], {}
    for i, alias in enumerate(aliases):
        words = [w for w in alias.split() if len(w) >= 2]
        if not words:
            continue
        parts = []
        for j, w in enumerate(words):
            params[f"r{i}_{j}"] = accent_regex(w)
            parts.append(f"{{col}} ~ :r{i}_{j}")
        clauses.append("(" + " AND ".join(parts) + ")")
    if not clauses:
        return {"processed": 0, "masters": 0}
    match = " OR ".join(clauses)

    rows = (
        await session.execute(
            text(f"SELECT id, name_original FROM processed_pois WHERE brand IS NULL AND ({match.format(col='name_original')})"),
            params,
        )
    ).all()
    hits = [(r.id, m.confidence) for r in rows if (m := detector.detect(r.name_original))]
    for rid, conf in hits:
        await session.execute(
            text("UPDATE processed_pois SET brand = :b, brand_confidence = :c WHERE id = :id AND brand IS NULL"),
            {"b": name, "c": conf, "id": rid},
        )

    masters = (
        await session.execute(
            text(
                f"""
                SELECT m.id, m.canonical_name,
                       EXISTS (SELECT 1 FROM processed_pois p
                               WHERE p.merged_into = m.id AND p.brand = :b) AS member_hit
                FROM master_pois m
                WHERE m.status = 'active' AND m.brand IS NULL
                  AND (({match.format(col='m.canonical_name')})
                       OR EXISTS (SELECT 1 FROM processed_pois p
                                  WHERE p.merged_into = m.id AND p.brand = :b))
                """
            ),
            {**params, "b": name},
        )
    ).all()
    master_ids = [m.id for m in masters if m.member_hit or detector.detect(m.canonical_name)]
    if master_ids:
        await session.execute(
            text("UPDATE master_pois SET brand = :b WHERE id = ANY(:ids) AND brand IS NULL"),
            {"b": name, "ids": master_ids},
        )
    logger.info("retag %r: %d processed rows, %d masters", name, len(hits), len(master_ids))
    return {"processed": len(hits), "masters": len(master_ids)}


def sweep_code(term: str) -> str:
    return f"{BRAND_PREFIX}{term.strip()}"


async def add_brand_sweeps(
    session: AsyncSession, terms: list[str], province_codes: list[str] | None, priority: int
) -> dict[str, int]:
    """One district-sweep crawl_plan row per (province, term); existing rows kept."""
    codes = [sweep_code(t) for t in terms if t.strip()]
    too_long = [c for c in codes if len(c) > 50]
    if too_long:
        raise ValueError(f"over 50 chars: {too_long}")
    provinces = province_codes or [
        r[0]
        for r in (
            await session.execute(text("SELECT code FROM admin_units WHERE level = 1 ORDER BY code"))
        ).all()
    ]
    inserted = 0
    for prov in provinces:
        for code in codes:
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO crawl_plan (province_code, openooh_code, cell_size_m,
                            priority, status, sweep)
                        VALUES (:prov, :code, 5000, :prio, 'pending', 'district')
                        ON CONFLICT (province_code, openooh_code) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {"prov": prov, "code": code, "prio": priority},
                )
            ).first()
            inserted += row is not None
    return {"rows_added": inserted, "brands": len(codes), "provinces": len(provinces)}


async def brand_progress(session: AsyncSession, name: str, terms: list[str]) -> dict[str, Any]:
    """Sweep rows by status, places found by them, and stores tagged with the brand."""
    codes = [sweep_code(t) for t in terms if t.strip()]
    by_status = dict(
        (
            await session.execute(
                text(
                    "SELECT status, count(*) FROM crawl_plan WHERE openooh_code = ANY(:codes) GROUP BY status"
                ),
                {"codes": codes},
            )
        ).all()
    )
    found = (
        await session.execute(
            text("SELECT coalesce(sum(pois_raw), 0) FROM crawl_plan WHERE openooh_code = ANY(:codes)"),
            {"codes": codes},
        )
    ).scalar_one()
    stores = (
        await session.execute(
            text("SELECT count(*) FROM master_pois WHERE status = 'active' AND brand = :b"),
            {"b": name},
        )
    ).scalar_one()
    total = sum(by_status.values())
    return {
        "brand": name,
        "sweep_rows": total,
        "sweep_done": by_status.get("done", 0),
        "sweep_by_status": by_status,
        "places_found": int(found),
        "stores": int(stores),
    }
