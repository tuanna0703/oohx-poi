"""Phone normalization to E.164."""

from __future__ import annotations

import pytest

from poi_lake.pipeline.normalize import PhoneNormalizer


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Hanoi landlines (024 area code, prefix 38 used by VNPT)
        ("024-3826-9999", "+842438269999"),
        ("+84 24 3826 9999", "+842438269999"),
        # Mobile (Viettel 09x, MobiFone 0901, etc.)
        ("0901 234 567", "+84901234567"),
        ("+84 901 234 567", "+84901234567"),
        ("0987654321", "+84987654321"),
    ],
)
def test_valid_vn_phones(raw: str, expected: str) -> None:
    assert PhoneNormalizer().normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "abc", "123", "00000"])
def test_invalid_phones_return_none(raw) -> None:
    assert PhoneNormalizer().normalize(raw) is None
