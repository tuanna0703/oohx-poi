"""Recompute the OpenOOH category of every processed POI and active master.

Until 2026-10-25 only 20% of active masters had a category: gosom's fast
mode returns none, so the name was the only clue, and the name rules knew
no tạp hóa, gara, karaoke, "quầy thuốc" or "ATM". This applies the current
``CategoryMapper.classify``:

  * a processed row: its source category, else its name, else what the
    ingestion job searched for (``from_search`` — trusted codes only);
  * a master: its canonical name, else the names of its rows (the most
    common answer), else their searches (the most common answer).

Read the numbers and samples first; the run keeps the old values in
``category_backup_20261025`` (master and processed ids with old top/sub):

    docker compose exec -T api python scripts/reclassify_categories.py --dry-run
    docker compose exec -T api python scripts/reclassify_categories.py

``updated_at`` is left alone: this is derived metadata, and bizmap pages
master lists by it.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from collections import Counter, defaultdict

from sqlalchemy import text

from poi_lake.db import session_scope
from poi_lake.pipeline.normalize import CategoryMapper
from poi_lake.seeds.vn_retail_taxonomy import VN_CODES

Cat = tuple[str | None, str | None]
BATCH = 2000
BACKUP = "category_backup_20261025"


def _most_common(results: list[Cat]) -> Cat:
    found = [r for r in results if r != (None, None)]
    if not found:
        return (None, None)
    return Counter(found).most_common(1)[0][0]


async def _load(session):  # type: ignore[no-untyped-def]
    t = time.monotonic()
    rows = (
        await session.execute(
            text(
                """
                SELECT p.id, p.merged_into, p.name_original, p.raw_category,
                       p.openooh_category, p.openooh_subcategory,
                       s.code AS source, j.params->>'category' AS searched
                FROM processed_pois p
                JOIN raw_pois r ON r.id = p.raw_poi_id
                JOIN sources s ON s.id = r.source_id
                LEFT JOIN ingestion_jobs j ON j.id = r.ingestion_job_id
                """
            )
        )
    ).all()
    masters = (
        await session.execute(
            text(
                """
                SELECT id, canonical_name, openooh_category, openooh_subcategory
                FROM master_pois WHERE status = 'active'
                """
            )
        )
    ).all()
    print(f"loaded {len(rows):,} processed rows, {len(masters):,} active masters in {time.monotonic() - t:.0f} s")
    return rows, masters


def _plan(rows, masters):  # type: ignore[no-untyped-def]
    m = CategoryMapper()
    proc_new: dict[int, Cat] = {}
    by_master: dict[object, list[tuple[Cat, Cat]]] = defaultdict(list)  # (by name, by search)
    for r in rows:
        by_name = m.map_with_fallback(r.source, r.raw_category, r.name_original)
        by_search = m.from_search(r.searched)
        new = by_name if by_name != (None, None) else by_search
        if new != (r.openooh_category, r.openooh_subcategory):
            proc_new[r.id] = new
        if r.merged_into is not None:
            by_master[r.merged_into].append((by_name, by_search))

    master_new: dict[object, Cat] = {}
    how: Counter[str] = Counter()
    for mp in masters:
        new = m.infer_from_name(mp.canonical_name)
        source = "canonical name"
        members = by_master.get(mp.id, [])
        if new == (None, None):
            new, source = _most_common([n for n, _ in members]), "row names"
        if new == (None, None):
            new, source = _most_common([s for _, s in members]), "search"
        how[source if new != (None, None) else "none"] += 1
        if new != (mp.openooh_category, mp.openooh_subcategory):
            master_new[mp.id] = new
    return proc_new, master_new, how


def _report(masters, master_new, how) -> None:  # type: ignore[no-untyped-def]
    before = sum(1 for mp in masters if mp.openooh_category)
    after_cat = {mp.id: (mp.openooh_category, mp.openooh_subcategory) for mp in masters}
    after_cat.update(master_new)
    after = sum(1 for c in after_cat.values() if c[0])
    n = len(masters)
    print(f"\nmasters with a category: {before:,} ({before / n:.1%}) → {after:,} ({after / n:.1%})")
    print("decided by: " + ", ".join(f"{k} {v:,}" for k, v in how.most_common()))

    changed = Counter(
        (old[1] or old[0] or "—", new[1] or new[0] or "—")
        for mp in masters
        if mp.id in master_new
        for old, new in [((mp.openooh_category, mp.openooh_subcategory), master_new[mp.id])]
        if old[0]
    )
    print(f"\nre-labelled (had a category before): {sum(changed.values()):,}")
    for (old, new), k in changed.most_common(25):
        print(f"  {k:>7,}  {old} → {new}")

    subs = Counter(c[1] or c[0] for c in after_cat.values() if c[0])
    print("\nafter, by code:")
    for code, k in subs.most_common():
        mark = "  (mới)" if code in VN_CODES else ""
        print(f"  {k:>7,}  {code}{mark}")

    names = {mp.id: mp.canonical_name for mp in masters}
    rnd = random.Random(7)
    print("\nsamples of new codes:")
    by_code: dict[str, list[str]] = defaultdict(list)
    for mid, (_, sub) in master_new.items():
        if sub in VN_CODES:
            by_code[sub].append(names[mid])
    for code in sorted(by_code):
        pick = rnd.sample(by_code[code], min(8, len(by_code[code])))
        print(f"  {code}: " + " | ".join(pick))


async def _apply(session, proc_new, master_new) -> None:  # type: ignore[no-untyped-def]
    await session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {BACKUP} (
                kind text NOT NULL, id text NOT NULL,
                openooh_category varchar(50), openooh_subcategory varchar(100),
                PRIMARY KEY (kind, id))
            """
        )
    )
    for kind, table, changes in (
        ("processed", "processed_pois", proc_new),
        ("master", "master_pois", master_new),
    ):
        items = list(changes.items())
        id_type = "bigint" if kind == "processed" else "uuid"
        for i in range(0, len(items), BATCH):
            chunk = items[i : i + BATCH]
            params: dict[str, object] = {}
            values = []
            for j, (pid, (top, sub)) in enumerate(chunk):
                params[f"i{j}"], params[f"t{j}"], params[f"s{j}"] = str(pid), top, sub
                values.append(f"(CAST(:i{j} AS {id_type}), CAST(:t{j} AS varchar), CAST(:s{j} AS varchar))")
            vals = ", ".join(values)
            await session.execute(
                text(
                    f"""
                    INSERT INTO {BACKUP} (kind, id, openooh_category, openooh_subcategory)
                    SELECT '{kind}', t.id::text, t.openooh_category, t.openooh_subcategory
                    FROM {table} t JOIN (VALUES {vals}) AS v(id, top, sub) ON v.id = t.id
                    ON CONFLICT (kind, id) DO NOTHING
                    """
                ),
                params,
            )
            await session.execute(
                text(
                    f"""
                    UPDATE {table} t SET openooh_category = v.top, openooh_subcategory = v.sub
                    FROM (VALUES {vals}) AS v(id, top, sub) WHERE v.id = t.id
                    """
                ),
                params,
            )
            await session.commit()
        print(f"updated {len(items):,} {table}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report and sample, change nothing")
    args = ap.parse_args()

    async with session_scope() as session:
        rows, masters = await _load(session)
        proc_new, master_new, how = _plan(rows, masters)
        print(f"changes: {len(proc_new):,} processed rows, {len(master_new):,} masters")
        _report(masters, master_new, how)
        if args.dry_run:
            print("\ndry run — nothing changed")
            return
        await _apply(session, proc_new, master_new)


if __name__ == "__main__":
    asyncio.run(main())
