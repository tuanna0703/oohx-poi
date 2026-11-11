"""Brand logos: found, checked, resized and kept as files on the VPS.

One logo per brand name, keyed by the name's slug, so a store brand on the
map and a product brand of the same name share it. Where a logo comes from,
most trusted first:

1. **manual** — an admin gave an image URL (``PUT /brands/{name}/logo``).
   Never replaced by the crawler.
2. **wikidata** — the item's "logo image" (P154) on Wikimedia Commons,
   rendered by Commons as a 256 px PNG, with its licence. An item counts only
   when its official website (P856) is the brand's, or, for a brand whose
   website we do not know, when it is the one item named exactly so that has
   a logo and a website.
3. **website** — what the brand's own home page declares: a schema.org
   ``logo``, then its ``apple-touch-icon``, then its largest ``icon``.

Retailers' pages (brand filters on Con Cưng, MediaMart…) are never used.

Every image is opened with Pillow, refused if smaller than ``MIN_SIDE`` or
not a raster image (SVG is never stored: it can carry script), shrunk to fit
``SIDE`` and saved as PNG under ``LOGOS_DIR`` as ``<slug>-<sha10>.png``. An
image already used by ``MAX_SHARED`` other brands is a platform's default
icon, not a logo, and is refused.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.catalog.brand_owners import owner_of, slugify

logger = logging.getLogger(__name__)

USER_AGENT = "poi-lake/0.1 (bizmap.live; brand logos)"
SIDE = 256  # stored logos fit in SIDE x SIDE
MIN_SIDE = 64  # smaller is a favicon, not a logo
MAX_BYTES = 3_000_000
MAX_SHARED = 2  # brands one image may serve before it counts as a default icon
WIKIDATA = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
RASTER = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/x-icon",
          "image/vnd.microsoft.icon", "image/ico")


class LogoError(Exception):
    """An image that is no logo we keep (too small, not raster, unreadable)."""


@dataclass(frozen=True, slots=True)
class Found:
    """An image found for a brand, before it is fetched."""

    url: str
    source: str  # wikidata | website | manual
    license: str | None = None


@dataclass(frozen=True, slots=True)
class Logo:
    png: bytes
    width: int
    height: int
    sha: str


def domain_of(url_or_host: str | None) -> str | None:
    """"https://www.Highlandscoffee.com.vn/vi" → "highlandscoffee.com.vn"."""
    s = (url_or_host or "").strip().lower()
    if not s:
        return None
    host = urlparse(s if "://" in s else f"http://{s}").hostname or ""
    host = host.removeprefix("www.")
    return host or None


# ---------------------------------------------------------------- images

def normalize(data: bytes) -> Logo:
    """Open, check and shrink an image to a PNG that fits SIDE x SIDE."""
    from PIL import Image, UnidentifiedImageError

    head = data[:512].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml", b"<!doctype", b"<html")):
        raise LogoError("not a raster image")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()  # an .ico opens at the largest size it holds; a GIF at its first frame
            img = im.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise LogoError(f"unreadable image: {exc}") from exc
    w, h = img.size
    if min(w, h) < MIN_SIDE:
        raise LogoError(f"too small: {w}x{h}")
    img.thumbnail((SIDE, SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png = buf.getvalue()
    return Logo(png=png, width=img.size[0], height=img.size[1], sha=hashlib.sha256(png).hexdigest())


def write_file(logos_dir: str, slug: str, logo: Logo) -> str:
    """Write the PNG as ``<slug>-<sha10>.png`` (atomically) and return its name."""
    d = Path(logos_dir)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{slug}-{logo.sha[:10]}.png"
    tmp = d / f".{name}.tmp"
    tmp.write_bytes(logo.png)
    tmp.replace(d / name)
    return name


def remove_file(logos_dir: str, name: str | None) -> None:
    if name and "/" not in name and not name.startswith("."):
        (Path(logos_dir) / name).unlink(missing_ok=True)


# ------------------------------------------------------------- web pages

_LINK = re.compile(r"<link\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')
_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def _attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): unescape(m.group(3) or m.group(4) or m.group(5) or "")
            for m in _ATTR.finditer(tag)}


def _ld_logos(node: Any) -> list[str]:
    """``logo`` values of schema.org nodes, in document order."""
    out: list[str] = []
    if isinstance(node, list):
        for n in node:
            out += _ld_logos(n)
    elif isinstance(node, dict):
        logo = node.get("logo")
        if isinstance(logo, str):
            out.append(logo)
        elif isinstance(logo, dict) and isinstance(logo.get("url"), str):
            out.append(logo["url"])
        for k in ("@graph", "publisher", "brand", "author", "organization"):
            if k in node:
                out += _ld_logos(node[k])
    return out


def _size(sizes: str) -> int:
    best = 0
    for s in sizes.lower().split():
        a, _, b = s.partition("x")
        if a.isdigit() and b.isdigit():
            best = max(best, min(int(a), int(b)))
    return best


def website_candidates(html: str, base_url: str) -> list[str]:
    """Image URLs a home page offers as its logo, best first: schema.org
    ``logo``, ``apple-touch-icon`` (largest first), ``icon`` (largest first).
    SVG files are left out."""
    ld: list[str] = []
    for raw in _LD.findall(html):
        try:
            ld += _ld_logos(json.loads(unescape(raw.strip())))
        except ValueError:
            continue
    touch: list[tuple[int, str]] = []
    icons: list[tuple[int, str]] = []
    for tag in _LINK.findall(html):
        a = _attrs(tag)
        rel = a.get("rel", "").lower().split()
        href = a.get("href", "").strip()
        if not href:
            continue
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            touch.append((_size(a.get("sizes", "")) or 180, href))
        elif "icon" in rel and "mask-icon" not in rel:
            icons.append((_size(a.get("sizes", "")), href))
    ordered = ld + [h for _, h in sorted(touch, key=lambda t: -t[0])] + \
        [h for _, h in sorted(icons, key=lambda t: -t[0])]
    out: dict[str, None] = {}
    for href in ordered:
        url = urljoin(base_url, href.strip())
        if urlparse(url).scheme in ("http", "https") and not urlparse(url).path.lower().endswith(".svg"):
            out.setdefault(url, None)
    return list(out)


# ---------------------------------------------------------------- wikidata

def _claim_values(entity: dict[str, Any], prop: str) -> list[str]:
    out = []
    for c in (entity.get("claims") or {}).get(prop) or []:
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(v, str):
            out.append(v)
    return out


def _names(entity: dict[str, Any]) -> set[str]:
    out = {v.get("value", "") for v in (entity.get("labels") or {}).values()}
    for vs in (entity.get("aliases") or {}).values():
        out |= {v.get("value", "") for v in vs}
    return {n.strip().casefold() for n in out if n}


def pick_wikidata(entities: list[dict[str, Any]], name: str, website: str | None) -> str | None:
    """The Commons file of the item that is this brand, or None.

    With a website: the first item with a logo whose official website is on
    the same domain. Without: the single item named exactly so (label or
    alias) that has both a logo and an official website — two or more such
    items, or none, and it is not decided here."""
    want = domain_of(website)
    with_logo = [e for e in entities if _claim_values(e, "P154")]
    if want:
        for e in with_logo:
            sites = {domain_of(u) for u in _claim_values(e, "P856")}
            if want in sites or any(s and (want.endswith("." + s) or s.endswith("." + want)) for s in sites):
                return _claim_values(e, "P154")[0]
        return None
    key = name.strip().casefold()
    exact = [e for e in with_logo if key in _names(e) and _claim_values(e, "P856")]
    return _claim_values(exact[0], "P154")[0] if len(exact) == 1 else None


class LogoFinder:
    """Finds and fetches a brand's logo; one request at a time, ``min_interval_s`` apart."""

    def __init__(self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10),
                                                   follow_redirects=True)
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> LogoFinder:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.requests += 1
        try:
            return await self._client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        finally:
            self._next_at = time.monotonic() + self._min_interval_s

    async def wikidata(self, name: str, website: str | None) -> Found | None:
        ids: list[str] = []
        for lang in ("vi", "en"):
            r = await self._get(WIKIDATA, {"action": "wbsearchentities", "search": name, "language": lang,
                                           "type": "item", "limit": 7, "format": "json"})
            if r.status_code == 200:
                ids += [s["id"] for s in r.json().get("search", []) if s.get("id") not in ids]
        if not ids:
            return None
        r = await self._get(WIKIDATA, {"action": "wbgetentities", "ids": "|".join(ids[:14]),
                                       "props": "claims|labels|aliases", "format": "json"})
        if r.status_code != 200:
            return None
        entities = list((r.json().get("entities") or {}).values())
        file = pick_wikidata(entities, name, website)
        if not file:
            return None
        r = await self._get(COMMONS, {"action": "query", "titles": f"File:{file}", "prop": "imageinfo",
                                      "iiprop": "url|extmetadata", "iiurlwidth": SIDE, "format": "json"})
        if r.status_code != 200:
            return None
        for page in (r.json().get("query") or {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            meta = info.get("extmetadata") or {}
            lic = (meta.get("LicenseShortName") or {}).get("value")
            if url:
                return Found(url=url, source="wikidata", license=lic)
        return None

    async def website(self, website: str | None) -> list[Found]:
        host = domain_of(website)
        if not host:
            return []
        base = f"https://{host}/"
        try:
            r = await self._get(base)
        except httpx.HTTPError:
            return []
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            return []
        urls = website_candidates(r.text[:600_000], str(r.url))
        urls.append(urljoin(str(r.url), "/apple-touch-icon.png"))
        return [Found(url=u, source="website") for u in dict.fromkeys(urls)]

    async def fetch(self, url: str) -> Logo:
        """Fetch an image and normalize it; LogoError when it is no logo we keep."""
        try:
            r = await self._get(url)
        except httpx.HTTPError as exc:
            raise LogoError(f"fetch failed: {exc}") from exc
        if r.status_code != 200:
            raise LogoError(f"HTTP {r.status_code}")
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype and ctype.startswith("image/") and ctype not in RASTER:
            raise LogoError(f"not a raster image: {ctype}")
        if len(r.content) > MAX_BYTES:
            raise LogoError("too large")
        return normalize(r.content)


def public_url(url: str) -> bool:
    """Whether an admin-given URL may be fetched by the server: http(s), a
    dotted public host name or a public IP, never localhost or the private
    network (no reaching the database or the docker services by URL)."""
    import ipaddress

    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # "postgres", "api": docker service names
    return ip.is_global


# ---------------------------------------------------------------- storage

@dataclass(frozen=True, slots=True)
class Brand:
    slug: str
    name: str
    website: str | None
    weight: int  # stores on the map + products in catalogs


_NOT_OWN_SITE = (
    r"(^|\.)(facebook|fb|google|goo|g|zalo|instagram|tiktok|youtube|youtu|shopee|lazada|tiki|"
    r"sendo|linktr|bio|bit|tinyurl|m|business|wa|twitter|x|foody|grab|be|baemin|now|"
    r"booking|agoda|traveloka|tripadvisor|maps|sites)\.[a-z.]+$"
)


async def candidates(session: AsyncSession, *, limit: int, retry_days: int) -> list[Brand]:
    """Brands to look for, most stores and products first: store brands on
    the map (with the domain most of their stores link to) and product
    brands in the catalogs, merged by slug. Left out: manual logos, logos
    found less than 90 days ago, and searches that found nothing less than
    ``retry_days`` ago."""
    stores = (
        await session.execute(
            text(
                """
                WITH d AS (
                    SELECT brand, NULLIF(regexp_replace(lower(canonical_website),
                           '^(https?://)?(www\\.)?([^/:?#]+).*$', '\\3'), '') AS dom
                    FROM master_pois WHERE status = 'active' AND brand IS NOT NULL
                )
                SELECT brand, count(*) AS n,
                       mode() WITHIN GROUP (ORDER BY dom) FILTER (WHERE dom IS NOT NULL AND dom !~ :social) AS dom
                FROM d GROUP BY brand
                """
            ),
            {"social": _NOT_OWN_SITE},
        )
    ).all()
    products = (
        await session.execute(
            text("SELECT brand, count(DISTINCT (chain, sku)) AS n FROM chain_products "
                 "WHERE brand IS NOT NULL GROUP BY brand")
        )
    ).all()
    merged: dict[str, Brand] = {}

    def add(slug: str, name: str, website: str | None, n: int) -> None:
        if not slug:
            return
        old = merged.get(slug)
        if old is None:
            merged[slug] = Brand(slug, name, website, n)
        else:
            merged[slug] = Brand(slug, old.name, old.website or website, old.weight + n)

    for r in stores:
        add(slugify(r.brand), r.brand, r.dom, int(r.n))
    for r in products:
        o = owner_of(r.brand)
        if o is not None:
            add(o.slug, o.name, None, int(r.n))
    skip = set(
        (
            await session.execute(
                text(
                    """
                    SELECT slug FROM brand_logos
                    WHERE manual
                       OR (status = 'ok' AND checked_at > now() - interval '90 days')
                       OR (status = 'missing' AND checked_at > now() - make_interval(days => :d))
                    """
                ),
                {"d": retry_days},
            )
        ).scalars()
    )
    todo = [b for b in merged.values() if b.slug not in skip]
    todo.sort(key=lambda b: (-b.weight, b.slug))
    return todo[:limit]


async def shared_count(session: AsyncSession, sha: str, slug: str) -> int:
    """How many other brands already use this exact image."""
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM brand_logos WHERE sha = :h AND slug <> :s AND status = 'ok'"),
                {"h": sha, "s": slug},
            )
        ).scalar_one()
    )


async def current_file(session: AsyncSession, slug: str) -> str | None:
    return (
        await session.execute(text("SELECT file FROM brand_logos WHERE slug = :s"), {"s": slug})
    ).scalar_one_or_none()


async def save_found(session: AsyncSession, brand_slug: str, name: str, file: str, found: Found,
                     logo: Logo, *, manual: bool = False) -> None:
    await session.execute(
        text(
            """
            INSERT INTO brand_logos (slug, name, status, file, source, source_url, license,
                width, height, sha, manual, checked_at, updated_at)
            VALUES (:slug, :name, 'ok', :file, :source, :url, :lic, :w, :h, :sha, :manual, now(), now())
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name, status = 'ok', file = EXCLUDED.file, source = EXCLUDED.source,
                source_url = EXCLUDED.source_url, license = EXCLUDED.license, width = EXCLUDED.width,
                height = EXCLUDED.height, sha = EXCLUDED.sha, manual = EXCLUDED.manual,
                checked_at = now(), updated_at = now()
            """
        ),
        {"slug": brand_slug, "name": name, "file": file, "source": found.source, "url": found.url,
         "lic": found.license, "w": logo.width, "h": logo.height, "sha": logo.sha, "manual": manual},
    )


async def save_missing(session: AsyncSession, brand_slug: str, name: str) -> None:
    """Nothing found: note when, so the search waits ``retry_days``. A logo
    found before stays (a site that is down this week loses nothing)."""
    await session.execute(
        text(
            """
            INSERT INTO brand_logos (slug, name, status, checked_at, updated_at)
            VALUES (:slug, :name, 'missing', now(), now())
            ON CONFLICT (slug) DO UPDATE SET checked_at = now()
            """
        ),
        {"slug": brand_slug, "name": name},
    )


async def logo_paths(session: AsyncSession, names: list[str]) -> dict[str, str]:
    """``{slug: "/logos/<file>"}`` for the brand names (or slugs) that have a logo."""
    slugs = sorted({slugify(n) for n in names if n})
    if not slugs:
        return {}
    rows = await session.execute(
        text("SELECT slug, file FROM brand_logos WHERE slug = ANY(:s) AND status = 'ok' AND file IS NOT NULL"),
        {"s": slugs},
    )
    return {r.slug: f"/logos/{r.file}" for r in rows}


async def find_one(session: AsyncSession, finder: LogoFinder, brand: Brand, logos_dir: str) -> str:
    """Look for one brand's logo and record what was found: 'wikidata',
    'website' or 'missing'. Caller commits."""
    tries: list[Found] = []
    try:
        wd = await finder.wikidata(brand.name, brand.website)
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("logo %s: wikidata %s", brand.slug, exc)
        wd = None
    if wd:
        tries.append(wd)
    try:
        tries += await finder.website(brand.website)
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("logo %s: website %s", brand.slug, exc)
    for found in tries:
        try:
            logo = await finder.fetch(found.url)
        except LogoError as exc:
            logger.debug("logo %s: %s %s", brand.slug, found.url, exc)
            continue
        if await shared_count(session, logo.sha, brand.slug) >= MAX_SHARED:
            continue  # a platform's default icon
        old = await current_file(session, brand.slug)
        file = write_file(logos_dir, brand.slug, logo)
        await save_found(session, brand.slug, brand.name, file, found, logo)
        if old and old != file:
            remove_file(logos_dir, old)
        return found.source
    await save_missing(session, brand.slug, brand.name)
    return "missing"
