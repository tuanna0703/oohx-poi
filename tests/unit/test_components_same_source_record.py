"""Two copies of one source record are one place, whatever the scorer says.

A re-crawl brings the same Google place back as a new raw row. Before 2026-10
those copies were scored like strangers, and 34k place_ids ended up split
across masters on production.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poi_lake.pipeline.dedupe import merge as merge_mod
from poi_lake.pipeline.dedupe.decision import DedupeDecision
from poi_lake.pipeline.dedupe.merge import MergeService


def _row(i: int, source_id: int | None, spid: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=i,
        name_original=f"POI {i}",
        _source_id=source_id,
        _source_poi_id=spid,
    )


@pytest.fixture(autouse=True)
def _scorer_says_different(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_mod, "decide", lambda _s: DedupeDecision.DISTINCT)
    monkeypatch.setattr(
        merge_mod.PairSimilarityScorer,
        "score",
        lambda self, a, b: SimpleNamespace(composite=0.1),
    )


async def test_same_source_record_joins_without_scoring() -> None:
    rows = [_row(1, 7, "ChIJ-a"), _row(2, 7, "ChIJ-a"), _row(3, 7, "ChIJ-b")]
    components, usage = await MergeService(resolver=None)._components_of(rows)

    ids = sorted(sorted(r.id for r in c) for c in components)
    assert ids == [[1, 2], [3]]
    assert usage.calls == 0


async def test_same_id_from_different_sources_is_not_a_match() -> None:
    rows = [_row(1, 7, "123"), _row(2, 8, "123")]
    components, _ = await MergeService(resolver=None)._components_of(rows)
    assert len(components) == 2


async def test_rows_without_source_attrs_are_left_to_the_scorer() -> None:
    rows = [_row(1, None, None), _row(2, None, None)]
    components, _ = await MergeService(resolver=None)._components_of(rows)
    assert len(components) == 2
