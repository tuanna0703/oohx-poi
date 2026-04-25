"""BrandDetector against the live brands table."""

from __future__ import annotations

import pytest

from poi_lake.db import get_sessionmaker
from poi_lake.pipeline.normalize import BrandDetector

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_detects_seeded_brands() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        d = BrandDetector()
        n = await d.refresh(session)
        assert n >= 60  # we seeded ~78

    # Each tuple: (name in the wild, expected canonical brand)
    cases = [
        ("Circle K — Bà Triệu", "Circle K"),
        ("Highlands Coffee Tràng Tiền", "Highlands Coffee"),
        ("Phở 24 Hàng Bài", "Pho 24"),
        ("Vietcombank chi nhánh Hà Nội", "Vietcombank"),
        ("VinMart+ Lê Đại Hành", "WinMart+"),
        ("KFC Bưu điện Hà Nội", "KFC"),
        ("Pharmacity 12B Phố Huế", "Pharmacity"),
    ]
    for raw_name, expected in cases:
        match = d.detect(raw_name)
        assert match is not None, f"no match for {raw_name!r}"
        assert match.name == expected, f"{raw_name!r} → {match.name}, expected {expected}"
        assert 0.5 <= match.confidence <= 1.0


async def test_no_match_returns_none() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        d = BrandDetector()
        await d.refresh(session)
    assert d.detect("Quán cơm bình dân Cô Hai") is None
