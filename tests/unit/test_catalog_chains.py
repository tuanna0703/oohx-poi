"""GET /catalog/chains: one entry per chain with a catalog, once it has listings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from poi_lake.catalog.chains import CHAINS
from poi_lake.services.catalog import chain_entry, region_note

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=UTC)


def _counts(**kw: object) -> dict:
    d = {"products": 5285, "priced_products": 1954, "stores": 4, "last_success_at": NOW - timedelta(hours=3)}
    d.update(kw)
    return d


def test_every_catalog_chain_names_its_site() -> None:
    for c in CHAINS.values():
        assert bool(c.site) == c.products, c.code
    assert [c.code for c in CHAINS.values() if c.per_store] == ["winmart"]


def test_an_entry() -> None:
    e = chain_entry(CHAINS["nguyenkim"], _counts(), enabled=True, now=NOW)
    assert e is not None
    assert (e["code"], e["label"], e["site"], e["brand"], e["per_store"]) == (
        "nguyenkim", "Nguyễn Kim", "nguyenkim.com", "Nguyen Kim", False,
    )
    assert (e["products"], e["priced_products"], e["stores"], e["status"]) == (5285, 1954, 4, "ok")
    assert e["region_note"] is None


def test_status_and_who_is_left_out() -> None:
    assert chain_entry(CHAINS["pico"], _counts(), enabled=False, now=NOW)["status"] == "disabled"
    stale = _counts(last_success_at=NOW - timedelta(days=4))
    assert chain_entry(CHAINS["pico"], stale, enabled=True, now=NOW)["status"] == "failing"
    assert chain_entry(CHAINS["pico"], None, enabled=True, now=NOW) is None, "no first run yet"
    assert chain_entry(CHAINS["pico"], _counts(products=0), enabled=True, now=NOW) is None
    assert chain_entry(CHAINS["bachhoaxanh"], _counts(), enabled=True, now=NOW) is None, "store list only"


def test_winmart_says_where_its_prices_come_from() -> None:
    e = chain_entry(CHAINS["winmart"], _counts(), enabled=True, now=NOW, region=region_note("HCM"))
    assert e is not None and e["per_store"] and e["region_note"] == "TP.HCM"
    assert e["brands"] == ["WinMart+", "WinMart"]
    assert region_note("HCM,HNI") == "TP.HCM, Hà Nội"
    assert region_note("*") is None
