"""Seed the ``sources`` table.

Usage:
    python -m scripts.seed_sources
    # or inside the container:
    docker compose exec api python -m scripts.seed_sources
"""

from __future__ import annotations

import asyncio
import logging
import sys

from poi_lake.db import session_scope
from poi_lake.seeds.runner import seed_sources


async def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    async with session_scope() as session:
        n = await seed_sources(session)
    print(f"[ok] seeded {n} sources")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
