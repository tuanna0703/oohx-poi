"""Brand discovery's pure parts: name folding, keys, pruning, scores, decisions."""

from __future__ import annotations

from poi_lake.services import brand_ai
from poi_lake.services.app_settings import DEFAULTS
from poi_lake.services.brand_discovery import (
    decide,
    fold,
    keys_of,
    prune_keys,
    rule_score,
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
