"""BrandDetector's alias rule is brand_quality.name_matches: order matters
for short words, glued spellings count."""

from __future__ import annotations

from poi_lake.pipeline.normalize.brand import BrandDetector, _CompiledBrand


def _detector(*brands: tuple[str, list[str]]) -> BrandDetector:
    d = BrandDetector()
    d._brands = [
        _CompiledBrand(name=n, aliases_folded=(), pattern=None, aliases=(n, *aliases)) for n, aliases in brands
    ]
    return d


def test_short_words_keep_their_order() -> None:
    d = _detector(("Phê La", []))
    assert d.detect("Phê La Hồ Gươm") is not None
    assert d.detect("La Cà Phê Quận 3") is None


def test_glued_and_reordered_spellings_match() -> None:
    d = _detector(("WinMart+", ["VinMart+"]), ("Honda HEAD", []))
    m = d.detect("Win Mart Lê Lợi")
    assert m is not None and m.name == "WinMart+"
    m = d.detect("HEAD Honda Quốc Tiến")
    assert m is not None and m.name == "Honda HEAD"


def test_exact_name_is_more_confident_and_one_letter_aliases_are_ignored() -> None:
    d = _detector(("Long Châu", ["K"]))
    assert d.detect("Long Châu").confidence == 0.9  # type: ignore[union-attr]
    assert d.detect("Nhà thuốc Long Châu 12").confidence == 0.75  # type: ignore[union-attr]
    assert d.detect("Cửa hàng K") is None
