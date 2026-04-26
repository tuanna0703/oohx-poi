"""PairSimilarityScorer."""

from __future__ import annotations

from poi_lake.pipeline.dedupe.similarity import PairSimilarityScorer


def _row(**kw):
    base = {
        "name_embedding": None,
        "address_normalized": None,
        "phone_e164": None,
        "website_domain": None,
        "brand": None,
    }
    base.update(kw)
    return base


def _norm_vec(seed: float, dim: int = 384) -> list[float]:
    """Build a normalized vector (length 1) from a seed, for cosine tests."""
    raw = [(seed * (i + 1)) % 7 - 3 for i in range(dim)]
    norm = sum(v * v for v in raw) ** 0.5
    return [v / norm for v in raw]


def test_identical_records_high_score() -> None:
    v = _norm_vec(0.7)
    a = _row(
        name_embedding=v,
        address_normalized="1 Tràng Tiền, Hoàn Kiếm, Hà Nội",
        phone_e164="+842438269999",
        website_domain="circlek.com.vn",
        brand="Circle K",
    )
    b = _row(**a)
    s = PairSimilarityScorer().score(a, b)
    assert s.composite >= 0.99
    assert s.name_similarity == 1.0
    assert s.phone_match == 1.0
    assert s.website_match == 1.0
    assert s.brand_match == 1.0


def test_different_records_low_score() -> None:
    a = _row(
        name_embedding=_norm_vec(1.1),
        address_normalized="1 Tràng Tiền, Hoàn Kiếm, Hà Nội",
        phone_e164="+842438269999",
        website_domain="circlek.com.vn",
        brand="Circle K",
    )
    b = _row(
        name_embedding=_norm_vec(2.7),
        address_normalized="100 Lê Lợi, Quận 1, Hồ Chí Minh",
        phone_e164="+842839302222",
        website_domain="phuclong.com.vn",
        brand="Phuc Long",
    )
    s = PairSimilarityScorer().score(a, b)
    assert s.composite < 0.5
    assert s.phone_match == 0.0
    assert s.website_match == 0.0
    assert s.brand_match == 0.0


def test_missing_fields_neutral() -> None:
    """Missing on either side → neutral (0.5), not a penalty."""
    v = _norm_vec(0.5)
    a = _row(name_embedding=v, address_normalized="X", brand="Y")
    b = _row(name_embedding=v, address_normalized="X")  # no brand
    s = PairSimilarityScorer().score(a, b)
    # name=1.0 + addr=1.0 + (phone+website+brand all 0.5 neutral) = 0.825.
    # That's NEEDS_LLM territory, which is the right outcome — without a
    # brand match we shouldn't auto-merge two records.
    assert s.brand_match == 0.5
    assert s.phone_match == 0.5
    assert s.website_match == 0.5
    assert 0.80 <= s.composite < 0.85


def test_address_token_set_handles_word_order() -> None:
    a = _row(address_normalized="1 Tràng Tiền, Hoàn Kiếm, Hà Nội")
    b = _row(address_normalized="Hà Nội, Hoàn Kiếm, 1 Tràng Tiền")
    s = PairSimilarityScorer().score(a, b)
    # token_set_ratio should reward identical tokens regardless of order.
    # Commas count as tokens, so the ratio is high (~0.97) but not 1.0.
    assert s.address_similarity >= 0.95


def test_partial_phone_difference_zero() -> None:
    a = _row(phone_e164="+842438269999")
    b = _row(phone_e164="+842438269998")  # last digit different
    s = PairSimilarityScorer().score(a, b)
    assert s.phone_match == 0.0


def test_score_is_serializable() -> None:
    a = _row()
    b = _row()
    s = PairSimilarityScorer().score(a, b)
    d = s.to_dict()
    assert "composite" in d and "name" in d and "address" in d
