"""One-off diagnostic: submit a gosom job and watch the list endpoint.

Run inside the api container:
    docker compose exec api python scripts/probe_gosom.py
"""

from __future__ import annotations

import asyncio

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://gosom-scraper:8080", timeout=30) as c:
        body = {
            "name": "probe-direct",
            "keywords": ["circle k"],
            "lang": "vi",
            "lat": "21.0285",
            "lon": "105.8542",
            "zoom": 16,
            "radius": 400,
            "depth": 1,
            "fast_mode": True,
            "email": False,
            "max_time": 180,
        }
        r = await c.post("/api/v1/jobs", json=body)
        print(f"submit: HTTP {r.status_code}  body={r.text[:200]}")
        if r.status_code != 201:
            return
        jid = r.json()["id"]
        print(f"job id: {jid}")

        # Dump the actual shape of one list item once.
        await asyncio.sleep(3)
        lr = await c.get("/api/v1/jobs")
        ls = lr.json() or []
        if ls:
            print("FIRST ITEM KEYS:", sorted(ls[0].keys()))
            print("FIRST ITEM:", ls[0])


if __name__ == "__main__":
    asyncio.run(main())
