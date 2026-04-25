"""Quality scorer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from poi_lake.pipeline.quality import QualityScorer


def _now_minus(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def test_full_record_high_score() -> None:
    s = QualityScorer()
    composite, factors = s.score(
        source_code="google_places",
        fetched_at=_now_minus(1),
        has_name=True,
        has_address=True,
        has_phone=True,
        has_website=True,
        has_coordinates=True,
        has_category=True,
        address_confidence=0.95,
        phone_valid=True,
    )
    assert composite > 0.85
    assert factors.completeness == 1.0
    assert factors.has_coordinates == 1.0
    assert factors.source_reliability == 0.90


def test_freshness_decays() -> None:
    s = QualityScorer()
    young = s.score(
        source_code="osm_overpass",
        fetched_at=_now_minus(0),
        has_name=True, has_address=False, has_phone=False, has_website=False,
        has_coordinates=True, has_category=True,
        address_confidence=0.5, phone_valid=False,
    )
    old = s.score(
        source_code="osm_overpass",
        fetched_at=_now_minus(720),  # 2 years
        has_name=True, has_address=False, has_phone=False, has_website=False,
        has_coordinates=True, has_category=True,
        address_confidence=0.5, phone_valid=False,
    )
    assert young[1].freshness > old[1].freshness


def test_minimal_record_low_score() -> None:
    s = QualityScorer()
    composite, _ = s.score(
        source_code="foody",
        fetched_at=_now_minus(0),
        has_name=True,
        has_address=False, has_phone=False, has_website=False,
        has_coordinates=False, has_category=False,
        address_confidence=0.0, phone_valid=False,
    )
    assert composite < 0.4


def test_unknown_source_default_reliability() -> None:
    s = QualityScorer()
    _, factors = s.score(
        source_code="brand_new_source",
        fetched_at=_now_minus(1),
        has_name=True, has_address=True, has_phone=True, has_website=True,
        has_coordinates=True, has_category=True,
        address_confidence=1.0, phone_valid=True,
    )
    assert factors.source_reliability == 0.5
