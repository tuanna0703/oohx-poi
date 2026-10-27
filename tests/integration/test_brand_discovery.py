"""Brand discovery end to end: find a chain among untagged stores, review it
over the API (as bizmap's admin does), add it, then undo it."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from poi_lake.db import get_sessionmaker
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client, _seed_master

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _forget(name: str, key: str) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("DELETE FROM brand_candidates WHERE key = :k"), {"k": key})
        await s.execute(text("DELETE FROM brands WHERE name = :n"), {"n": name})
        await s.execute(text("DELETE FROM crawl_plan WHERE openooh_code = :c"), {"c": f"brand:{name}"})
        await s.commit()


async def test_a_chain_is_found_reviewed_added_and_undone() -> None:
    from poi_lake.services.brand_discovery import run

    tag = "bd-" + uuid.uuid4().hex[:6]
    brand = "Zentrix" + uuid.uuid4().hex[:4]
    key = brand.lower()
    sm = get_sessionmaker()
    async with sm() as s:
        for i in range(10):
            mid = await _seed_master(s, name=f"Siêu thị Mẹ Bé {brand} {i}", lat=10.8 + i / 100,
                                     lng=106.7, test_tag=tag, category="retail")
            await s.execute(
                text("UPDATE master_pois SET province_code = :p, openooh_subcategory = 'retail.mother_baby' "
                     "WHERE id = :i"),
                {"p": ("79", "01", "48")[i % 3], "i": mid},
            )
        await s.commit()
        token, _ = await _make_client(s, tag, permissions=["write:brands"])
    try:
        async with sm() as s:
            await run(s, use_ai=False)
        async with await _http() as c:
            h = {"X-API-Key": token}
            items = (await c.get("/api/v1/brand-discovery/candidates?status=pending", headers=h)).json()["items"]
            cand = next(i for i in items if i["key"] == key)
            assert (cand["stores"], cand["provinces"]) == (10, 3)
            assert cand["score"] < 80, "no AI verdict: never enough to add on its own"
            assert all(sm_["ok"] for sm_ in cand["samples"])

            r = await c.post(f"/api/v1/brand-discovery/candidates/{cand['id']}/approve",
                             json={"search_term": brand}, headers=h)
            assert r.status_code == 200, r.text
            assert r.json()["tagged"] == 10

            async with sm() as s:
                tagged = (await s.execute(
                    text("SELECT count(*) FROM master_pois WHERE brand = :b"), {"b": brand}
                )).scalar_one()
            assert tagged == 10

            r = await c.post(f"/api/v1/brand-discovery/candidates/{cand['id']}/remove", headers=h)
            assert r.json()["untagged"] == 10
            async with sm() as s:
                left = (await s.execute(
                    text("SELECT count(*) FROM master_pois WHERE brand = :b"), {"b": brand}
                )).scalar_one()
                in_dict = (await s.execute(text("SELECT count(*) FROM brands WHERE name = :b"), {"b": brand})).scalar_one()
            assert (left, in_dict) == (0, 0)
    finally:
        await _forget(brand, key)
        async with sm() as s:
            await _cleanup(s, tag)


async def test_a_new_spelling_of_a_known_brand_is_merged() -> None:
    """ "MB ATM" found in the data is MB Bank: approving it adds the spelling."""
    from poi_lake.services.brand_discovery import run

    tag = "bdm-" + uuid.uuid4().hex[:6]
    known = "Qbank" + uuid.uuid4().hex[:4]
    spelling = f"{known} Smartpoint"
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text("INSERT INTO brands (name, aliases, category, country, enabled) VALUES (:n, '{}', 'financial', 'VN', true)"),
            {"n": known},
        )
        for i in range(9):
            mid = await _seed_master(s, name=f"{spelling} {i}", lat=10.8, lng=106.7 + i / 100,
                                     test_tag=tag, category="financial")
            await s.execute(
                text("UPDATE master_pois SET province_code = :p, openooh_subcategory = 'financial.atms' WHERE id = :i"),
                {"p": ("79", "01", "48")[i % 3], "i": mid},
            )
        await s.commit()
        token, _ = await _make_client(s, tag, permissions=["write:brands"])
    key = spelling.lower()
    try:
        async with sm() as s:
            await run(s, use_ai=False)
        async with await _http() as c:
            h = {"X-API-Key": token}
            items = (await c.get("/api/v1/brand-discovery/candidates?status=pending", headers=h)).json()["items"]
            cand = next(i for i in items if i["key"] == key)
            r = await c.post(f"/api/v1/brand-discovery/candidates/{cand['id']}/approve",
                             json={"name": known}, headers=h)
            assert r.status_code == 200, r.text
            assert (r.json()["brand"], r.json()["tagged"], r.json()["sweep_rows"]) == (known, 9, 0)
        async with sm() as s:
            aliases = (await s.execute(text("SELECT aliases FROM brands WHERE name = :n"), {"n": known})).scalar_one()
        assert spelling in aliases
    finally:
        async with sm() as s:
            await s.execute(text("UPDATE master_pois SET brand = NULL WHERE brand = :b"), {"b": known})
            await s.commit()
        await _forget(known, key)
        async with sm() as s:
            await _cleanup(s, tag)


async def test_settings_never_return_a_key() -> None:
    tag = "bds-" + uuid.uuid4().hex[:6]
    sm = get_sessionmaker()
    async with sm() as s:
        token, _ = await _make_client(s, tag, permissions=["write:brands"])
        before = (await s.execute(text("SELECT value FROM app_settings WHERE key = 'brand_discovery'"))).first()
    try:
        async with await _http() as c:
            h = {"X-API-Key": token}
            r = await c.put("/api/v1/brand-discovery/providers/openai",
                            json={"key": "sk-test-secret-ABCD", "model": "m", "role": "primary"}, headers=h)
            assert r.status_code == 200
            body = r.text
            assert "sk-test-secret" not in body
            providers = r.json()["providers"]
            assert providers["openai"]["key_last4"] == "ABCD" and providers["openai"]["role"] == "primary"
            assert providers["claude"]["role"] == "fallback", "one primary at a time"
            r = await c.put("/api/v1/brand-discovery/settings", json={"add_at": 40, "reject_below": 60}, headers=h)
            assert r.status_code == 422
            no_perm, _ = await _make_client_plain(tag)
            assert (await c.get("/api/v1/brand-discovery/settings", headers={"X-API-Key": no_perm})).status_code == 403
    finally:
        async with sm() as s:
            if before:
                await s.execute(text("UPDATE app_settings SET value = CAST(:v AS jsonb) WHERE key = 'brand_discovery'"),
                                {"v": json.dumps(before[0])})
            else:
                await s.execute(text("DELETE FROM app_settings WHERE key = 'brand_discovery'"))
            await s.commit()
            await _cleanup(s, tag)


async def _make_client_plain(tag: str) -> tuple[str, int]:
    async with get_sessionmaker()() as s:
        return await _make_client(s, tag, permissions=["read:master"])
