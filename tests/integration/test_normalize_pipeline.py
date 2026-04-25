"""End-to-end normalize: insert raw_poi + run pipeline + verify processed_poi."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text

from poi_lake.db import get_sessionmaker
from poi_lake.db.models import ProcessedPOI, RawPOI
from poi_lake.pipeline.embed import EmbeddingService, set_embedding_service
from poi_lake.pipeline.orchestrator import NormalizePipeline

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeEmbeddings(EmbeddingService):
    """Deterministic 384-dim embedding without loading the real model."""

    def __init__(self) -> None:
        # Skip the parent __init__ — we don't need a real model name/cache.
        self._model = "stub"  # type: ignore[assignment]

    @property
    def dim(self) -> int:
        return 384

    def encode(self, text: str) -> list[float]:
        # Cheap, content-aware stub: spread the hash across the vector so
        # different names produce different vectors.
        h = hash(text) & 0xFFFFFFFF
        return [(h >> (i % 24)) & 1 for i in range(384)]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


@pytest.fixture(autouse=True)
def _stub_embeddings():
    set_embedding_service(_FakeEmbeddings())
    yield
    set_embedding_service(None)


async def _insert_osm_raw(session, source_id: int, payload: dict) -> int:
    """Insert a unique-per-call raw_poi to avoid colliding with leftover
    rows from any previous interrupted test run."""
    import hashlib
    import uuid

    payload = {**payload, "_test_id": uuid.uuid4().hex}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    row = RawPOI(
        source_id=source_id,
        source_poi_id=f"{payload.get('type', 'node')}-{payload.get('id', 0)}-{payload['_test_id'][:8]}",
        raw_payload=payload,
        content_hash=digest,
        location=f"SRID=4326;POINT({payload.get('lon', 0)} {payload.get('lat', 0)})"
        if "lat" in payload else None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.id


async def test_full_normalize_osm_record() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        # Use the seeded osm_overpass source (id=2 in fresh DB but lookup is safe).
        from poi_lake.db.models import Source
        src = (
            await session.execute(select(Source).where(Source.code == "osm_overpass"))
        ).scalar_one()

        payload = {
            "type": "node",
            "id": 9001,
            "lat": 21.0285,
            "lon": 105.8542,
            "tags": {
                "amenity": "cafe",
                "name": "Highlands Coffee — Tràng Tiền",
                "addr:housenumber": "1",
                "addr:street": "Tràng Tiền",
                "addr:district": "Hoàn Kiếm",
                "addr:city": "Hà Nội",
                "phone": "+84 24 3826 9999",
                "website": "https://highlandscoffee.com.vn",
            },
        }
        raw_id = await _insert_osm_raw(session, src.id, payload)

        pipeline = NormalizePipeline()
        await pipeline.warm_up(session)
        proc_id = await pipeline.process(session, raw_id)

    assert proc_id is not None

    async with sm() as session:
        proc = await session.get(ProcessedPOI, proc_id)
        assert proc is not None
        assert proc.name_original == "Highlands Coffee — Tràng Tiền"
        assert proc.openooh_category == "hospitality"
        assert proc.openooh_subcategory == "hospitality.cafes"
        assert proc.brand == "Highlands Coffee"
        assert proc.brand_confidence is not None
        assert proc.phone_e164 == "+842438269999"
        assert proc.website_domain == "highlandscoffee.com.vn"
        assert proc.address_components is not None
        assert proc.quality_score is not None
        assert float(proc.quality_score) > 0.5
        assert proc.merge_status == "pending"
        # Embedding present and 384-dim.
        assert proc.name_embedding is not None
        assert len(list(proc.name_embedding)) == 384

        # raw_pois.processed_at was set
        raw = await session.get(RawPOI, raw_id)
        assert raw.processed_at is not None

        # cleanup
        await session.execute(text("DELETE FROM processed_pois WHERE id = :i"), {"i": proc_id})
        await session.execute(text("DELETE FROM raw_pois WHERE id = :i"), {"i": raw_id})
        await session.commit()


async def test_idempotent_normalize() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        from poi_lake.db.models import Source
        src = (
            await session.execute(select(Source).where(Source.code == "osm_overpass"))
        ).scalar_one()

        payload = {
            "type": "node",
            "id": 9002,
            "lat": 21.03,
            "lon": 105.85,
            "tags": {"amenity": "restaurant", "name": "Quán Ăn Ngon"},
        }
        raw_id = await _insert_osm_raw(session, src.id, payload)

        pipeline = NormalizePipeline()
        await pipeline.warm_up(session)
        proc1 = await pipeline.process(session, raw_id)
        proc2 = await pipeline.process(session, raw_id)

    assert proc1 is not None
    assert proc1 == proc2  # second call returns the existing processed_poi.id

    async with sm() as session:
        # Only one processed_poi row exists for this raw_poi.
        rows = (
            await session.execute(
                select(ProcessedPOI).where(ProcessedPOI.raw_poi_id == raw_id)
            )
        ).scalars().all()
        assert len(rows) == 1

        await session.execute(text("DELETE FROM processed_pois WHERE raw_poi_id = :r"), {"r": raw_id})
        await session.execute(text("DELETE FROM raw_pois WHERE id = :i"), {"i": raw_id})
        await session.commit()


async def test_record_without_coords_is_skipped() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        from poi_lake.db.models import Source
        src = (
            await session.execute(select(Source).where(Source.code == "osm_overpass"))
        ).scalar_one()

        # An OSM way without center → no usable coordinates.
        payload = {
            "type": "way",
            "id": 9003,
            "tags": {"amenity": "cafe", "name": "No Coords Cafe"},
        }
        raw_id = await _insert_osm_raw(session, src.id, payload)

        pipeline = NormalizePipeline()
        await pipeline.warm_up(session)
        result = await pipeline.process(session, raw_id)

    assert result is None
    async with sm() as session:
        rows = (
            await session.execute(
                select(ProcessedPOI).where(ProcessedPOI.raw_poi_id == raw_id)
            )
        ).scalars().all()
        assert rows == []
        # raw_pois.processed_at is set so we don't retry forever.
        raw = await session.get(RawPOI, raw_id)
        assert raw.processed_at is not None

        await session.execute(text("DELETE FROM raw_pois WHERE id = :i"), {"i": raw_id})
        await session.commit()
