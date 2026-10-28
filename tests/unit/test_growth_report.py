"""Growth per brand: snapshots where reports exist, entry dates before."""

from __future__ import annotations

from datetime import date, timedelta

from poi_lake.services.reports import brand_growth

TODAY = date(2026, 10, 28)


def d(n: int) -> date:
    return TODAY - timedelta(days=n)


def test_snapshots_count_a_store_the_day_it_got_the_brand() -> None:
    # Reports from 3 days back; a sweep tagged 50 old stores on day -1.
    snaps = {d(3): {"A": 100}, d(2): {"A": 110}, d(1): {"A": 160}}
    g = brand_growth({"A": 170}, snaps, {"A": {d(0): 2}}, {"A": 95}, TODAY, first=d(1), series_days=4)
    a = g["A"]
    assert a["start"] == 110, "the snapshot of the day before the period"
    assert a["new"] == 60
    assert a["series"] == [0, 10, 50, 10], "day -3 has no earlier snapshot: entry dates (none)"


def test_before_reports_began_the_entry_date_counts() -> None:
    created = {"A": {d(5): 3, d(4): 7}}
    g = brand_growth({"A": 20}, {}, created, {"A": 10}, TODAY, first=d(6), series_days=7)
    assert g["A"]["start"] == 10
    assert g["A"]["new"] == 10
    assert g["A"]["series"] == [0, 3, 7, 0, 0, 0, 0]


def test_a_brand_missing_from_the_start_snapshot_starts_at_zero() -> None:
    g = brand_growth({"New": 12}, {d(1): {"A": 5}}, {}, {}, TODAY, first=d(0), series_days=1)
    assert g["New"] == {"total": 12, "start": 0, "new": 12, "series": [12]}
