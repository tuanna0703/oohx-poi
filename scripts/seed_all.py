"""Seed sources + openooh_categories + brands in one shot.

Idempotent — re-run freely after editing any seed file.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from poi_lake.db import session_scope
from poi_lake.seeds.runner import seed_all


async def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    async with session_scope() as session:
        counts = await seed_all(session)
    for table, n in counts.items():
        print(f"[ok] {table}: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
