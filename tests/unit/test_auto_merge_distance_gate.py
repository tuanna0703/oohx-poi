"""A high similarity score must not merge two places that sit far apart.

`PairSimilarityScorer` weighs name, address, phone, website and brand — no
geometry. For a chain, every one of those is identical across branches: two
Sacombank ATMs 217 m apart in Ho Chi Minh City scored composite 0.9676, took
the AUTO_MERGE branch, and were folded together without the resolver ever
being asked. (Asked directly, it rejects the pair 5 times out of 5.)

DBSCAN's eps bounds the hop between neighbours, not the span of a cluster.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poi_lake.pipeline.dedupe import merge as merge_mod
from poi_lake.pipeline.dedupe.decision import DedupeDecision
from poi_lake.pipeline.dedupe.merge import MergeService

# ~0.001 degrees of latitude is ~111 m.
NEAR = (10.0000, 106.0)
MID = (10.0001, 106.0)  # ~11 m from NEAR
FAR = (10.0020, 106.0)  # ~222 m from NEAR


def _row(i: int, lat: float, lon: float) -> SimpleNamespace:
    r = SimpleNamespace(
        id=i,
        name_original="ATM SACOMBANK",
        address_normalized=None,
        address_components=None,
        phone_e164="+84190055588",
        website=None,
        website_domain=None,
        brand="Sacombank",
        openooh_category=None,
        openooh_subcategory=None,
    )
    r._lat, r._lon = lat, lon
    return r


class _Resolver:
    """Records what it was asked; always says the pair is different."""

    def __init__(self) -> None:
        self.asked: list[tuple[int, int]] = []

    async def resolve(self, a: dict, b: dict, **kw: object) -> object:
        self.asked.append((a["id"], b["id"]))
        return SimpleNamespace(same=False, confidence=0.95, reason="different branch", cached=False)


@pytest.fixture(autouse=True)
def _always_auto_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pair scores as a slam dunk — geometry is the only thing that differs."""
    monkeypatch.setattr(merge_mod, "decide", lambda _s: DedupeDecision.AUTO_MERGE)
    monkeypatch.setattr(
        merge_mod.PairSimilarityScorer, "score", lambda self, a, b: SimpleNamespace(composite=0.97)
    )


async def test_far_apart_pair_is_sent_to_the_resolver_not_merged() -> None:
    resolver = _Resolver()
    svc = MergeService(resolver=resolver, auto_merge_max_meters=50.0)

    components, usage = await svc._components_of([_row(1, *NEAR), _row(2, *FAR)])

    assert resolver.asked == [(1, 2)], "the gate must escalate, not merge silently"
    assert usage.calls == 1
    assert len(components) == 2, "resolver said different — they stay apart"


async def test_nearby_pair_still_auto_merges_without_asking() -> None:
    """The gate must not turn every merge into an API call."""
    resolver = _Resolver()
    svc = MergeService(resolver=resolver, auto_merge_max_meters=50.0)

    components, usage = await svc._components_of([_row(1, *NEAR), _row(2, *MID)])

    assert resolver.asked == [], "11 m apart — no reason to ask"
    assert usage.calls == 0
    assert len(components) == 1


async def test_gated_pair_stays_unmerged_when_the_resolver_is_unavailable() -> None:
    """With no resolver the pair must not fall back to merging."""
    svc = MergeService(resolver=None, auto_merge_max_meters=50.0)

    components, usage = await svc._components_of([_row(1, *NEAR), _row(2, *FAR)])

    assert usage.calls == 0
    assert len(components) == 2, "a missed merge is recoverable; a bad merge is not"


async def test_gate_is_inert_when_coordinates_are_missing() -> None:
    """Rows without _lat/_lon (hand-built, or a null geometry) merge as before."""
    a, b = _row(1, *NEAR), _row(2, *FAR)
    del b._lat, b._lon
    svc = MergeService(resolver=_Resolver(), auto_merge_max_meters=50.0)

    components, _ = await svc._components_of([a, b])

    assert len(components) == 1
