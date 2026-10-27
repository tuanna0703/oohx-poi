"""Which codes the daily rerank pushes to the back of the crawl queue."""

from __future__ import annotations

from poi_lake.services.crawl_priority import penalized


def test_only_codes_with_enough_jobs_are_judged() -> None:
    yields = {
        "hospitality.cafes": (2366, 9.82),
        "retail.gas_stations": (8, 0.1),        # too few jobs to tell
        "hospitality.bars": (400, 0.4),
        "retail.grocery": (200, 1.0),           # exactly the floor pays
    }
    assert penalized(yields, 1.0, 200) == ["hospitality.bars"]


def test_zero_floor_turns_it_off() -> None:
    assert penalized({"hospitality.bars": (400, 0.0)}, 0, 200) == []
