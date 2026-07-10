"""Undo merges that pulled two different places into one master.

Before `dedupe_auto_merge_max_meters` existed, `decide()` could return
AUTO_MERGE for a pair the resolver was never shown. For a chain brand every
non-geometric field agrees across branches — name, national hotline, website,
brand — so two Sacombank ATMs 217 m apart scored 0.9676 and were merged. DBSCAN
chains, so the cluster spanned both.

The damage is repairable because a loser keeps its `source_refs` when it is
marked `merged_away`. `MergeService.split_master` re-clusters a master's members
and gives each spatial group back one of the master ids that used to own it. The
survivor keeps its id; the Data Engine stores it as `source.pois.external_id`.

Find the suspects, look at them, then fix them:

    docker compose exec -T api python scripts/split_overmerged_masters.py --list
    docker compose exec -T api python scripts/split_overmerged_masters.py --all --dry-run
    docker compose exec -T api python scripts/split_overmerged_masters.py --all

`--dry-run` rolls the transaction back, so it reports exactly what a real run
would do without writing a row.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import text

from poi_lake.db import session_scope
from poi_lake.pipeline.dedupe import MergeService

# A master whose members span more than this is worth re-clustering. It matches
# the AUTO_MERGE gate: anything the gate would now refuse to merge in one go.
_DEFAULT_SPAN_METERS = 50.0

_SUSPECTS = text(
    """
    SELECT m.id,
           m.canonical_name,
           count(p.id) AS members,
           MAX(ST_Distance(p.location, m.location)) AS span_m
    FROM master_pois m
    JOIN processed_pois p ON p.merged_into = m.id
    WHERE m.status = 'active'
    GROUP BY m.id, m.canonical_name
    HAVING MAX(ST_Distance(p.location, m.location)) > :span
    ORDER BY span_m DESC
    """
)


async def _suspects(session, span: float) -> list[tuple[uuid.UUID, str, int, float]]:
    return list((await session.execute(_SUSPECTS, {"span": span})).all())


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--master", action="append", default=[], help="master uuid (repeatable)")
    ap.add_argument("--all", action="store_true", help="every master wider than --span")
    ap.add_argument("--list", action="store_true", help="show suspects and exit")
    ap.add_argument("--span", type=float, default=_DEFAULT_SPAN_METERS)
    ap.add_argument("--eps", type=float, default=None, help="re-cluster eps (default: settings)")
    ap.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    args = ap.parse_args()

    if not (args.master or args.all or args.list):
        ap.error("pass --list, --all, or at least one --master")

    async with session_scope() as session:
        if args.list or args.all:
            found = await _suspects(session, args.span)
            print(f"{len(found)} master(s) span more than {args.span:.0f} m\n")
            for mid, name, members, span in found:
                print(f"  {mid}  {span:7.1f} m  {members:3d} members  {name!r}")
            if args.list:
                return
            targets = [mid for mid, _, _, _ in found]
        else:
            targets = [uuid.UUID(m) for m in args.master]

        if not targets:
            print("nothing to do")
            return

        svc = MergeService()  # no resolver: splitting re-clusters, it never asks
        total_before = total_after = 0
        for mid in targets:
            # A savepoint per master. `split_master` can raise after it has
            # already grown one group of a multi-group master; without this the
            # half-finished split rides along to the final commit.
            try:
                async with session.begin_nested():
                    result = await svc.split_master(session, mid, eps_meters=args.eps)
            except ValueError as exc:
                print(f"  SKIP {mid}: {exc}")
                continue
            if not result["changed"]:
                print(f"  {mid}: one group, nothing to split")
                continue
            total_before += 1
            total_after += len(result["masters"])
            print(f"  {mid}: -> {result['groups']} masters: {', '.join(result['masters'])}")

        print(f"\n{total_before} master(s) split into {total_after}")
        if args.dry_run:
            await session.rollback()
            print("dry-run: rolled back, nothing written")
        else:
            await session.commit()
            print("committed")


if __name__ == "__main__":
    asyncio.run(main())
