"""Retire active master_pois that no processed row points at any more.

Pipeline runs of 2026-04-27 – 05-03 moved members to another master without
retiring the old one, leaving 7,860 "ghost" masters active and served by the
API — each a copy of the master that now holds its rows. This script runs
``MergeService.retire_ghost_masters``: a ghost becomes ``merged_away`` into
that master (or ``archived`` if no active master holds its rows), with a
``master_poi_history`` row per change.

Read the numbers first:

    docker compose exec -T api python scripts/retire_ghost_masters.py --dry-run
    docker compose exec -T api python scripts/retire_ghost_masters.py
"""

from __future__ import annotations

import argparse
import asyncio

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
        if client.set(DEDUPE_LOCK_KEY, "ghost-retire", ex=_LOCK_TTL_S, nx=True):
            return client
        print("waiting for a dedupe pass to finish…")
        time.sleep(10)
    raise SystemExit("dedupe lock still held after 30 min — not retiring")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true", help="count the ghosts, change nothing")
    args = ap.parse_args()

    svc = MergeService(resolver=None)
    async with session_scope() as session:
        if args.dry_run:
            print(f"ghost masters: {await svc.retire_ghost_masters(session, dry_run=True)}")
            print("dry run — nothing changed")
            return
        # A dedupe pass attaching rows to a ghost's owner while we retire the
        # ghost is harmless, but hold its lock anyway, as the fold does.
        lock = _acquire_dedupe_lock()
        try:
            stats = await svc.retire_ghost_masters(
                session, batch_size=args.batch_size, max_seconds=_LOCK_TTL_S - 5 * 60
            )
        finally:
            lock.delete(DEDUPE_LOCK_KEY)
    print(f"retired: {stats}")


if __name__ == "__main__":
    asyncio.run(main())
