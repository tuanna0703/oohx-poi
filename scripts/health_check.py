"""Standalone health check — useful in CI and for manual ops.

Exit code 0 on healthy, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text

from poi_lake.config import get_settings
from poi_lake.db import get_engine


REQUIRED_EXTENSIONS = {"postgis", "vector", "pgcrypto"}
REQUIRED_TABLES = {
    "sources",
    "ingestion_jobs",
    "raw_pois",
    "master_pois",
    "master_poi_history",
    "processed_pois",
    "brands",
    "openooh_categories",
    "api_clients",
}


async def _check() -> bool:
    settings = get_settings()
    print(f"env={settings.app_env}  db={settings.database_url.split('@')[-1]}")
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        print("[ok] database reachable")

        ext_rows = await conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = ANY(:names)"),
            {"names": list(REQUIRED_EXTENSIONS)},
        )
        installed = {r[0] for r in ext_rows}
        missing_ext = REQUIRED_EXTENSIONS - installed
        if missing_ext:
            print(f"[FAIL] missing extensions: {sorted(missing_ext)}")
            return False
        print(f"[ok] extensions: {sorted(installed)}")

        tbl_rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(:names)"
            ),
            {"names": list(REQUIRED_TABLES)},
        )
        present = {r[0] for r in tbl_rows}
        missing_tbl = REQUIRED_TABLES - present
        if missing_tbl:
            print(f"[FAIL] missing tables: {sorted(missing_tbl)}")
            return False
        print(f"[ok] tables: {sorted(present)}")

        src_count = (await conn.execute(text("SELECT COUNT(*) FROM sources"))).scalar_one()
        print(f"[info] sources rows: {src_count}")

    await engine.dispose()
    return True


async def _main() -> int:
    logging.basicConfig(level=logging.WARNING)
    try:
        ok = await _check()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
