"""Brand quality rules: the same cases as bizmap's tests/brandQuality.test.ts,
so the two implementations cannot drift apart unnoticed."""

from __future__ import annotations

from poi_lake.services.brand_quality import (
    INDUSTRIES,
    Chain,
    canonical_name,
    core_words,
    decide,
    display_name,
    industry_for_code,
    industry_of_brand_name,
    industry_of_name,
    merge_groups,
    name_matches,
    name_share,
)


def test_a_store_carries_its_chains_name_as_whole_words_or_glued() -> None:
    assert name_matches("WinMart+ 12 Lê Lợi", "WinMart+")
    assert name_matches("Win Mart Quận 1", "WinMart+")
    assert name_matches("Cửa hàng Vinamilk - Giấc Mơ Sữa Việt", "Vinamilk")
    assert not name_matches("Nhà Thuốc Gia Truyền Đức Thịnh", "Phở Gia Truyền")
    assert not name_matches("ACBank Store", "ACB"), "a short name does not match inside a word"


def test_a_detector_mistake_has_almost_no_store_carrying_the_name_and_is_hidden() -> None:
    names = ["Nhà Thuốc Gia Truyền A", "Thuốc Nam Gia Truyền B", "Đông Y Gia Truyền C", "Phở Gia Truyền Bát Đàn"]
    assert name_share(names, "Phở Gia Truyền") == 0.25
    d = decide("Phở Gia Truyền", names, "pharmacy")
    assert (d.verdict, d.confidence) == ("hide", "low")


def test_store_names_put_a_chain_in_the_right_industry_against_the_lakes_code() -> None:
    names = ["Vinamilk - Giấc Mơ Sữa Việt", "Cửa hàng Vinamilk", "Vinamilk Sữa tươi", "Vinamilk"]
    d = decide("Vinamilk", names, "electronics")
    assert (d.verdict, d.industry) == ("keep", "mother-baby")


def test_keywords_specific_trades_win_over_generic_words() -> None:
    assert industry_of_name("Nhà thuốc Long Châu") == "pharmacy"
    assert industry_of_name("Nha Khoa Việt Pháp") == "clinic"
    assert industry_of_name("Tôn Hoa Sen Bình Dương") == "misc"
    assert industry_of_name("Phở Thìn") == "restaurant"
    assert industry_of_name("Karaoke Galaxy") == "leisure"
    assert industry_of_name("Highlands Coffee") == "coffee"
    assert industry_of_name("Circle K") is None


def test_the_lakes_code_stands_when_names_say_nothing_at_medium_confidence() -> None:
    d = decide("Circle K", ["Circle K", "Circle K Lê Lợi", "Circle K Viet Nam", "Circle K Tôn Đức Thắng"], "supermarket")
    assert (d.industry, d.confidence) == ("supermarket", "medium")


def test_agreement_between_names_and_the_lake_is_high_confidence() -> None:
    d = decide("Long Châu", ["Nhà thuốc Long Châu 1", "Nhà thuốc Long Châu 2", "Long Châu"], "pharmacy")
    assert (d.industry, d.confidence) == ("pharmacy", "high")


def test_generic_suffixes_fold_brand_formats_into_one_brand() -> None:
    assert canonical_name("MB ATM") == "mb"
    assert canonical_name("MB Smartbank") == "mb"
    assert canonical_name("Hoa Sen Home") == "hoa sen"
    assert canonical_name("ATM") == "atm", "a name is never emptied"
    groups = merge_groups([
        Chain("MB ATM", "MB ATM", "bank", 636),
        Chain("MB Smartbank", "MB Smartbank", "bank", 131),
        Chain("Hoa Sen Home", "Hoa Sen Home", "misc", 45),
    ])
    assert [[c.key for c in g] for g in groups] == [["MB ATM", "MB Smartbank"]]


def test_the_chain_with_most_stores_leads_a_merge_and_industry_must_agree() -> None:
    groups = merge_groups([
        Chain("Viettel", "Viettel", "telecom", 40),
        Chain("Viettel Store", "Viettel Store", "telecom", 300),
        Chain("Viettel Post", "Viettel Post", "courier", 500),
    ])
    assert [[c.key for c in g] for g in groups] == [["Viettel Store", "Viettel"]]


def test_display_name_drops_a_trailing_format_word() -> None:
    assert display_name("Viettel Store") == "Viettel"
    assert display_name("MB ATM") == "MB"
    assert display_name("DongA Bank ATM") == "DongA Bank"
    assert display_name("ATM") == "ATM"


def test_shops_that_leave_out_filler_words_or_swap_them_still_carry_the_name() -> None:
    assert core_words("The Coffee House") == "coffee house"
    assert core_words("Trung Nguyên Legend") == "trung nguyen"
    assert name_matches("Coffee House Lê Lợi", "The Coffee House")
    assert name_matches("Trung Nguyên E-Coffee", "Trung Nguyên Legend")
    assert name_matches("HEAD Honda Quốc Tiến", "Honda HEAD")
    assert name_matches("Nha Khoa Pháp Việt", "Nha Khoa Việt Pháp")
    assert not name_matches("La Cà Phê", "Phê La"), "short words keep their order"


def test_a_stray_word_in_a_few_shop_names_does_not_refile_a_chain() -> None:
    names = ["Circle K", "Circle K Q1", "Circle K Q3", "Circle K Q5", "Circle K Q7", "Circle K Q10", "Circle K Tôn Thép"]
    assert decide("Circle K", names, "supermarket").industry == "supermarket"


def test_a_keyword_glued_into_the_chains_own_name_counts() -> None:
    assert industry_of_brand_name("LPBank") == "bank"
    assert industry_of_brand_name("Napoli Coffee") == "coffee"
    # A glued suffix only confirms: MediaMart sells electronics, as the lake says.
    assert decide("MediaMart", ["MediaMart Hà Đông", "MediaMart Cầu Giấy"], "electronics").industry == "electronics"
    d = decide("LPBank", ["LPBank PGD Bưu điện A", "LPBank PGD Bưu điện B", "ATM LPBank"], "bank")
    assert (d.industry, d.confidence) == ("bank", "high")


def test_car_makers_are_vehicles_whatever_the_lakes_code_says() -> None:
    assert decide("Toyota", ["Toyota Lý Thường Kiệt", "Toyota Đông Sài Gòn"], "fuel").industry == "vehicles"


def test_no_store_names_is_no_evidence_never_hidden() -> None:
    d = decide("WinMart+", [], "supermarket")
    assert (d.verdict, d.industry) == ("keep", "supermarket")


def test_place_names_are_not_trades() -> None:
    assert industry_of_name("MediaMart Cầu Giấy") is None
    assert industry_of_name("Circle K Tôn Đức Thắng") is None


def test_lake_codes_map_to_industries_exact_code_first_then_level_one() -> None:
    assert industry_for_code("retail.mother_baby") == "mother-baby"
    assert industry_for_code("financial.atms") == "bank"
    assert industry_for_code("financial.pawn_shops") == "consumer-finance"
    assert industry_for_code("hospitality.something_new") == "restaurant"
    assert industry_for_code("education.schools") == "education"
    assert industry_for_code("retail.unknown_shop") is None
    assert industry_for_code(None) is None
    assert set(INDUSTRIES) >= {"misc", "telecom", "vehicles"}
