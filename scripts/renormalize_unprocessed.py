"""Re-enqueue normalize for any raw_pois that are currently unprocessed.

Used after schema/rule changes (e.g. category-inference rule reorder)
to roll new logic over existing raw rows without re-fetching them from
the source.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from poi_lake.db import session_scope
from poi_lake.db.models import RawPOI
from poi_lake.workers.normalize import run_normalize_raw_poi


async def main() -> None:
    async with session_scope() as s:
        ids = (
            await s.execute(
                select(RawPOI.id).where(RawPOI.processed_at.is_(None))
            )
        ).scalars().all()
    print(f"enqueueing {len(ids)} unprocessed raw_pois")
    for i in ids:
        run_normalize_raw_poi.send(i)


if __name__ == "__main__":
    asyncio.run(main())
