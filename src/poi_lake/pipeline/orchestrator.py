"""NormalizePipeline — raw_pois row → processed_pois row.

Steps per record:
    extract canonical fields
    normalize address / phone / category / brand
    embed name (kept Vietnamese-diacritized for the multilingual MiniLM)
    score quality
    INSERT processed_pois  (idempotent: skips if raw_poi_id already processed)
    UPDATE raw_pois.processed_at
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.db.models import ProcessedPOI, RawPOI, Source
from poi_lake.observability import NORMALIZE_PROCESSED_INSERTED
from poi_lake.observability.metrics import NORMALIZE_SKIPPED
from poi_lake.pipeline.embed import EmbeddingService, get_embedding_service
from poi_lake.services.admin_geo import lookup_admin
from poi_lake.pipeline.extractors import get_extractor
from poi_lake.pipeline.normalize import (
    AddressNormalizer,
    BrandDetector,
    CategoryMapper,
    PhoneNormalizer,
)
from poi_lake.pipeline.quality import QualityScorer

logger = logging.getLogger(__name__)


class NormalizePipeline:
    """One instance per worker process; pre-loads brand cache + model."""

    def __init__(
        self,
        *,
        embedding_service: EmbeddingService | None = None,
        brand_detector: BrandDetector | None = None,
    ) -> None:
        self.address_norm = AddressNormalizer()
        self.phone_norm = PhoneNormalizer()
        self.category_map = CategoryMapper()
        self.brand_detector = brand_detector or BrandDetector()
        self.embeddings = embedding_service or get_embedding_service()
        self.quality = QualityScorer()

    async def warm_up(self, session: AsyncSession) -> None:
        """Pre-load brand cache. Call once on worker boot."""
        await self.brand_detector.refresh(session)

    async def process(self, session: AsyncSession, raw_poi_id: int) -> int | None:
        """Normalize a raw POI row.

        Returns the inserted ``processed_pois.id``, ``None`` if skipped
        (already processed or unusable payload).
        """
        raw = await session.get(RawPOI, raw_poi_id)
        if raw is None:
            logger.warning("normalize: raw_poi %d not found", raw_poi_id)
            return None
        if raw.processed_at is not None:
            # Already processed — return the existing processed_poi.id so
            # idempotent re-runs are seamless.
            existing = (
                await session.execute(
                    select(ProcessedPOI.id).where(ProcessedPOI.raw_poi_id == raw.id)
                )
            ).scalar_one_or_none()
            logger.debug(
                "normalize: raw_poi %d already processed at %s -> processed_poi %s",
                raw_poi_id, raw.processed_at, existing,
            )
            return existing

        source = await session.get(Source, raw.source_id)
        if source is None:
            logger.warning("normalize: source %d not found for raw_poi %d", raw.source_id, raw_poi_id)
            return None

        # 1. Extract canonical fields per source.
        try:
            extractor = get_extractor(source.code)
        except KeyError:
            logger.warning("normalize: no extractor for source %s", source.code)
            return None

        canonical = extractor.extract(raw.raw_payload or {})
        if canonical is None or not canonical.name:
            logger.info("normalize: raw_poi %d has no usable name; skipping", raw_poi_id)
            NORMALIZE_SKIPPED.labels(source.code, "no_name").inc()
            await self._mark_processed(session, raw)
            await session.commit()
            return None

        # 2. Normalize each field.
        addr_norm_str, addr_components = self.address_norm.normalize(canonical.address or "")
        phone_e164 = self.phone_norm.normalize(canonical.phone)
        top_cat, sub_cat = self.category_map.map_with_fallback(
            source.code, canonical.raw_category, canonical.name
        )

        if not self.brand_detector._brands:  # type: ignore[attr-defined]
            await self.brand_detector.refresh(session)
        brand_match = self.brand_detector.detect(canonical.name)

        # 3. Embedding (keep diacritics — MiniLM-L12 is multilingual).
        name_embedding = self.embeddings.encode(canonical.name)

        # 4. Quality.
        composite, factors = self.quality.score(
            source_code=source.code,
            fetched_at=raw.fetched_at,
            has_name=bool(canonical.name),
            has_address=bool(canonical.address),
            has_phone=bool(canonical.phone),
            has_website=bool(canonical.website),
            has_coordinates=canonical.location is not None,
            has_category=bool(top_cat),
            address_confidence=addr_components.confidence,
            phone_valid=phone_e164 is not None,
        )

        # 5. Insert (idempotent on raw_poi_id — same row, same processed once).
        location_wkt = None
        if canonical.location is not None:
            lat, lng = canonical.location
            location_wkt = f"SRID=4326;POINT({lng} {lat})"
        elif raw.location is not None:
            location_wkt = raw.location  # geography copies cleanly via cast in query
        else:
            # processed_pois.location is NOT NULL — without coords we can't process.
            logger.info("normalize: raw_poi %d has no coordinates; skipping", raw_poi_id)
            NORMALIZE_SKIPPED.labels(source.code, "no_coords").inc()
            await self._mark_processed(session, raw)
            await session.commit()
            return None

        website_domain = self._extract_domain(canonical.website)

        # Spatial admin-unit stamp: which province / district / ward contains
        # this point? Bbox lookup is a single indexed range scan.
        if canonical.location is not None:
            admin = await lookup_admin(session, canonical.location[0], canonical.location[1])
        else:
            admin = await lookup_admin(session, 0.0, 0.0)  # all None

        # Idempotency: skip if this raw_poi already produced a processed_poi.
        # (We don't have a unique index on raw_poi_id by design — Phase 4
        # might want to reprocess on schema upgrades — so dedup at insert time.)
        existing = (
            await session.execute(
                select(ProcessedPOI.id).where(ProcessedPOI.raw_poi_id == raw.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            await self._mark_processed(session, raw)
            await session.commit()
            return existing

        result = await session.execute(
            pg_insert(ProcessedPOI).values(
                raw_poi_id=raw.id,
                name_original=canonical.name,
                name_normalized=canonical.name.casefold().strip(),
                name_embedding=name_embedding,
                address_original=canonical.address,
                address_normalized=addr_norm_str or None,
                address_components=addr_components.to_dict(),
                phone_original=canonical.phone,
                phone_e164=phone_e164,
                website=canonical.website,
                website_domain=website_domain,
                openooh_category=top_cat,
                openooh_subcategory=sub_cat,
                raw_category=canonical.raw_category,
                brand=brand_match.name if brand_match else None,
                brand_confidence=brand_match.confidence if brand_match else None,
                province_code=admin.province_code,
                district_code=admin.district_code,
                ward_code=admin.ward_code,
                location=location_wkt,
                quality_score=composite,
                quality_factors=factors_to_dict(factors),
                merge_status="pending",
            ).returning(ProcessedPOI.id)
        )
        new_id = result.scalar_one()
        await self._mark_processed(session, raw)
        await session.commit()
        NORMALIZE_PROCESSED_INSERTED.labels(source.code).inc()
        logger.info(
            "normalize: raw_poi %d → processed_poi %d (q=%.2f, brand=%s, cat=%s)",
            raw.id, new_id, composite, brand_match.name if brand_match else "-", top_cat,
        )
        return new_id

    @staticmethod
    async def _mark_processed(session: AsyncSession, raw: RawPOI) -> None:
        raw.processed_at = datetime.now(timezone.utc)

    @staticmethod
    def _extract_domain(url: str | None) -> str | None:
        if not url:
            return None
        try:
            parsed = urlparse(url if "://" in url else f"http://{url}")
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
        return host[4:] if host.startswith("www.") else (host or None)


def factors_to_dict(f: Any) -> dict[str, float]:
    """Manual asdict to keep this module independent of dataclasses import."""
    return {
        "completeness": f.completeness,
        "freshness": f.freshness,
        "source_reliability": f.source_reliability,
        "address_confidence": f.address_confidence,
        "phone_valid": f.phone_valid,
        "has_coordinates": f.has_coordinates,
    }
