"""OpenOOH code → search keyword translation."""

from __future__ import annotations

from poi_lake.pipeline.normalize.openooh_keywords import (
    is_openooh_code,
    keywords_for_openooh,
)


def test_level2_exact_match() -> None:
    kws = keywords_for_openooh("retail.convenience_stores")
    assert "convenience store" in kws
    assert "circle k" in kws
    assert "cua hang tien loi" in kws


def test_level1_match() -> None:
    kws = keywords_for_openooh("hospitality")
    assert "restaurant" in kws or "cafe" in kws


def test_unknown_level2_falls_back_to_level1() -> None:
    """An unknown ``retail.something_new`` should still get retail-ish keywords."""
    kws = keywords_for_openooh("retail.brand_new_subcategory")
    assert "store" in kws or "shop" in kws


def test_completely_unknown_code_returns_default() -> None:
    kws = keywords_for_openooh("totally_unknown_taxonomy")
    assert kws == ["shop", "restaurant"]


def test_none_or_empty_returns_default() -> None:
    assert keywords_for_openooh(None) == ["shop", "restaurant"]
    assert keywords_for_openooh("") == ["shop", "restaurant"]
    assert keywords_for_openooh("   ") == ["shop", "restaurant"]


def test_is_openooh_code_recognizes_known() -> None:
    assert is_openooh_code("retail.convenience_stores") is True
    assert is_openooh_code("hospitality") is True
    assert is_openooh_code("retail.unknown_sub") is True  # level-1 prefix matches


def test_is_openooh_code_rejects_free_text() -> None:
    assert is_openooh_code("circle k") is False
    assert is_openooh_code("Quán cà phê") is False
    assert is_openooh_code("cafe") is False  # no dot, no level-1 match
    assert is_openooh_code(None) is False
    assert is_openooh_code("") is False


def test_case_insensitive() -> None:
    assert is_openooh_code("RETAIL.CONVENIENCE_STORES") is True
    kws_upper = keywords_for_openooh("RETAIL.CONVENIENCE_STORES")
    kws_lower = keywords_for_openooh("retail.convenience_stores")
    assert kws_upper == kws_lower


def test_gosom_adapter_uses_keyword_list_for_openooh_codes() -> None:
    """End-to-end: passing an OpenOOH code into the adapter triggers the
    keyword expansion rather than a verbatim single-keyword query."""
    from poi_lake.adapters.gosom_scraper import GosomScraperAdapter

    kws = GosomScraperAdapter._keywords_for("retail.convenience_stores")
    assert len(kws) >= 5
    assert "circle k" in kws


def test_gosom_adapter_passes_through_free_text() -> None:
    from poi_lake.adapters.gosom_scraper import GosomScraperAdapter

    assert GosomScraperAdapter._keywords_for("circle k") == ["circle k"]
    assert GosomScraperAdapter._keywords_for("Quán cà phê") == ["Quán cà phê"]


def test_gosom_adapter_default_when_no_category() -> None:
    from poi_lake.adapters.gosom_scraper import GosomScraperAdapter

    assert GosomScraperAdapter._keywords_for(None) == [
        "restaurant", "cafe", "convenience store", "shop"
    ]
