"""Stores from the chains' own store lists, already read into ``chain_stores``.

No web call: the catalog workers fetch the lists; this adapter only turns a
stored row into a raw record, so a store the lake lacks enters through the
usual raw → normalize → dedupe path. ``source_poi_id`` is ``"<chain>:<store_code>"``.
Run as a ``detail_enrich`` job (see ``POST /admin/catalog/{chain}/add-missing``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text

from poi_lake.adapters.base import AdapterError, RawPOIRecord, SourceAdapter
from poi_lake.catalog.chains import CHAINS, display_name
from poi_lake.db import session_scope


class ChainWebsitesAdapter(SourceAdapter):
    code = "chain_websites"
    name = "Chains' own store lists"

    async def fetch_by_area(
        self,
        lat: float,
        lng: float,
        radius_m: int,
        category: str | None = None,
        place: str | None = None,
    ) -> AsyncIterator[RawPOIRecord]:
        raise AdapterError("chain_websites only runs detail_enrich jobs")
        yield  # pragma: no cover - makes this an async generator

    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        chain_code, _, store_code = source_poi_id.partition(":")
        chain = CHAINS.get(chain_code)
        if chain is None or not store_code:
            raise AdapterError(f"bad chain store id {source_poi_id!r}")
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        """
                        SELECT name, store_type, address, phone, open_hours,
                               ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
                        FROM chain_stores WHERE chain = :c AND store_code = :s AND active
                        """
                    ),
                    {"c": chain_code, "s": store_code},
                )
            ).one_or_none()
        if row is None:
            return None
        payload = {
            "chain": chain_code,
            "store_code": store_code,
            "name": display_name(chain_code, row.name, row.address, row.store_type),
            "chain_store_name": row.name,
            "address": row.address,
            "phone": row.phone,
            "open_hours": row.open_hours,
            "category": "|".join(chain.category),
            "lat": row.lat,
            "lng": row.lng,
        }
        return RawPOIRecord(
            source_poi_id=source_poi_id, raw_payload=payload, location=(row.lat, row.lng)
        )

    async def health_check(self) -> bool:
        return True
