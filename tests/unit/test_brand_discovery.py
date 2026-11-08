"""Brand discovery's pure parts: name folding, keys, pruning, scores, decisions."""

from __future__ import annotations

from poi_lake.services import brand_ai
from poi_lake.services.app_settings import DEFAULTS
from poi_lake.services.brand_discovery import (
    BrandMatcher,
    acronym,
    blocked_by,
    compact_key,
    decide,
    fold,
    generic_reason,
    keys_of,
    prune_keys,
    rule_score,
    script_reason,
    sensitive_reason,
    strip_prefix,
    tokens,
    total_score,
)


def test_fold_drops_accents_and_d() -> None:
    assert fold("Siêu Thị Mẹ Bầu & Em Bé Con Cưng Đà Nẵng") == "sieu thi me bau & em be con cung da nang"


def test_shop_type_prefix_is_skipped_before_the_brand() -> None:
    folded, original = tokens("Siêu thị Điện máy MediaMart Gia Lộc")
    start = strip_prefix(folded)
    assert original[start] == "MediaMart"
    assert keys_of(folded, start)[0] == ("mediamart",)


def test_generic_words_and_numbers_make_no_key() -> None:
    folded, _ = tokens("Quán Cà Phê 24")
    assert keys_of(folded, strip_prefix(folded)) == []


def test_the_longer_key_wins_when_it_keeps_the_stores() -> None:
    counts = {("con",): 500, ("con", "cung"): 446, ("con", "cung", "ha"): 12}
    assert prune_keys(counts) == {("con", "cung")}


def test_the_shorter_key_stays_when_the_longer_is_a_branch() -> None:
    counts = {("mixue",): 46, ("mixue", "ice"): 9}
    assert prune_keys(counts) == {("mixue",)}


def test_rule_score_rewards_spread_purity_and_position() -> None:
    strong = rule_score(stores=446, provinces=40, purity=0.95, position=0.95, common=False)
    weak = rule_score(stores=10, provinces=2, purity=0.5, position=0.4, common=True)
    assert 60 <= strong <= 70 and weak < 30


def test_a_not_a_chain_verdict_caps_the_score() -> None:
    assert total_score(65, {"is_chain": False, "confidence": 0.9}) == 40
    assert total_score(65, {"is_chain": True, "confidence": 0.9}) == 92
    assert total_score(65, None) == 65


def test_auto_add_needs_the_switch_the_score_the_spread_and_the_ai() -> None:
    settings = {**DEFAULTS, "auto_add": True}
    good = {"score": 90, "stores": 446, "provinces": 40, "ai": {"is_chain": True}}
    assert decide(good, settings) == "auto_added"
    assert decide(good, {**settings, "auto_add": False}) == "pending"
    assert decide({**good, "ai": None}, settings) == "pending"
    assert decide({**good, "stores": 5}, settings) == "pending"
    assert decide({**good, "score": 30}, settings) == "auto_rejected"


def test_blocked_by_names_every_test_the_candidate_fails() -> None:
    settings = {**DEFAULTS, "auto_add": True, "add_at": 70, "min_stores": 15, "min_provinces": 3}
    good = {"status": "pending", "score": 90, "stores": 446, "provinces": 40,
            "ai": {"is_chain": True}, "signals": []}
    assert blocked_by(good, settings) == []
    assert blocked_by({**good, "status": "added"}, settings) is None
    assert blocked_by({**good, "provinces": 2, "score": 68}, {**settings, "auto_add": False}) == [
        {"code": "auto_add_off"},
        {"code": "score", "have": 68, "need": 70},
        {"code": "provinces", "have": 2, "need": 3},
    ]
    assert blocked_by({**good, "ai": None}, settings) == [{"code": "ai_missing"}]
    assert blocked_by({**good, "ai": {"is_chain": False}}, settings) == [{"code": "ai_not_chain"}]
    merge = {**good, "ai": {"is_chain": True, "existing_brand": "MB Bank"}}
    assert blocked_by(merge, settings) == [{"code": "existing_brand", "brand": "MB Bank"}]
    generic = {**good, "signals": [{"label": "Tên chung chung", "good": False,
                                    "text": "tên một món ăn: cần người duyệt"}]}
    assert blocked_by(generic, settings) == [{"code": "generic", "reason": "tên một món ăn"}]


def test_ai_reply_parsing_tolerates_fences() -> None:
    assert brand_ai.parse('```json\n{"is_chain": true}\n```') == {"is_chain": True}


def test_provider_order_and_cost() -> None:
    settings = {"providers": {
        "claude": {"model": "claude-haiku-5-5", "role": "fallback"},
        "openai": {"model": "some-model", "role": "primary"},
    }}
    assert brand_ai.order(settings) == ["openai", "claude"]
    settings["providers"]["openai"]["role"] = "off"
    assert brand_ai.order(settings) == ["claude"]
    assert brand_ai.cost("claude", "claude-haiku-5-5", 1_000_000, 0) == 0.10
    assert brand_ai.cost("openai", "some-model", 1000, 1000) is None


PLACES = frozenset({"ha noi", "da nang", "dong nai"})


def _why(name: str, purity: float | None = 0.95) -> str | None:
    return generic_reason(tuple(tokens(name)[0]), PLACES, purity)


def test_generic_names_wait_for_review() -> None:
    for name in [
        "Gia Truyền", "Phin", "Bao Cấp", "Dubai", "Việt Pháp", "Dược Hà Nội", "Tây Minh Châu",
        "sữa DHA MAX", "HEAD", "Thế giới đèn", "Đạt Chuẩn GPP", "Cơ Điện Lạnh", "Dê Núi",
        "Burger Việt", "Phòng Khám Đa Khoa Sài Gòn", "Điểm Bưu Điện Văn Hóa Xã",
        "Hoàng Barber", "An Coffee Tea", "K Market", "K Coffee", "Di Động T",
    ]:
        assert _why(name), name


def test_chain_names_pass() -> None:
    for name in [
        "Napoli Coffee", "KienlongBank", "TOKYOLIFE", "Mì cay Yagami", "Bama Mart", "Toyota",
        "Shinhan Bank", "Vinamilk", "Gu Coffee", "Circa", "ICOOL", "Mì Cay SEOUL",
    ]:
        assert _why(name) is None, name


def test_a_chain_named_like_a_person_waits_too() -> None:
    # "Nắng" is also a given name: rules cannot tell it from "An Coffee Tea", a person does.
    assert _why("Nắng Coffee & Tea")


def test_stores_of_unrelated_kinds_wait_for_review() -> None:
    assert _why("Commonwealth Bank", 0.57)
    assert _why("Commonwealth Bank", 0.9) is None


def test_gambling_and_adult_names_are_caught_with_their_accents() -> None:
    for name in ("OKVIP", "Okvip88 Club", "Nhà Cái Uy Tín", "Nha Cai Uy Tin", "Tài Xỉu Online",
                 "Cá độ bóng đá", "Lô Đề 24h", "Nổ Hũ Club", "Casino Hồ Tràm", "W88", "Bet Shop",
                 "Sex Shop"):
        assert sensitive_reason(name), name


def test_names_that_only_fold_like_gambling_pass() -> None:
    for name in ("Phở Bết", "Cà Đỗ", "Ca Do", "Lò Đề", "Nhà Cải Tạo", "Bò Nổ Hủ", "Sexy Nails",
                 "Betong Coffee", "Highlands Coffee", "Ngân hàng Đông Á"):
        assert sensitive_reason(name) is None, name


def test_names_not_in_latin_letters() -> None:
    assert script_reason("ដេប៉ូតេលា") == "tên viết bằng chữ Khmer"
    assert script_reason("中国建设银行") == "tên viết bằng chữ Hán"
    assert script_reason("ร้านกาแฟ") == "tên viết bằng chữ Thái"
    for name in ("ACLEDA", "Đông Á", "Café 咖啡", "7-Eleven", ""):
        assert script_reason(name) is None, name


def test_blocked_by_names_sensitive_and_script() -> None:
    settings = {**DEFAULTS, "auto_add": True, "add_at": 70, "min_stores": 1, "min_provinces": 1}
    good = {"status": "pending", "score": 90, "stores": 40, "provinces": 9,
            "ai": {"is_chain": True}, "signals": []}
    assert blocked_by({**good, "name": "OKVIP"}, settings) == [
        {"code": "sensitive", "reason": 'tên trang cá cược "okvip"'}]
    flagged = {**good, "name": "Lucky", "ai": {"is_chain": True, "sensitive": True}}
    assert [b["code"] for b in blocked_by(flagged, settings)] == ["sensitive"]
    assert blocked_by({**good, "name": "ដេប៉ូតេលា"}, settings) == [
        {"code": "script", "reason": "tên viết bằng chữ Khmer"}]
    assert blocked_by({**good, "name": "Highlands Coffee"}, settings) == []


def test_ai_verdict_carries_sensitive() -> None:
    v = brand_ai._verdict("claude", "m", {"is_chain": True, "sensitive": True, "confidence": 1}, "X")
    assert v.sensitive is True and v.as_json()["sensitive"] is True
    assert brand_ai._verdict("claude", "m", {"is_chain": True}, "X").sensitive is False
    assert "sensitive" in brand_ai.VERDICT_SCHEMA["required"]


def test_compact_keys_drop_company_words() -> None:
    assert compact_key("ACLEDA Bank Plc") == compact_key("ACLEDA") == "acleda"
    assert compact_key("Vietcom Bank") == compact_key("Vietcombank") == "vietcom"
    assert compact_key("Ngân hàng Đông Á") == "donga"
    assert compact_key("Thu Hằng Shop") == "thuhangshop", "hang alone is a name, not a company word"
    assert acronym("Saigon Commercial Bank") == "scb"
    assert acronym("Bank for Investment and Development of Vietnam") == "bidv"
    assert acronym("Lakaa Tea & Coffee") is None, "no company word: initials mean nothing"


def test_another_spelling_of_a_known_brand_is_found() -> None:
    m = BrandMatcher()
    for name, aliases in (("ACLEDA", []), ("SCB", []), ("Vietcombank", []), ("Highlands Coffee", []),
                          ("Agribank", ["Vietnam Bank for Agriculture and Rural Development"]),
                          ("BIDV", []), ("MB Bank", [])):
        m.add(name, *aliases)
    assert m.find("ACLEDA Bank Plc") == "ACLEDA"
    assert m.find("Saigon Commercial Bank") == "SCB"
    assert m.find("Vietcom Bank") == "Vietcombank"
    assert m.find("Highland Coffee") == "Highlands Coffee"
    assert m.find("Vietnam Bank For Agriculture And Rural Development") == "Agribank"
    assert m.find("Bank for Investment and Development of Vietnam") == "BIDV"
    assert m.find("MB") == "MB Bank"
    for other in ("An Coffee Bar", "Highlands Tea", "Mono", "Lakaa Tea & Coffee", "Thu Hằng Shop"):
        assert m.find(other) is None, other


def test_strict_matching_between_candidates_takes_compact_keys_only() -> None:
    m = BrandMatcher(strict=True)
    m.add("ACLEDA")
    m.add("SCB")
    assert m.find("ACLEDA Bank Plc") == "ACLEDA"
    assert m.find("Saigon Commercial Bank") is None
    m.add("Highlands Coffee")
    assert m.find("Highland Coffee") is None
