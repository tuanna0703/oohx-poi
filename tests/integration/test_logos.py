"""Brand logos end to end: an admin's logo over the API, the file served at
/logos, logo_url on the catalog's brand pages, the crawler's fallbacks and its
default-icon guard."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from poi_lake.config import get_settings
from poi_lake.db import get_sessionmaker
from poi_lake.services import logos
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _png(w: int, h: int, color: tuple[int, int, int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeFinder:
    """What find_one asks of a LogoFinder, scripted."""

    def __init__(self, wikidata: logos.Found | None, website: list[logos.Found], images: dict[str, bytes]):
        self._wd, self._site, self._images = wikidata, website, images

    async def wikidata(self, name: str, website: str | None) -> logos.Found | None:
        return self._wd

    async def website(self, website: str | None) -> list[logos.Found]:
        return self._site

    async def fetch(self, url: str) -> logos.Logo:
        if url not in self._images:
            raise logos.LogoError("HTTP 404")
        return logos.normalize(self._images[url])


async def _forget(slugs: list[str], chain: str | None = None) -> None:
    d = get_settings().logos_dir
    async with get_sessionmaker()() as s:
        files = (await s.execute(text("SELECT file FROM brand_logos WHERE slug = ANY(:s)"), {"s": slugs})).scalars()
        for f in files:
            logos.remove_file(d, f)
        await s.execute(text("DELETE FROM brand_logos WHERE slug = ANY(:s)"), {"s": slugs})
        if chain:
            for t in ("chain_price_changes", "chain_store_products", "chain_products", "chain_stores"):
                await s.execute(text(f"DELETE FROM {t} WHERE chain = :c"), {"c": chain})
        await s.commit()


async def test_an_admins_logo_is_kept_served_and_shown_on_brand_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    tag = "lg-" + uuid.uuid4().hex[:6]
    name = f"Lumora {tag}"
    slug = logos.slugify(name)
    chain = f"test-{tag}"
    png = _png(400, 200, (10, 120, 200, 255))

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/logo.png":
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        return httpx.Response(200, text="<svg/>", headers={"content-type": "image/svg+xml"})

    real = logos.LogoFinder

    def fake_finder(**kw):  # type: ignore[no-untyped-def]
        return real(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), min_interval_s=0)

    monkeypatch.setattr(logos, "LogoFinder", fake_finder)
    sm = get_sessionmaker()
    async with sm() as s:
        writer, _ = await _make_client(s, tag, permissions=["write:brands", "read:master"])
        # One product listed at a virtual store, as a crawled chain has.
        from datetime import UTC, datetime

        from poi_lake.catalog.winmart import Item
        from poi_lake.services import catalog

        store = await catalog.ensure_online_store(s, chain, "Test")
        item = Item(sku="1", item_no="1", name="Sữa Lumora", brand=name, barcode=None, uom=None,
                    price=100000.0, sale_price=None, category_path=("Sữa", "Sữa bột"), menu="Sữa",
                    seo_name=None, image_url=None)
        await catalog.save_items(s, chain, store, [item], answered_menus=[], now=datetime.now(UTC))
    try:
        async with await _http() as c:
            h = {"X-API-Key": writer}
            bad = await c.put(f"/api/v1/brands/{name}/logo", json={"url": "https://img.example.com/x.svg"}, headers=h)
            assert bad.status_code == 422 and "raster" in bad.text
            local = await c.put(f"/api/v1/brands/{name}/logo", json={"url": "http://postgres:5432/x.png"}, headers=h)
            assert local.status_code == 422, "no fetching the private network"

            r = await c.put(f"/api/v1/brands/{name}/logo", json={"url": "https://img.example.com/logo.png"}, headers=h)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["manual"] and body["source"] == "manual" and (body["width"], body["height"]) == (256, 128)
            assert body["logo_url"].startswith(f"/logos/{slug}-")
            served = await c.get(body["logo_url"])
            assert served.status_code == 200 and served.content.startswith(b"\x89PNG")

            got = await c.get(f"/api/v1/brands/{name}/logo", headers=h)
            assert got.json()["logo_url"] == body["logo_url"]

            # The catalog's brand pages carry it (no cache: a fresh test chain).
            listed = (await c.get(f"/api/v1/catalog/{chain}/brands", headers=h)).json()["brands"]
            assert [b["logo_url"] for b in listed if b["slug"] == slug] == [body["logo_url"]]

            # A manual logo is never a crawl candidate.
            async with sm() as s:
                todo = await logos.candidates(s, limit=100_000, retry_days=30)
            assert slug not in {b.slug for b in todo}

            gone = await c.delete(f"/api/v1/brands/{name}/logo", headers=h)
            assert gone.json()["removed"] == 1
            file = Path(get_settings().logos_dir) / body["logo_url"].removeprefix("/logos/")
            assert not file.exists()
            assert (await c.get(f"/api/v1/brands/{name}/logo", headers=h)).status_code == 404
    finally:
        await _forget([slug], chain)
        async with sm() as s:
            await _cleanup(s, tag)


async def test_the_crawler_falls_back_and_refuses_a_shared_default_icon() -> None:
    tag = uuid.uuid4().hex[:6]
    brands = [logos.Brand(slug=f"brand-{tag}-{i}", name=f"Brand {tag} {i}", website="brand.vn", weight=1)
              for i in range(4)]
    d = get_settings().logos_dir
    own = _png(300, 300, (1, 2, 3, 255))
    default = _png(180, 180, (255, 255, 255, 255))  # what a site builder gives every shop
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            # Wikidata's image is gone (404); the home page's icon is used.
            f = _FakeFinder(logos.Found("https://commons/x.png", "wikidata", "CC0"),
                            [logos.Found("https://brand.vn/t.png", "website")],
                            {"https://brand.vn/t.png": own})
            assert await logos.find_one(s, f, brands[0], d) == "website"
            # Nothing usable: recorded as missing.
            nothing = _FakeFinder(None, [logos.Found("https://brand.vn/none.png", "website")], {})
            assert await logos.find_one(s, nothing, brands[1], d) == "missing"
            await s.commit()

            same = _FakeFinder(None, [logos.Found("https://x.vn/d.png", "website")], {"https://x.vn/d.png": default})
            for b in brands[1:3]:
                assert await logos.find_one(s, same, b, d) == "website"
            await s.commit()
            # A third brand with the very same image: a default icon, refused.
            assert await logos.find_one(s, same, brands[3], d) == "missing"
            await s.commit()

            rows = dict((await s.execute(
                text("SELECT slug, status FROM brand_logos WHERE slug = ANY(:s)"),
                {"s": [b.slug for b in brands]},
            )).all())
            assert rows == {brands[0].slug: "ok", brands[1].slug: "ok", brands[2].slug: "ok",
                            brands[3].slug: "missing"}
            paths = await logos.logo_paths(s, [b.name for b in brands])
            assert set(paths) == {b.slug for b in brands[:3]}
            assert all((Path(d) / p.removeprefix("/logos/")).exists() for p in paths.values())
    finally:
        await _forget([b.slug for b in brands])
