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
    def __init__(self, same: bool = True) -> None:
        self.calls: list[tuple[int, int]] = []
        self.same = same

    async def resolve(self, a: dict, b: dict) -> object:
        self.calls.append((a["id"], b["id"]))
        return SimpleNamespace(same=self.same, confidence=0.9, reason="test")


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

    components, llm_calls, llm_errors = await svc._components_of(_rows(4))

    assert len(components) == 1, "all four rows are the same place"
    assert len(components[0]) == 4
    assert llm_errors == 0
    # Without the skip this is 6 — every i<j pair.
    assert llm_calls == 3, f"expected 3 verdicts, made {resolver.calls}"


async def test_distinct_rows_still_compare_every_pair() -> None:
    """When nothing merges, no component forms, so no pair may be skipped."""
    resolver = _CountingResolver(same=False)
    svc = MergeService(resolver=resolver)  # type: ignore[arg-type]

    components, llm_calls, _ = await svc._components_of(_rows(4))

    assert len(components) == 4, "nothing merged"
    assert llm_calls == 6, "all 4C2 pairs must still be judged"
