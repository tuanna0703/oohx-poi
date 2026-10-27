"""Vietnam retail & services categories: names, searches, taxonomy wiring."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from poi_lake.pipeline.normalize import CategoryMapper
from poi_lake.pipeline.normalize.openooh_keywords import (
    DISTRICT_SWEEP_CODES,
    is_openooh_code,
    keywords_for_openooh,
)
from poi_lake.seeds.openooh_priority import priority_for
from poi_lake.seeds.openooh_taxonomy import TAXONOMY
from poi_lake.seeds.vn_retail_taxonomy import FIRST, VN_CATEGORIES, VN_CODES


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Tạp hóa Thu Hường", "retail.traditional_grocery"),
        ("Tạp hóa Trường An", "retail.traditional_grocery"),  # not a school
        ("Gara Ô tô Trường Phát", "retail.auto_repair"),
        ("Sửa xe ô tô Minh Khang", "retail.auto_repair"),
        ("Tiệm sửa xe Hùng", "retail.motorbike_repair"),
        ("Karaoke Tình Bạn", "entertainment.karaoke"),
        ("DK BILLIARD CLUB", "entertainment.billiards"),
        ("Tiệm vàng Kim Hoàn", "retail.jewelry"),
        ("Dịch Vụ Cầm Đồ Thế Anh", "financial.pawn_shops"),
        ("Chợ Bến Thành", "retail.markets"),
        ("CHỢ TÂN AN", "retail.markets"),
        ("Chè Thái Ý Phương", "hospitality.milk_tea"),
        ("Trà sữa Tocotoco", "hospitality.milk_tea"),
        ("Siêu thị Mẹ và Bé Bibo", "retail.mother_baby"),
        ("Sơn Lâm petshop 2", "retail.pet_stores"),
        # everyday type words the old rules missed
        ("Quầy Thuốc Nhã Phương", "retail.pharmacy"),
        ("ATM BIDV", "financial.atms"),
        ("CHXD Chính Phước", "retail.gas_stations"),
        ("Điện Máy Thanh Vũ", "retail.electronics"),
        ("Siêu Thị Điện Máy Xanh", "retail.electronics"),
        ("Hớt Tóc Hoàng Tiến", "health_and_beauty.barbers"),
        ("Siêu thị mini Bảo Ngọc", "retail.grocery"),
        ("G23 Mart", "retail.convenience_stores"),
        # added 2026-10-27
        ("Điểm Cung Cấp Dịch Vụ VNPT - Vinaphone", "retail.telecom"),
        ("Cửa Hàng Viettel Telecom số 451 Hà Huy Giáp", "retail.telecom"),
        ("MobiFone Thạnh Yên A", "retail.telecom"),
        ("Viettel Post Bưu Cục Tân Bình", "retail.courier"),
        ("NINJAVAN Định Hoá", "retail.courier"),
        ("Bưu điện Văn hóa xã Tân Lập", "retail.courier"),
        ("Trạm sạc VinFast Vincom Plaza", "transit.ev_charging"),
        ("Trạm đổi PIN XMĐ Vinfast", "transit.ev_charging"),
        ("Mua Bán Xe Máy,Xe Điện Vinfast", "retail.motorbike_dealers"),
        ("VINFAST TÂN Á CHÂU", "retail.dealerships"),
        ("VUS Tô Ký 2", "education.training_centers"),
        ("California Fitness & Yoga Đà Nẵng", "sports_and_fitness.gyms"),
        ("Quán Cơm Bình Dân Thu Hà", "hospitality.restaurants"),
        ("Hủ Tiếu Nam Vang Hồng Phát", "hospitality.restaurants"),
        ("Quán Bia Tươi 68", "hospitality.beer_halls"),
        ("Sacombank PGD Tân Phú", "financial.banks"),
        ("Nhà Nghỉ Hoàng Long", "travel.hotels"),
        ("Chung cư Hoàng Anh Gia Lai", "residential.apartment_buildings"),
        ("Thế Giới Di Động", "retail.electronics"),
        ("Tiệm Trà Sữa Cát Tường", "hospitality.milk_tea"),
        ("Trà sữa ăn vặt cô Hồng", "hospitality.milk_tea"),
        ("Ăn Vặt Cô Tấm", "hospitality.fast_food"),
        ("Máy tính - Camera Vinh Nguyễn", "retail.electronics"),
    ],
)
def test_names(name: str, expected: str) -> None:
    assert CategoryMapper().infer_from_name(name) == (expected.split(".")[0], expected)


@pytest.mark.parametrize(
    "name",
    [
        "Cao tốc Hà Nội - Hải Phòng",  # "tốc" is not "tóc"
        "Petrolimex Số 5",  # not a pet shop (brand rule aside, no "pet" word)
        "Nhà Chó Mèo",  # "chó" is not "chợ"
        "Chó mẹt Hoàng Long",  # nor as the first word
        "Cho thuê căn hộ 2PN",  # "cho thuê" = for rent
        "Walmart",  # "mart" whole word only
    ],
)
def test_no_false_hits(name: str) -> None:
    result = CategoryMapper().infer_from_name(name)
    assert result[1] not in {
        "health_and_beauty.salons", "retail.pet_stores", "retail.convenience_stores", "retail.markets",
    }, result


def test_existing_place_types_still_win() -> None:
    m = CategoryMapper()
    assert m.infer_from_name("Bệnh viện Đại học Y Hà Nội") == ("point_of_care", "point_of_care.hospitals")
    assert m.infer_from_name("Trường THCS Láng Thượng") == ("education", "education.schools")
    assert m.infer_from_name("Nhà hàng Karaoke Hoa Sen") == ("hospitality", "hospitality.restaurants")


def test_search_fallback_only_for_trusted_codes() -> None:
    m = CategoryMapper()
    assert m.classify("gosom_scraper", None, "Minh Anh", "retail.pharmacy") == ("retail", "retail.pharmacy")
    assert m.classify("gosom_scraper", None, "Cô Ba", "retail.traditional_grocery") == (
        "retail", "retail.traditional_grocery",
    )
    assert m.classify("gosom_scraper", None, "Vua Gà AZ", "financial.banks") == ("financial", None)
    assert m.classify("gosom_scraper", None, "Minh Anh", "entertainment.nightclubs") == (None, None)
    assert m.classify("gosom_scraper", None, "Minh Anh", "_probe") == (None, None)
    assert m.classify("gosom_scraper", None, "Minh Anh", "brand:circle k") == (None, None)
    # the name beats the search
    assert m.classify("gosom_scraper", None, "ATM ACB", "financial.banks") == ("financial", "financial.atms")


def test_codes_are_wired_everywhere() -> None:
    codes = {r["code"] for r in TAXONOMY}
    parents = {r["code"] for r in TAXONOMY if r["level"] == 1}
    assert len(VN_CODES) == len(VN_CATEGORIES), "no duplicate codes"
    for c in VN_CATEGORIES:
        assert c.code in codes
        assert c.code.split(".")[0] in parents
        assert is_openooh_code(c.code)
        assert keywords_for_openooh(c.code) == list(c.search)
        assert c.code in DISTRICT_SWEEP_CODES
    assert [priority_for(c) for c in FIRST] == [6, 7, 8, 9]
    assert all(100 <= priority_for(c) < 300 for c in VN_CODES - set(FIRST))


def _migration_rows(pattern: str) -> list[tuple[str, str]]:
    path = next(Path(__file__).parents[2].glob(f"migrations/versions/{pattern}"))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod._ROWS)


def test_migrations_match_the_seed() -> None:
    rows = _migration_rows("*_0007_vn_retail_categories.py") + _migration_rows("*_0015_more_vn_categories.py")
    assert sorted((c.code, c.name) for c in VN_CATEGORIES) == sorted(rows)
