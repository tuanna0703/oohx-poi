"""Brand logos: image checks, home-page parsing, Wikidata matching, the finder."""

from __future__ import annotations

import io
import json

import httpx
import pytest

from poi_lake.services import logos
from poi_lake.services.logos import (
    LogoError,
    LogoFinder,
    domain_of,
    normalize,
    pick_wikidata,
    public_url,
    website_candidates,
    write_file,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _png(w: int, h: int, color: tuple[int, int, int, int] = (200, 20, 20, 255)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_normalize_shrinks_to_fit_and_refuses_small_or_vector_images() -> None:
    big = normalize(_png(1024, 512))
    assert (big.width, big.height) == (256, 128), "fits 256 x 256, keeps its shape"
    assert big.png.startswith(b"\x89PNG") and len(big.sha) == 64
    same = normalize(_png(120, 120))
    assert (same.width, same.height) == (120, 120), "never enlarged"
    with pytest.raises(LogoError, match="too small"):
        normalize(_png(32, 32))
    with pytest.raises(LogoError, match="raster"):
        normalize(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(LogoError, match="unreadable"):
        normalize(b"\x89PNG not really")


def test_write_file_is_named_by_slug_and_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    logo = normalize(_png(100, 100))
    name = write_file(str(tmp_path), "highlands-coffee", logo)
    assert name == f"highlands-coffee-{logo.sha[:10]}.png"
    assert (tmp_path / name).read_bytes() == logo.png
    assert not list(tmp_path.glob(".*.tmp")), "written atomically"


def test_domain_and_public_url() -> None:
    assert domain_of("https://www.HighlandsCoffee.com.vn/vi/") == "highlandscoffee.com.vn"
    assert domain_of("winmart.vn") == "winmart.vn"
    assert domain_of(None) is None
    assert public_url("https://upload.wikimedia.org/a.png")
    for bad in ("ftp://x.com/a.png", "http://localhost/a.png", "http://127.0.0.1/a.png",
                "http://10.0.0.5/a.png", "http://postgres:5432/", "http://api:8000/logos/x.png"):
        assert not public_url(bad), bad


def test_website_candidates_best_first_without_svg() -> None:
    html = """
    <html><head>
      <link rel="icon" href="/favicon-32.png" sizes="32x32">
      <link rel="icon" type="image/svg+xml" href="/logo.svg">
      <link rel='apple-touch-icon' sizes='152x152' href='/touch-152.png'>
      <link rel="apple-touch-icon" sizes="180x180" href="https://cdn.brand.vn/touch-180.png">
      <link rel="icon" href="/favicon-192.png" sizes="192x192">
      <script type="application/ld+json">
        {"@context": "https://schema.org", "@graph": [
          {"@type": "Organization", "name": "Brand", "logo": {"@type": "ImageObject", "url": "/img/logo.png"}}]}
      </script>
    </head></html>"""
    assert website_candidates(html, "https://brand.vn/") == [
        "https://brand.vn/img/logo.png",          # schema.org logo
        "https://cdn.brand.vn/touch-180.png",     # apple-touch-icon, largest first
        "https://brand.vn/touch-152.png",
        "https://brand.vn/favicon-192.png",       # icons, largest first
        "https://brand.vn/favicon-32.png",
    ], "the SVG is never offered"


def _entity(qid: str, label: str, logo: str | None, site: str | None, alias: str | None = None) -> dict:
    claims: dict = {}
    if logo:
        claims["P154"] = [{"mainsnak": {"datavalue": {"value": logo}}}]
    if site:
        claims["P856"] = [{"mainsnak": {"datavalue": {"value": site}}}]
    e = {"id": qid, "labels": {"en": {"value": label}}, "claims": claims}
    if alias:
        e["aliases"] = {"vi": [{"value": alias}]}
    return e


def test_pick_wikidata_needs_the_brands_website_or_one_exact_match() -> None:
    vinamilk = _entity("Q1", "Vinamilk", "Vinamilk logo.svg", "https://www.vinamilk.com.vn")
    other = _entity("Q2", "Vinamilk Arena", "Arena.png", "https://arena.vn")
    assert pick_wikidata([other, vinamilk], "Vinamilk", "vinamilk.com.vn") == "Vinamilk logo.svg"
    assert pick_wikidata([other], "Vinamilk", "vinamilk.com.vn") is None, "website must match"
    # No website known: the one item named exactly so, with a logo and a site.
    assert pick_wikidata([other, vinamilk], "vinamilk", None) == "Vinamilk logo.svg"
    twin = _entity("Q3", "Vinamilk", "Other.png", "https://other.vn")
    assert pick_wikidata([vinamilk, twin], "Vinamilk", None) is None, "two candidates: undecided"
    no_site = _entity("Q4", "Gerber", "Gerber.svg", None)
    assert pick_wikidata([no_site], "Gerber", None) is None, "an item without a website is not trusted"
    aliased = _entity("Q5", "Highlands Coffee Vietnam", "HC.svg", "https://highlandscoffee.com.vn",
                      alias="Highlands Coffee")
    assert pick_wikidata([aliased], "Highlands Coffee", None) == "HC.svg"


async def test_finder_reads_wikidata_then_commons_and_fetches_the_thumbnail() -> None:
    seen: list[str] = []
    png = _png(512, 512)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.url.host}:{req.url.params.get('action', '')}")
        assert "poi-lake" in req.headers["user-agent"]
        p = req.url.params
        if req.url.host == "www.wikidata.org" and p["action"] == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q1"}]})
        if req.url.host == "www.wikidata.org" and p["action"] == "wbgetentities":
            return httpx.Response(200, json={"entities": {"Q1": _entity(
                "Q1", "Vinamilk", "Vinamilk logo.svg", "https://vinamilk.com.vn")}})
        if req.url.host == "commons.wikimedia.org":
            assert p["titles"] == "File:Vinamilk logo.svg"
            return httpx.Response(200, json={"query": {"pages": {"1": {"imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/thumb/Vinamilk_logo.svg/256px.png",
                "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]}}}})
        if req.url.host == "upload.wikimedia.org":
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with LogoFinder(client=http, min_interval_s=0) as f:
        found = await f.wikidata("Vinamilk", "vinamilk.com.vn")
        assert found == logos.Found(url="https://upload.wikimedia.org/thumb/Vinamilk_logo.svg/256px.png",
                                    source="wikidata", license="Public domain")
        logo = await f.fetch(found.url)
        assert (logo.width, logo.height) == (256, 256)


async def test_finder_offers_the_home_pages_icons_and_refuses_svg_content() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/":
            return httpx.Response(200, text='<link rel="apple-touch-icon" href="/t.png">',
                                  headers={"content-type": "text/html; charset=utf-8"})
        if req.url.path == "/x.svg":
            return httpx.Response(200, text="<svg/>", headers={"content-type": "image/svg+xml"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with LogoFinder(client=http, min_interval_s=0) as f:
        found = await f.website("https://www.brand.vn/vi")
        assert [x.url for x in found] == ["https://brand.vn/t.png", "https://brand.vn/apple-touch-icon.png"]
        assert all(x.source == "website" for x in found)
        assert await f.website(None) == []
        with pytest.raises(LogoError, match="raster"):
            await f.fetch("https://brand.vn/x.svg")
        with pytest.raises(LogoError, match="HTTP 404"):
            await f.fetch("https://brand.vn/missing.png")


def test_ld_json_logo_as_plain_string() -> None:
    html = '<script type="application/ld+json">' + json.dumps(
        {"@type": "Organization", "logo": "https://brand.vn/logo.png"}) + "</script>"
    assert website_candidates(html, "https://brand.vn/") == ["https://brand.vn/logo.png"]
