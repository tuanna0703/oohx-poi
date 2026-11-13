"""Load the 2025 wards' boundaries into admin_wards / admin_ward_parts.

    python scripts/load_admin_wards.py            # download the pinned export
    python scripts/load_admin_wards.py FILE.zip   # or read a copy of it

The archive (about 50 MB) must match ``SOURCE_SHA256``; the tables are
replaced in one transaction, so a failed load leaves the old ones. Then run
``scripts/backfill_ward_codes.py`` to stamp the POIs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import text

from poi_lake.db import session_scope
from poi_lake.observability import configure_logging
from poi_lake.services import admin_wards

logger = logging.getLogger(__name__)
USER_AGENT = "poi-lake/0.1 (bizmap.live; ward boundaries)"


def _download(dest: Path) -> None:
    with httpx.stream("GET", admin_wards.SOURCE_URL, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def _load(path: Path) -> None:
    digest = _sha256(path)
    if digest != admin_wards.SOURCE_SHA256:
        raise SystemExit(f"{path}: sha256 {digest}, expected {admin_wards.SOURCE_SHA256}")
    async with session_scope() as s:
        with path.open("rb") as f:
            n = await admin_wards.load(s, admin_wards.read_archive(f))
        parts = (await s.execute(text("SELECT COUNT(*) FROM admin_ward_parts"))).scalar()
        invalid = (await s.execute(text(
            "SELECT COUNT(*) FROM admin_wards WHERE ST_IsEmpty(geom) OR NOT ST_IsValid(geom)"
        ))).scalar()
        if invalid:
            raise SystemExit(f"{invalid} wards with an empty or invalid boundary; nothing loaded")
        await s.commit()
    print(f"wards loaded: {n}, pieces: {parts}")


def main() -> None:
    configure_logging()
    if len(sys.argv) > 1:
        asyncio.run(_load(Path(sys.argv[1])))
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wards.zip"
        _download(path)
        asyncio.run(_load(path))


if __name__ == "__main__":
    main()
