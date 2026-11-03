"""Act on the brand quality check: untag stores that do not carry their brand.

``brand_quality.run`` only records. This module changes tags, in three ways:

* For a brand judged "hide" (few of its stores carry its name), an alias made
  of only some of the brand's own words ("Galaxy" for Galaxy Cinema, "gia
  truyền" for Phở Gia Truyền) is dropped from the dictionary: it is what
  tagged every "Karaoke Galaxy" and "Nhà thuốc gia truyền". A kept brand
  keeps such aliases ("Highlands" for Highlands Coffee is right).
* A store whose name carries neither the brand's name, a trusted alias nor
  its regex loses the tag, on the processed rows and on the master (a master
  keeps it while one of its rows still carries the name: the merge would
  stamp it back otherwise).
* A brand none of whose stores carries its name is disabled in the dictionary
  so the detector stops using it. A "hide" brand that some stores do carry
  (Galaxy Cinema, Phong Vũ, Phê La) keeps those: a generic-looking name is
  not reason enough, it also caught real chains.

Every change is logged in ``brand_cleanup_log`` with what was there before,
so ``undo`` puts a run back. ``plan`` is the same pass without writing.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.services import app_settings, brand_quality

logger = logging.getLogger(__name__)

SETTINGS_KEY = "brand_quality"
DEFAULTS: dict[str, Any] = {"apply": False}  # daily run untags only once an admin turns this on

_CHUNK = 5000


@dataclass(slots=True)
class BrandPlan:
    brand: str
    action: str  # "disable" | "trim"
    reason: str
    kept: int = 0
    samples: list[str] = field(default_factory=list)
    drop_aliases: list[str] = field(default_factory=list)
    processed: list[int] = field(default_factory=list)
    masters: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "brand": self.brand, "action": self.action, "reason": self.reason,
            "drop_aliases": self.drop_aliases,
            "untag_processed": len(self.processed), "untag_masters": len(self.masters),
            "kept_masters": self.kept, "untag_samples": self.samples,
        }


def _carrier(name: str, aliases: list[str], pattern: str | None) -> Callable[[str | None], bool]:
    rx = None
    if pattern:
        try:
            rx = re.compile(pattern, re.IGNORECASE | re.UNICODE)
        except re.error:
            rx = None

    def carries(store: str | None) -> bool:
        if not store:
            return False
        return bool(rx and rx.search(store)) or brand_quality.carries(store, name, aliases)

    return carries


async def build_plan(session: AsyncSession) -> list[BrandPlan]:
    """What a clean-up would change, brand by brand (brands with no change left out).

    Re-assesses every brand first, so verdicts follow today's rules and
    dictionary; ``plan`` does not commit, so its re-assessment is discarded.
    """
    await brand_quality.run(session)
    dictionary = {
        r.name: r
        for r in (await session.execute(
            text("SELECT name, aliases, match_pattern FROM brands WHERE enabled")
        )).all()
    }
    verdicts = dict((await session.execute(text("SELECT brand, verdict FROM brand_quality"))).all())

    processed: dict[str, list[tuple[int, str | None, str | None]]] = defaultdict(list)
    for r in (await session.execute(text(
        "SELECT id, brand, name_original, merged_into::text AS m FROM processed_pois WHERE brand IS NOT NULL"
    ))).all():
        processed[r.brand].append((r.id, r.name_original, r.m))
    masters: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for r in (await session.execute(text(
        "SELECT id::text AS id, brand, canonical_name FROM master_pois "
        "WHERE status = 'active' AND brand IS NOT NULL"
    ))).all():
        masters[r.brand].append((r.id, r.canonical_name))

    plans: list[BrandPlan] = []
    for name, entry in dictionary.items():
        rows, ms = processed.get(name, []), masters.get(name, [])
        if not rows and not ms:
            continue
        aliases = list(entry.aliases or [])
        drop = brand_quality.partial_aliases(name, aliases) if verdicts.get(name) == "hide" else []
        carries = _carrier(name, [a for a in aliases if a not in drop], entry.match_pattern)
        untag_rows = [r[0] for r in rows if not carries(r[1])]
        kept_members = {r[2] for r in rows if r[2] and carries(r[1])}
        untag_ms = [m[0] for m in ms if m[0] not in kept_members and not carries(m[1])]
        gone = set(untag_ms)
        samples = [m[1] or "" for m in ms if m[0] in gone][:5]
        if len(untag_rows) == len(rows) and len(untag_ms) == len(ms):
            plans.append(BrandPlan(
                name, "disable", "Không cửa hàng nào mang tên chuỗi", kept=0, samples=samples,
                processed=untag_rows, masters=untag_ms,
            ))
        elif drop or untag_rows or untag_ms:
            plans.append(BrandPlan(
                name, "trim",
                "Bỏ tên gọi khác chung chung và cửa hàng không mang tên chuỗi" if drop
                else "Bỏ cửa hàng không mang tên chuỗi",
                kept=len(ms) - len(untag_ms), samples=samples,
                drop_aliases=drop, processed=untag_rows, masters=untag_ms,
            ))
    plans.sort(key=lambda p: -(len(p.masters) + len(p.processed)))
    return plans


def _totals(plans: list[BrandPlan]) -> dict[str, int]:
    return {
        "brands": len(plans),
        "disable": sum(p.action == "disable" for p in plans),
        "untag_processed": sum(len(p.processed) for p in plans),
        "untag_masters": sum(len(p.masters) for p in plans),
        "drop_aliases": sum(len(p.drop_aliases) for p in plans),
    }


async def plan(session: AsyncSession) -> dict[str, Any]:
    """Dry run: the plan, nothing written."""
    plans = await build_plan(session)
    return {"totals": _totals(plans), "brands": [p.summary() for p in plans]}


async def _log(session: AsyncSession, run_id: str, brand: str, kind: str, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _CHUNK):
        part = rows[i: i + _CHUNK]
        await session.execute(
            text(
                """
                INSERT INTO brand_cleanup_log (run_id, brand, kind, ref, before)
                SELECT :run, :brand, :kind, x->>'ref', x->'before'
                FROM jsonb_array_elements(CAST(:rows AS jsonb)) AS x
                """
            ),
            {"run": run_id, "brand": brand, "kind": kind, "rows": json.dumps(part, ensure_ascii=False)},
        )


async def apply(session: AsyncSession) -> dict[str, Any]:
    """Carry out the plan, logging what each change replaced. Caller commits."""
    plans = await build_plan(session)
    run_id = uuid.uuid4().hex[:12]
    for p in plans:
        if p.action == "disable" or p.drop_aliases:
            before = (await session.execute(
                text("SELECT aliases, enabled FROM brands WHERE name = :b"), {"b": p.brand}
            )).one()
            await _log(session, run_id, p.brand, "brand", [
                {"ref": p.brand, "before": {"aliases": list(before.aliases or []), "enabled": before.enabled}}
            ])
        if p.action == "disable":
            await session.execute(text("UPDATE brands SET enabled = false WHERE name = :b"), {"b": p.brand})
        elif p.drop_aliases:
            await session.execute(
                text("UPDATE brands SET aliases = :a WHERE name = :b"),
                {"a": [a for a in (await _aliases(session, p.brand)) if a not in p.drop_aliases], "b": p.brand},
            )
        for i in range(0, len(p.processed), _CHUNK):
            ids = p.processed[i: i + _CHUNK]
            old = (await session.execute(
                text("SELECT id, brand_confidence FROM processed_pois WHERE id = ANY(:ids) AND brand = :b"),
                {"ids": ids, "b": p.brand},
            )).all()
            await _log(session, run_id, p.brand, "processed", [
                {"ref": str(r.id), "before": {"brand": p.brand, "brand_confidence": None if r.brand_confidence is None else float(r.brand_confidence)}} for r in old
            ])
            await session.execute(
                text("UPDATE processed_pois SET brand = NULL, brand_confidence = NULL WHERE id = ANY(:ids) AND brand = :b"),
                {"ids": ids, "b": p.brand},
            )
        for i in range(0, len(p.masters), _CHUNK):
            ids = p.masters[i: i + _CHUNK]
            await _log(session, run_id, p.brand, "master", [{"ref": m, "before": {"brand": p.brand}} for m in ids])
            await session.execute(
                text("UPDATE master_pois SET brand = NULL WHERE id = ANY(:ids) AND brand = :b"),
                {"ids": [uuid.UUID(m) for m in ids], "b": p.brand},
            )
    totals = _totals(plans)
    # The verdicts now describe the stores that are left.
    await brand_quality.run(session)
    logger.info("brand cleanup %s: %s", run_id, totals)
    return {"run_id": run_id, "totals": totals, "brands": [p.summary() for p in plans]}


async def _aliases(session: AsyncSession, brand: str) -> list[str]:
    row = (await session.execute(text("SELECT aliases FROM brands WHERE name = :b"), {"b": brand})).first()
    return list(row[0] or []) if row else []


async def undo(session: AsyncSession, run_id: str) -> dict[str, int]:
    """Put back what run ``run_id`` changed. A tag set since then by something
    else is left alone. Caller commits."""
    p = {"run": run_id}
    processed = (await session.execute(text(
        """
        WITH l AS MATERIALIZED (
            SELECT ref, before FROM brand_cleanup_log WHERE run_id = :run AND kind = 'processed'
        )
        UPDATE processed_pois t SET brand = l.before->>'brand',
               brand_confidence = (l.before->>'brand_confidence')::numeric
        FROM l
        WHERE t.id = l.ref::bigint AND t.brand IS NULL
        """
    ), p)).rowcount
    masters = (await session.execute(text(
        """
        WITH l AS MATERIALIZED (
            SELECT ref, before FROM brand_cleanup_log WHERE run_id = :run AND kind = 'master'
        )
        UPDATE master_pois t SET brand = l.before->>'brand'
        FROM l
        WHERE t.id = l.ref::uuid AND t.brand IS NULL
        """
    ), p)).rowcount
    brands = (await session.execute(text(
        """
        UPDATE brands t SET enabled = (l.before->>'enabled')::boolean,
               aliases = ARRAY(SELECT jsonb_array_elements_text(l.before->'aliases'))
        FROM brand_cleanup_log l
        WHERE l.run_id = :run AND l.kind = 'brand' AND t.name = l.ref
        """
    ), p)).rowcount
    await brand_quality.run(session)
    return {"processed": processed, "masters": masters, "brands": brands}


async def settings(session: AsyncSession) -> dict[str, Any]:
    return {**DEFAULTS, **(await app_settings.read(session, SETTINGS_KEY))}


async def daily(session: AsyncSession) -> dict[str, Any]:
    """The daily job: record verdicts, then clean up if an admin turned it on."""
    stats: dict[str, Any] = {"quality": await brand_quality.run(session)}
    if (await settings(session))["apply"]:
        stats["cleanup"] = (await apply(session))["totals"]
    return stats
