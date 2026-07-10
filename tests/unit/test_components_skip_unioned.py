"""Pairs already in the same component must not be sent to the LLM.

Union-find is transitive: once A≡B and B≡C are settled, asking "is A the same
as C?" buys nothing and costs an API round-trip. Dense clusters — the ones that
dominate the Anthropic bill — are exactly where the redundant pairs pile up.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poi_lake.pipeline.dedupe import merge as merge_mod
from poi_lake.pipeline.dedupe.decision import DedupeDecision
from poi_lake.pipeline.dedupe.merge import MergeService


class _CountingResolver:
    def __init__(self, same: bool = True, cached: bool = False) -> None:
        self.calls: list[tuple[int, int]] = []
        self.distances: list[object] = []
        self.same = same
        self.cached = cached

    async def resolve(self, a: dict, b: dict, **kw: object) -> object:
        self.calls.append((a["id"], b["id"]))
        self.distances.append(kw.get("distance_meters"))
        return SimpleNamespace(same=self.same, confidence=0.9, reason="test", cached=self.cached)


def _rows(n: int) -> list[SimpleNamespace]:
    """Just enough of a ProcessedPOI for _components_of + _serialize_for_llm."""
    return [
        SimpleNamespace(
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
        for i in range(1, n + 1)
    ]


@pytest.fixture(autouse=True)
def _all_pairs_need_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_mod, "decide", lambda _s: DedupeDecision.NEEDS_LLM)
    monkeypatch.setattr(
        merge_mod.PairSimilarityScorer,
        "score",
        lambda self, a, b: SimpleNamespace(composite=0.7),
    )


async def test_four_identical_rows_cost_three_calls_not_six() -> None:
    """n rows that are all the same place need n-1 verdicts, not n(n-1)/2."""
    resolver = _CountingResolver(same=True)
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    components, usage = await svc._components_of(_rows(4))

    assert len(components) == 1, "all four rows are the same place"
    assert len(components[0]) == 4
    assert usage.errors == 0
    # Without the skip this is 6 — every i<j pair.
    assert usage.calls == 3, f"expected 3 verdicts, made {resolver.calls}"


async def test_distinct_rows_still_compare_every_pair() -> None:
    """When nothing merges, no component forms, so no pair may be skipped."""
    resolver = _CountingResolver(same=False)
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    components, usage = await svc._components_of(_rows(4))

    assert len(components) == 4, "nothing merged"
    assert usage.calls == 6, "all 4C2 pairs must still be judged"


async def test_cache_hits_are_not_counted_as_billed_calls() -> None:
    """A warm cache must report llm_cached, never llm_calls.

    Re-merge re-asks pairs the normal pass already paid for. Counting those
    Redis reads as calls made a 5-second run look like a 141-call spend.
    """
    resolver = _CountingResolver(same=False, cached=True)
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    _, usage = await svc._components_of(_rows(4))

    assert usage.cached == 6, "every verdict came from Redis"
    assert usage.calls == 0, "nothing was billed"
    assert usage.as_stats() == {"llm_calls": 0, "llm_cached": 6, "llm_errors": 0}
