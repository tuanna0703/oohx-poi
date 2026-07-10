"""Fold duplicate master_pois back together.

A ``NEEDS_LLM`` pair decided without the resolver is not deferred — each row
becomes its own master, marked ``merged``, and the normal pass only ever reads
``pending`` rows. Nothing revisits them. This script runs
``MergeService.remerge_masters``, which re-clusters rows regardless of
merge_status, re-scores them (with the LLM), and folds each component into one
surviving master. The survivor keeps its id; losers become ``merged_away`` with
``merged_into`` pointing at it.

Run it bounded first and read the numbers before widening:

    docker compose exec -T api python scripts/remerge_masters.py --max-clusters 20 --dry-run
    docker compose exec -T api python scripts/remerge_masters.py --max-clusters 200

``--since`` limits the working set to rows merged after a timestamp, which is
what you want after an outage: only the masters created while the resolver was
unavailable are suspect.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from poi_lake.config import get_settings
from poi_lake.db import session_scope
from poi_lake.pipeline.dedupe import LLMResolver, MergeService


async def _target_ids(session, since: str | None, limit: int | None) -> list[int]:
    """processed_pois worth re-examining: already merged, optionally recent."""
    sql = "SELECT id FROM processed_pois WHERE merge_status = 'merged'"
    params: dict = {}
    if since:
        sql += " AND updated_at >= CAST(:since AS timestamptz)"
        params["since"] = since
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT :lim"
        params["lim"] = limit
    return list((await session.execute(text(sql), params)).scalars().all())


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-clusters", type=int, default=50)
    ap.add_argument("--max-seconds", type=float, default=600.0)
    ap.add_argument("--commit-every", type=int, default=10)
    ap.add_argument(
        "--since",
        default=None,
        help="only rows merged at/after this timestamp, e.g. 2026-07-10T00:00:00Z",
    )
    ap.add_argument("--row-limit", type=int, default=None, help="cap the working set")
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the resolver; NEEDS_LLM pairs stay separate (cheap smoke test)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report how many rows would be examined, then exit without merging",
    )
    args = ap.parse_args()

    settings = get_settings()
    async with session_scope() as session:
        ids = await _target_ids(session, args.since, args.row_limit)
        print(f"working set: {len(ids)} already-merged processed_pois")
        if args.dry_run:
            print("dry run — nothing merged")
            return
        if not ids:
            return

        resolver = (
            None
            if args.no_llm or not settings.anthropic_api_key
            else LLMResolver()
        )
        if resolver is None:
            print("resolver OFF — ambiguous pairs will stay as separate masters")

        stats = await MergeService(resolver=resolver).remerge_masters(
            session,
            ids=ids,
            max_clusters=args.max_clusters,
            commit_every=args.commit_every,
            max_seconds=args.max_seconds,
        )
    print(f"remerge: {stats}")
    if stats.get("clusters_available", 0) > stats.get("clusters", 0):
        print("backlog remains — run again to continue")


if __name__ == "__main__":
    asyncio.run(main())
