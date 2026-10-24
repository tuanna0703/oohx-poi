"""Fold master_pois that hold copies of the same source record.

Before 2026-10 a re-crawled place came back as a fresh pending row, and the
pending-only clusterer minted a second master for it: on production 200k
distinct Google place_ids sat in 250k masters, 34k of them split. The normal
pass now attaches such rows to their master; this script repairs the backlog
via ``MergeService.fold_same_place_masters``. No scoring and no LLM — a shared
place_id is the identity. The survivor keeps its id; losers become
``merged_away`` with ``merged_into`` pointing at it.

Groups chain through masters that already hold several places (one mall gave a
25-master, 18-place group), so by default only groups covering exactly one
place are folded — plus groups where one master already holds every place
the group covers (the rest are copies of it: one store listed twice on
Google, then re-crawled).

Read the numbers first, then run bounded before widening:

    docker compose exec -T api python scripts/fold_same_place_masters.py --dry-run
    docker compose exec -T api python scripts/fold_same_place_masters.py --max-groups 200
    docker compose exec -T api python scripts/fold_same_place_masters.py
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import text

from poi_lake.config import get_settings
from poi_lake.db import session_scope
from poi_lake.pipeline.dedupe import MergeService
from poi_lake.workers.dedupe import DEDUPE_LOCK_KEY

_LOCK_TTL_S = 25 * 60  # same TTL run_dedupe uses


def _acquire_dedupe_lock():  # type: ignore[no-untyped-def]
    """Wait (≤30 min) for any running dedupe pass, then take its lock."""
    import time

    import redis

    client = redis.from_url(get_settings().redis_url)
    for _ in range(180):
        if client.set(DEDUPE_LOCK_KEY, "fold", ex=_LOCK_TTL_S, nx=True):
            return client
        print("waiting for a dedupe pass to finish…")
        time.sleep(10)
    raise SystemExit("dedupe lock still held after 30 min — not folding")


async def _places_per_group(session, groups) -> list[tuple[int, bool]]:  # type: ignore[no-untyped-def]
    """(distinct places, one master holds them all) per group."""
    rows = (
        await session.execute(
            text(
                "SELECT p.merged_into, r.source_id, r.source_poi_id "
                "FROM processed_pois p JOIN raw_pois r ON r.id = p.raw_poi_id "
                "WHERE p.merged_into = ANY(CAST(:ids AS UUID[]))"
            ),
            {"ids": [str(m) for g in groups for m in g]},
        )
    ).all()
    places_of: dict = {}
    for mid, sid, spid in rows:
        places_of.setdefault(mid, set()).add((sid, spid))
    out = []
    for g in groups:
        sets = [places_of.get(m, set()) for m in g]
        union = set().union(*sets)
        out.append((len(union), bool(union) and any(x == union for x in sets)))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-groups", type=int, default=None)
    ap.add_argument("--max-group-size", type=int, default=10)
    ap.add_argument(
        "--max-places", type=int, default=1,
        help="skip groups whose masters span more distinct places (0 = no limit)",
    )
    ap.add_argument("--commit-every", type=int, default=100)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report the groups that would be folded, then exit without merging",
    )
    args = ap.parse_args()
    max_places = args.max_places or None

    svc = MergeService(resolver=None)
    async with session_scope() as session:
        if args.dry_run:
            groups = await svc.same_place_master_groups(session)
            places = await _places_per_group(session, groups)
            fold = [
                g for g, (n, same) in zip(groups, places, strict=True)
                if len(g) <= args.max_group_size
                and (max_places is None or n <= max_places or same)
            ]
            held = sum(
                1 for g, (n, same) in zip(groups, places, strict=True)
                if len(g) <= args.max_group_size and max_places is not None
                and n > max_places and same
            )
            print(f"groups: {len(groups)}")
            print("distinct places per group (6 = 6+):",
                  dict(sorted(Counter(min(n, 6) for n, _ in places).items())))
            print(f"multi-place groups one master already holds (folded): {held}")
            print("group size → count:", dict(sorted(Counter(len(g) for g in groups).items())))
            print(f"would fold {len(fold)} groups, merging away "
                  f"{sum(len(g) - 1 for g in fold)} masters")
            print(f"would skip {len(groups) - len(fold)} groups "
                  f"(> {args.max_group_size} masters or > {max_places} places)")
            print("dry run — nothing merged")
            return

        # Hold the dedupe pass's lock: a pass attaching a pending row to a
        # master this run is about to retire would strand the row on a
        # merged_away master. Stay well inside the lock's TTL.
        lock = _acquire_dedupe_lock()
        try:
            stats = await svc.fold_same_place_masters(
                session,
                max_groups=args.max_groups,
                max_group_size=args.max_group_size,
                max_places=max_places,
                commit_every=args.commit_every,
                max_seconds=_LOCK_TTL_S - 5 * 60,
            )
        finally:
            lock.delete(DEDUPE_LOCK_KEY)
    print(f"fold: {stats}")
    remaining = (
        stats["groups_available"] - stats["groups"]
        - stats["skipped_large"] - stats["skipped_multi_place"]
    )
    if remaining > 0:
        print(f"{remaining} groups remain — run again to continue")


if __name__ == "__main__":
    asyncio.run(main())
