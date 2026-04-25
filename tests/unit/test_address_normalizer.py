"""VN address normalization."""

from __future__ import annotations

from poi_lake.pipeline.normalize import AddressNormalizer


def test_full_hanoi_address() -> None:
    norm = AddressNormalizer()
    raw = "1 Tràng Tiền, Hoàn Kiếm, Hà Nội, Việt Nam"
    _, c = norm.normalize(raw)
    assert c.house_number == "1"
    assert c.street == "Tràng Tiền"
    assert c.ward == "Hoàn Kiếm"
    # ward & district fall through positionally; second comma-token landed in ward
    assert c.country == "Việt Nam"
    assert c.confidence > 0.5


def test_explicit_prefixes() -> None:
    norm = AddressNormalizer()
    _, c = norm.normalize("35 Tràng Tiền, P. Tràng Tiền, Q. Hoàn Kiếm, TP. Hà Nội")
    assert c.street == "Tràng Tiền"
    assert c.ward == "Tràng Tiền"
    assert c.district == "Hoàn Kiếm"
    assert c.province == "Hà Nội"
    assert c.confidence == 1.0 - 0.05  # missing country → 0.95


def test_hcm_abbreviation() -> None:
    norm = AddressNormalizer()
    _, c = norm.normalize("100 Nguyễn Huệ, P. Bến Nghé, Q.1, TP. HCM")
    assert c.province == "Hồ Chí Minh"
    assert c.district == "1"


def test_handles_house_number_range() -> None:
    norm = AddressNormalizer()
    _, c = norm.normalize("12A/34 Nguyễn Trãi, Thanh Xuân, Hà Nội")
    assert c.house_number == "12A/34"
    assert c.street == "Nguyễn Trãi"


def test_empty_input() -> None:
    norm = AddressNormalizer()
    s, c = norm.normalize("")
    assert s == ""
    assert c.confidence == 0.0
    assert c.street is None


def test_province_recognized_without_prefix() -> None:
    norm = AddressNormalizer()
    _, c = norm.normalize("Đường ABC, Đà Nẵng")
    assert c.province == "Đà Nẵng"


def test_render_keeps_diacritics() -> None:
    norm = AddressNormalizer()
    rendered, _ = norm.normalize("16 Hàng Bài, Hoàn Kiếm, Hà Nội")
    assert "Hà Nội" in rendered
    assert "Hàng Bài" in rendered
