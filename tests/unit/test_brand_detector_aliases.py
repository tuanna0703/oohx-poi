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


def test_a_pattern_only_brand_is_tagged_by_its_regex_alone() -> None:
    import re

    from poi_lake.seeds.vn_brands import BRANDS

    nk = next(b for b in BRANDS if b["name"] == "Nguyen Kim")
    assert nk["pattern_only"]
    d = BrandDetector()
    d._brands = [_CompiledBrand(
        name="Nguyen Kim", aliases_folded=(), pattern=re.compile(nk["match_pattern"], re.I | re.U),
        aliases=("Nguyen Kim", "Nguyễn Kim"), pattern_only=True,
    )]
    for shop in (
        "Trung Tâm Mua Sắm Điện Máy Nguyễn Kim Gò Vấp", "Điện máy Nguyễn Kim Vĩnh Long",
        "Siêu thị Điện máy Nguyễn Kim", "Trung Tâm Nguyễn Kim Hà Đông", "Nguyễn Kim Dĩ An",
        "Nguyễn Kim - Thủ Đức", "TTMS SBS Nguyễn Kim Dĩ An", "Nguyen Kim",
    ):
        m = d.detect(shop)
        assert m is not None and m.name == "Nguyen Kim", shop
    for other in (
        "Tiệm Vàng Kim Nguyên", "Nhà Thuốc Nguyễn Thị Kim Anh", "ATM BIDV Nguyễn Kim Thủ Đức",
        "TrueMoney Vietnam - Nguyễn Kim Thư", "Manwah - Taiwanese Hot Pot (Nguyễn Kim Cao Thắng)",
        "Nội Thất Nguyễn Kim", "BẢO HÀNH SỬA CHỮA ĐIỆN MÁY NGUYỄN KIM", "Cửa Hàng Vlxd Kim Nguyên",
    ):
        assert d.detect(other) is None, other
