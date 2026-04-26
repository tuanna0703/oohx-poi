"""End-to-end MergeService: synthetic duplicate Circle Ks → one master."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select, text

from poi_lake.db import get_sessionmaker
from poi_lake.db.models import (
    MasterPOI,
    MasterPOIHistory,
    ProcessedPOI,
    RawPOI,
    Source,
)
from poi_lake.pipeline.dedupe import MergeService

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _insert_processed(
    session,
    source_id: int,
    *,
    name: str,
    lat: float,
    lng: float,
    test_tag: str,
    embedding: list[float],
    address: str | None = None,
    phone: str | None = None,
    website_domain: str | None = None,
    brand: str | None = None,
    quality: float = 0.8,
) -> int:
    """Create one raw_poi + one processed_poi with the given fields."""
    suffix = uuid.uuid4().hex[:8]
    raw = RawPOI(
        source_id=source_id,
        source_poi_id=f"{test_tag}-{suffix}",
        raw_payload={"name": name, "_test_tag": test_tag},
        content_hash=hashlib.sha256((test_tag + suffix).encode()).hexdigest(),
        location=f"SRID=4326;POINT({lng} {lat})",
    )
    session.add(raw)
    await session.commit()
    await session.refresh(raw)

    proc = ProcessedPOI(
        raw_poi_id=raw.id,
        name_original=name,
        name_normalized=name.lower(),
        name_embedding=embedding,
        address_original=address,
        address_normalized=address,
        phone_e164=phone,
        website_domain=website_domain,
        brand=brand,
        quality_score=quality,
        location=f"SRID=4326;POINT({lng} {lat})",
        merge_status="pending",
    )
    session.add(proc)
    await session.commit()
    await session.refresh(proc)
    return proc.id


async def _cleanup(session, test_tag: str) -> None:
    # FK chain: master_poi_history → master_pois ← processed_pois ← raw_pois
    await session.execute(
        text(
            """
            DELETE FROM master_poi_history WHERE master_poi_id IN (
                SELECT merged_into FROM processed_pois WHERE raw_poi_id IN (
                    SELECT id FROM raw_pois WHERE source_poi_id LIKE :p
                )
            )
            """
        ),
        {"p": f"{test_tag}-%"},
    )
    # Need to detach masters from processed_pois before deleting masters
    await session.execute(
        text(
            """
            UPDATE processed_pois SET merged_into = NULL WHERE raw_poi_id IN (
                SELECT id FROM raw_pois WHERE source_poi_id LIKE :p
            )
            """
        ),
        {"p": f"{test_tag}-%"},
    )
    await session.execute(
        text(
            """
            DELETE FROM master_pois WHERE id IN (
                SELECT DISTINCT merged_into FROM processed_pois WHERE merged_into IS NOT NULL
                AND raw_poi_id IN (SELECT id FROM raw_pois WHERE source_poi_id LIKE :p)
            )
            """
        ),
        {"p": f"{test_tag}-%"},
    )
    await session.execute(
        text(
            "DELETE FROM processed_pois WHERE raw_poi_id IN "
            "(SELECT id FROM raw_pois WHERE source_poi_id LIKE :p)"
        ),
        {"p": f"{test_tag}-%"},
    )
    await session.execute(
        text("DELETE FROM raw_pois WHERE source_poi_id LIKE :p"),
        {"p": f"{test_tag}-%"},
    )
    await session.commit()


def _shared_emb(seed: float = 0.42, dim: int = 384) -> list[float]:
    raw = [(seed * (i + 1)) % 5 - 2 for i in range(dim)]
    norm = sum(v * v for v in raw) ** 0.5
    return [v / norm for v in raw]


async def test_three_circle_ks_merge_into_one_master() -> None:
    """Three Circle K rows within ~30m, identical brand+phone → one master."""
    sm = get_sessionmaker()
    tag = "merge1-" + uuid.uuid4().hex[:6]
    emb = _shared_emb(0.42)

    async with sm() as s:
        src = (
            await s.execute(select(Source).where(Source.code == "osm_overpass"))
        ).scalar_one()
        a = await _insert_processed(
            s, src.id, name="Circle K — Tràng Tiền", lat=21.0285, lng=105.8542,
            test_tag=tag, embedding=emb, address="1 Tràng Tiền, Hoàn Kiếm, Hà Nội",
            phone="+842432669489", website_domain="circlek.com.vn", brand="Circle K",
            quality=0.92,
        )
        b = await _insert_processed(
            s, src.id, name="Circle K Tràng Tiền", lat=21.02852, lng=105.85425,
            test_tag=tag, embedding=emb, address="1 Tràng Tiền, Hoàn Kiếm, Hà Nội",
            phone="+842432669489", website_domain="circlek.com.vn", brand="Circle K",
            quality=0.85,
        )
        c = await _insert_processed(
            s, src.id, name="Circle K - 1 Tràng Tiền", lat=21.02855, lng=105.85428,
            test_tag=tag, embedding=emb, address="1 Tràng Tiền, Hoàn Kiếm",
            phone="+842432669489", website_domain="circlek.com.vn",
            brand="Circle K", quality=0.70,
        )

    try:
        async with sm() as s:
            stats = await MergeService().dedupe_pending(s)
        # The pass may have processed unrelated pending rows too; we only
        # assert our 3 specifically merged together.
        assert stats["llm_calls"] == 0  # composite scores well above auto-merge threshold
        assert stats["masters_created"] >= 1
        assert stats["members_merged"] >= 3

        async with sm() as s:
            # Exactly one master_poi covers all three.
            rows = (
                await s.execute(
                    select(ProcessedPOI.id, ProcessedPOI.merged_into, ProcessedPOI.merge_status)
                    .where(ProcessedPOI.id.in_([a, b, c]))
                )
            ).all()
            merged_ids = {r[1] for r in rows}
            assert len(merged_ids) == 1
            master_id = next(iter(merged_ids))
            assert all(r[2] == "merged" for r in rows)

            master = await s.get(MasterPOI, master_id)
            assert master is not None
            assert master.canonical_name == "Circle K — Tràng Tiền"  # picked from highest-quality member
            assert master.brand == "Circle K"
            assert master.canonical_phone == "+842432669489"
            assert master.sources_count == 3
            assert sorted(master.merged_processed_ids) == sorted([a, b, c])

            # Audit log written.
            history = (
                await s.execute(
                    select(MasterPOIHistory).where(MasterPOIHistory.master_poi_id == master_id)
                )
            ).scalars().all()
            assert len(history) == 1
            assert history[0].change_reason == "initial_merge"
    finally:
        async with sm() as s:
            await _cleanup(s, tag)


async def test_distinct_locations_each_get_their_own_master() -> None:
    """Two unrelated cafés 1km apart → two masters, no merge."""
    sm = get_sessionmaker()
    tag = "merge2-" + uuid.uuid4().hex[:6]

    async with sm() as s:
        src = (
            await s.execute(select(Source).where(Source.code == "osm_overpass"))
        ).scalar_one()
        a = await _insert_processed(
            s, src.id, name="Highlands Coffee", lat=21.0285, lng=105.8542,
            test_tag=tag, embedding=_shared_emb(0.1), brand="Highlands Coffee", quality=0.85,
        )
        b = await _insert_processed(
            s, src.id, name="Phúc Long", lat=21.0395, lng=105.8542,  # 1.2km north
            test_tag=tag, embedding=_shared_emb(2.7), brand="Phúc Long", quality=0.85,
        )

    try:
        async with sm() as s:
            stats = await MergeService().dedupe_pending(s)
        assert stats["masters_created"] >= 2

        async with sm() as s:
            rows = (
                await s.execute(
                    select(ProcessedPOI.id, ProcessedPOI.merged_into)
                    .where(ProcessedPOI.id.in_([a, b]))
                )
            ).all()
            ids = {r[1] for r in rows}
            assert len(ids) == 2  # two distinct masters
    finally:
        async with sm() as s:
            await _cleanup(s, tag)
