"""The resolver must be told how far apart the two records are.

The prompt used to instruct Claude to "consider coordinates" while
`_serialize_for_llm` sent none, so the model could only trust the address
string. Two rows 0.0 m apart under one plus code were rejected on production
because a mis-segmented Vietnamese address put them in "different districts".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poi_lake.pipeline.dedupe import merge as merge_mod
from poi_lake.pipeline.dedupe.decision import DedupeDecision
from poi_lake.pipeline.dedupe.merge import (
    MergeService,
    _haversine_meters,
    _pair_distance_meters,
)
from poi_lake.pipeline.dedupe.resolver import (
    _CACHE_KEY_PREFIX,
    _DISTANCE_BLOCK,
    _PROMPT_TEMPLATE,
    LLMResolver,
)


def _row(i: int, lat: float | None = None, lon: float | None = None) -> SimpleNamespace:
    r = SimpleNamespace(
        id=i,
        name_original=f"POI {i}",
        address_normalized=None,
        address_components=None,
        phone_e164=None,
        website=None,
        website_domain=None,
        brand=None,
        openooh_category=None,
        openooh_subcategory=None,
    )
    if lat is not None:
        r._lat, r._lon = lat, lon
    return r


def test_haversine_matches_a_known_separation() -> None:
    """One degree of latitude is ~111.2 km anywhere on the globe."""
    d = _haversine_meters(10.0, 106.0, 11.0, 106.0)
    assert 111_000 < d < 111_400, d


def test_haversine_is_zero_for_one_point() -> None:
    assert _haversine_meters(21.02, 105.84, 21.02, 105.84) == pytest.approx(0.0)


def test_distance_is_none_when_coords_were_never_attached() -> None:
    """Rows built by hand carry no _lat/_lon; the resolver just omits the line."""
    assert _pair_distance_meters(_row(1), _row(2)) is None


def test_distance_is_none_when_only_one_row_has_coords() -> None:
    assert _pair_distance_meters(_row(1, 10.0, 106.0), _row(2)) is None


def test_two_rows_a_few_metres_apart() -> None:
    # ~0.0001 degrees of latitude is ~11.1 m.
    d = _pair_distance_meters(_row(1, 10.0000, 106.0), _row(2, 10.0001, 106.0))
    assert d is not None
    assert 10.5 < d < 11.5, d


async def test_resolver_receives_the_pair_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    """_resolve_pair must hand the separation to the resolver, not drop it."""
    monkeypatch.setattr(merge_mod, "decide", lambda _s: DedupeDecision.NEEDS_LLM)
    monkeypatch.setattr(
        merge_mod.PairSimilarityScorer, "score", lambda self, a, b: SimpleNamespace(composite=0.7)
    )

    seen: list[float | None] = []

    class _Resolver:
        async def resolve(self, a: dict, b: dict, **kw: object) -> object:
            seen.append(kw.get("distance_meters"))  # type: ignore[arg-type]
            return SimpleNamespace(same=False, confidence=0.9, reason="", cached=False)

    svc = MergeService(resolver=_Resolver())  # type: ignore[arg-type]
    rows = [_row(1, 10.0, 106.0), _row(2, 10.0001, 106.0)]

    await svc._components_of(rows)

    assert len(seen) == 1
    assert seen[0] is not None
    assert 10.5 < seen[0] < 11.5, seen


def test_prompt_states_the_distance_when_it_is_known() -> None:
    body = _PROMPT_TEMPLATE.format(
        a_json="{}", b_json="{}", distance_block=_DISTANCE_BLOCK.format(meters=108.1)
    )
    assert "108.1 m" in body
    assert "mis-segmented" in body


def test_prompt_omits_the_distance_block_when_unknown() -> None:
    body = _PROMPT_TEMPLATE.format(a_json="{}", b_json="{}", distance_block="")
    assert " m." not in body
    assert "Consider: name, address" in body


def test_cache_key_is_namespaced_so_old_verdicts_cannot_answer_the_new_prompt() -> None:
    """A v1 verdict was formed without any geometry. It must not be reused."""
    assert _CACHE_KEY_PREFIX.endswith(":v2:")
    key = LLMResolver._cache_key({"id": 9}, {"id": 4})
    assert key == "poi-lake:dedupe:llm:v2:4:9", key
