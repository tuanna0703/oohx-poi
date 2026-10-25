"""Accent-insensitive SQL pre-filter for brand re-tagging."""

from __future__ import annotations

import re

from poi_lake.services.brands import accent_regex


def test_accent_regex_matches_every_vietnamese_form() -> None:
    rx = re.compile(accent_regex("soi") + r"\s+" + accent_regex("bien"))
    for name in ("Sói Biển", "SÓI BIỂN", "soi bien", "Hải sản Sói Biển 2"):
        assert rx.search(name), name
    assert not rx.search("Sói Xám")


def test_accent_regex_escapes_punctuation() -> None:
    assert re.search(accent_regex("winmart+"), "WinMart+ Ba Đình")
    assert re.fullmatch(accent_regex("a.b"), "a.b")
    assert not re.fullmatch(accent_regex("a.b"), "axb")
