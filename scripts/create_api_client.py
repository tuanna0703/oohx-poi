"""Bootstrap an API client and print the plaintext key once.

Usage:
    docker compose exec api python scripts/create_api_client.py adtrue \\
        --permissions read:master --rate-limit 1000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from poi_lake.db import session_scope
from poi_lake.db.models import APIClient
from poi_lake.services.api_keys import generate_api_key


async def _create(name: str, permissions: list[str], rate_limit: int) -> int:
    async with session_scope() as session:
        existing = (
            await session.execute(select(APIClient).where(APIClient.name == name))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"[ERROR] api_client {name!r} already exists (id={existing.id}).")
            print("        Delete it first if you want to rotate the key:")
            print(f"        psql ... -c \"DELETE FROM api_clients WHERE id={existing.id}\"")
            return 1

        key = generate_api_key()
        client = APIClient(
            name=name,
            api_key_hash=key.hash,
            permissions=permissions,
            rate_limit_per_minute=rate_limit,
            enabled=True,
        )
        session.add(client)
        await session.commit()
        await session.refresh(client)

    print("=" * 64)
    print(f"  API client created: {name}  (id={client.id})")
    print(f"  permissions:         {permissions}")
    print(f"  rate limit:          {rate_limit}/min")
    print("=" * 64)
    print(f"  X-API-Key:  {key.plaintext}")
    print("=" * 64)
    print("  Save the key now — it is NOT recoverable later.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Create an API client + print key.")
    p.add_argument("name", help="human-readable client name (e.g. 'adtrue', 'tapon-ssp')")
    p.add_argument(
        "--permissions",
        default="read:master",
        help="comma-separated permissions (default: read:master)",
    )
    p.add_argument("--rate-limit", type=int, default=1000, help="requests per minute")
    args = p.parse_args()

    perms = [p.strip() for p in args.permissions.split(",") if p.strip()]
    return asyncio.run(_create(args.name, perms, args.rate_limit))


if __name__ == "__main__":
    sys.exit(main())
