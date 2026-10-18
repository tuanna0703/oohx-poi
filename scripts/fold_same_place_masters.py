"""Fold master_pois that hold copies of the same source record.

Before 2026-10 a re-crawled place came back as a fresh pending row, and the
pending-only clusterer minted a second master for it: on production 200k
distinct Google place_ids sat in 250k masters, 34k of them split. The normal
pass now attaches such rows to their master; this script repairs the backlog
via ``MergeService.fold_same_place_masters``. No scoring and no LLM — a shared
place_id is the identity. The survivor keeps its id; losers become
``merged_away`` with ``merged_into`` pointing at it.

Read the numbers first, then run bounded before widening:

    docker compose exec -T api python scripts/fold_same_place_masters.py --dry-run
    docker compose exec -T api python scripts/fold_same_place_masters.py --max-groups 200
    docker compose exec -T api python scripts/fold_same_place_masters.py
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from poi_lake.db import session_scope
from poi_lake.pipeline.dedupe import MergeService


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-groups", type=int, default=None)
    ap.add_argument("--max-group-size", type=int, default=10)
    ap.add_argument("--commit-every", type=int, default=100)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report the groups that would be folded, then exit without merging",
    )
    args = ap.parse_args()

    svc = MergeService(resolver=None)
    async with session_scope() as session:
        if args.dry_run:
            groups = await svc.same_place_master_groups(session)
            sizes = Counter(len(g) for g in groups)
            print(f"groups: {len(groups)}")
            print(f"masters that would be merged away: {sum(len(g) - 1 for g in groups)}")
            print("group size → count:", dict(sorted(sizes.items())))
            large = [g for g in groups if len(g) > args.max_group_size]
            print(f"groups over --max-group-size={args.max_group_size} (skipped): {len(large)}")
            for g in large[:5]:
                print("  e.g.", [str(m) for m in g[:6]], "…" if len(g) > 6 else "")
            print("dry run — nothing merged")
            return

        stats = await svc.fold_same_place_masters(
            session,
            max_groups=args.max_groups,
            max_group_size=args.max_group_size,
            commit_every=args.commit_every,
        )
    print(f"fold: {stats}")
    remaining = stats["groups_available"] - stats["groups"] - stats["skipped_large"]
    if remaining > 0:
        print(f"{remaining} groups remain — run again to continue")


if __name__ == "__main__":
    asyncio.run(main())
