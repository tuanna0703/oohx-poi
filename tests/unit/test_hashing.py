"""Tests for content_hash."""

from __future__ import annotations

import pytest

from poi_lake.services.hashing import content_hash


def test_stable_across_key_order() -> None:
    a = {"name": "Phở 24", "address": "Hà Nội", "rating": 4.5}
    b = {"rating": 4.5, "address": "Hà Nội", "name": "Phở 24"}
    assert content_hash(a) == content_hash(b)


def test_changes_with_payload() -> None:
    base = {"name": "Circle K", "address": "1 Tràng Tiền"}
    other = {"name": "Circle K", "address": "2 Tràng Tiền"}
    assert content_hash(base) != content_hash(other)


def test_handles_unicode_literally() -> None:
    h = content_hash({"name": "Phố Cổ"})
    # Hash is stable across runs and includes the unescaped character bytes.
    assert h == content_hash({"name": "Phố Cổ"})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_nested_structures() -> None:
    payload = {
        "name": "X",
        "tags": ["a", "b", "c"],
        "hours": {"mon": [9, 18], "tue": [9, 18]},
    }
    assert content_hash(payload) == content_hash(
        {
            "tags": ["a", "b", "c"],
            "hours": {"tue": [9, 18], "mon": [9, 18]},
            "name": "X",
        }
    )


def test_rejects_non_serializable() -> None:
    with pytest.raises(TypeError, match="non-JSON-serializable"):
        content_hash({"k": object()})


def test_tuple_treated_as_list() -> None:
    assert content_hash({"loc": (21.0, 105.8)}) == content_hash({"loc": [21.0, 105.8]})
