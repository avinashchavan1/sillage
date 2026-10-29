"""Fragrantica provider: find a perfume page through a web search engine, fetch it, parse it.

Fragrantica has no public API and sits behind Cloudflare, so:
  * the page URL is discovered via DuckDuckGo / Bing (`site:fragrantica.com/perfume ...`),
  * pages are fetched with `curl_cffi` (browser TLS fingerprint) when installed, else httpx,
  * parsing leans on the meta description first (very stable wording), then the HTML pyramid
    and accord bars, so a layout change degrades the result instead of breaking it.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from ..models import Accord, Alternative, FragranceDetails
from ..text import fold, infer_concentration, match_score, split_list

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
PERFUME_URL = re.compile(
    r"https?://(?:www\.)?fragrantica\.[a-z.]+/perfume/([^/\s\"'<>?#]+)/([^/\s\"'<>?#]+?)-(\d+)\.html", re.I
)
TIMEOUT = 20


class FragranticaError(Exception):
    pass


class BlockedError(FragranticaError):
    """Cloudflare (or a search engine) refused us."""


# ---------------------------------------------------------------- HTTP


def _get(url: str, params: Optional[dict] = None, data: Optional[dict] = None) -> str:
    try:
        from curl_cffi import requests as creq  # type: ignore

        method = "POST" if data else "GET"
        resp = creq.request(method, url, params=params, data=data, headers=HEADERS, impersonate="chrome", timeout=TIMEOUT)
        status, text = resp.status_code, resp.text
    except ImportError:
        import httpx

        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.post(url, data=data) if data else client.get(url, params=params)
            status, text = resp.status_code, resp.text
    except Exception as exc:  # network errors from either client
        raise FragranticaError(f"Could not reach {urlparse(url).netloc}: {exc}") from exc

    if status in (403, 429, 503) or "cf-challenge" in text or "<title>Just a moment" in text:
        raise BlockedError(f"{urlparse(url).netloc} blocked the request (HTTP {status})")
    if status >= 400:
        raise FragranticaError(f"{urlparse(url).netloc} answered HTTP {status}")
    return text


# ---------------------------------------------------------------- URL discovery


def canonical_url(url: str) -> Optional[str]:
    m = PERFUME_URL.search(url)
    if not m:
        return None
    return f"https://www.fragrantica.com/perfume/{m.group(1)}/{m.group(2)}-{m.group(3)}.html"


def label_from_url(url: str) -> str:
    m = PERFUME_URL.search(url)
    if not m:
        return url
    brand = unquote(m.group(1)).replace("-", " ")
    name = unquote(m.group(2)).replace("-", " ")
    return f"{brand} – {name}"


def extract_perfume_urls(html: str) -> list[str]:
    """Pull Fragrantica perfume URLs out of a search-results page (handles redirect wrappers)."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "uddg=" in href:  # DuckDuckGo redirect
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        url = canonical_url(unquote(href))
        if url and url not in found:
            found.append(url)
    # Bing sometimes only exposes the target in <cite> text
    for url in (canonical_url(m.group(0)) for m in PERFUME_URL.finditer(html)):
        if url and url not in found:
            found.append(url)
    return found


def search(query: str) -> list[str]:
    """Return candidate Fragrantica perfume URLs for a free-text query, best match first."""
    q = f"site:fragrantica.com/perfume {query}"
    errors = []
    urls: list[str] = []
    engines = (
        ("https://html.duckduckgo.com/html/", None, {"q": q}),
        ("https://www.bing.com/search", {"q": q, "setlang": "en"}, None),
    )
    for url, params, data in engines:
        try:
            urls = extract_perfume_urls(_get(url, params=params, data=data))
        except FragranticaError as exc:
            errors.append(str(exc))
            continue
        if urls:
            break
    if not urls:
        detail = f" ({'; '.join(errors)})" if errors else ""
        raise FragranticaError(f"No Fragrantica page found for “{query}”{detail}")
    # Re-rank by how well the URL slug matches what the user typed; keep engine order as tiebreak.
    ranked = sorted(enumerate(urls), key=lambda iu: (-match_score(query, label_from_url(iu[1])), iu[0]))
    return [u for _, u in ranked]


# ---------------------------------------------------------------- parsing

_DESC = re.compile(
    r"^(?P<name>.+?) by (?P<brand>.+?) is an? (?P<family>.+?) fragrance for (?P<gender>women and men|women|men)\b",
    re.I | re.S,
)
_LAUNCH = re.compile(r"was launched in (\d{4})", re.I)
_NOSE = re.compile(r"The noses? behind this fragrance (?:is|are) (.+?)\.(?:\s|$)", re.I)
_PYRAMID = {
    "top": re.compile(r"Top notes? (?:is|are) (.+?)(?:;|\.(?:\s|$))", re.I),
    "middle": re.compile(r"(?:middle|heart) notes? (?:is|are) (.+?)(?:;|\.(?:\s|$))", re.I),
    "base": re.compile(r"base notes? (?:is|are) (.+?)(?:;|\.(?:\s|$))", re.I),
}
_FLAT = re.compile(r"(?:main notes|fragrance notes|notes) (?:is|are|include) (.+?)\.(?:\s|$)", re.I)
_LAYER_HEADING = re.compile(r"^\s*(top|middle|heart|base)\s+notes?\s*$", re.I)
_WIDTH = re.compile(r"width\s*:\s*([\d.]+)%")
_HEADINGS = ("h2", "h3", "h4", "h5", "h6")


def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for n in names:
        tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _num(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", text.replace(" ", ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _count(text: Optional[str]) -> Optional[int]:
    """'31,204 votes' -> 31204 (thousands separators vary by locale)."""
    digits = re.sub(r"[^\d]", "", (text or "").split("votes")[0])
    return int(digits) if digits else None


def parse_description(desc: str) -> dict:
    out: dict = {}
    if m := _DESC.search(desc):
        out.update(name=m["name"].strip(), brand=m["brand"].strip(), family=m["family"].strip())
        g = m["gender"].lower()
        out["gender"] = {"women and men": "Unisex", "women": "Women", "men": "Men"}[g]
    if m := _LAUNCH.search(desc):
        out["year"] = int(m.group(1))
    if m := _NOSE.search(desc):
        out["perfumers"] = split_list(m.group(1))
    layered = False
    for layer, rx in _PYRAMID.items():
        if m := rx.search(desc):
            out[f"{layer}_notes"] = split_list(m.group(1))
            layered = True
    if not layered and (m := _FLAT.search(desc)):
        out["notes"] = split_list(m.group(1))
    return out


def _note_name(a) -> Optional[str]:
    text = a.get_text(" ", strip=True)
    if not text:
        img = a.find("img")
        text = (img.get("alt") or img.get("title") or "") if img else ""
    text = " ".join(text.split())
    return text or None


def parse_pyramid(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Walk the document in order; note links after a 'Top/Middle/Base Notes' heading belong to it."""
    container = soup.find(id="pyramid") or soup.find(attrs={"class": re.compile("pyramid")}) or soup
    layers: dict[str, list[str]] = {}
    current: Optional[str] = None
    flat: list[str] = []
    for el in container.find_all(True):
        is_heading = el.name in _HEADINGS
        is_label = el.name in ("b", "strong", "span", "div", "p") and el.find(True) is None
        if is_heading or is_label:
            text = el.get_text(" ", strip=True)
            if m := _LAYER_HEADING.match(text):
                key = m.group(1).lower()
                current = "middle" if key == "heart" else key
                layers.setdefault(current, [])
                continue
            if is_heading and text and current:
                current = None  # some other section started
        if el.name == "a" and "/notes/" in (el.get("href") or ""):
            name = _note_name(el)
            if not name:
                continue
            bucket = layers[current] if current else flat
            if name.lower() not in {n.lower() for n in bucket}:
                bucket.append(name)
    result = {f"{k}_notes": v for k, v in layers.items() if v}
    if not result and flat and container is not soup:
        result["notes"] = flat
    return result


def parse_accords(soup: BeautifulSoup) -> list[Accord]:
    accords = []
    for bar in soup.select(".accord-bar"):
        name = bar.get_text(" ", strip=True)
        if not name:
            continue
        width = _WIDTH.search(bar.get("style") or "")
        strength = float(width.group(1)) / 100 if width else 1.0
        accords.append(Accord(name=name.lower(), strength=strength))
    return accords


def _json_ld(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") in ("Product", "IndividualProduct"):
                return item
    return {}


def parse_perfume_page(html: str, url: Optional[str] = None) -> FragranceDetails:
    soup = BeautifulSoup(html, "lxml")
    desc = _meta(soup, "description", "og:description") or ""
    data = parse_description(desc)
    ld = _json_ld(soup)

    if "name" not in data:
        h1 = soup.find("h1")
        title = (h1.get_text(" ", strip=True) if h1 else "") or (soup.title.get_text(strip=True) if soup.title else "")
        title = re.split(r"\s+(?:for (?:women and men|women|men))\b|\s+-\s+", title)[0].strip()
        data["name"] = ld.get("name") or title
    if not data.get("brand"):
        brand = soup.select_one('[itemprop="brand"] [itemprop="name"]') or soup.select_one('[itemprop="brand"]')
        ld_brand = ld.get("brand")
        if isinstance(ld_brand, dict):
            ld_brand = ld_brand.get("name")
        data["brand"] = (brand.get_text(" ", strip=True) if brand else None) or ld_brand or ""
        # "Sauvage Dior" -> "Sauvage" when the brand is glued to the title
        if data["brand"] and fold(data["name"]).endswith(" " + fold(data["brand"])):
            data["name"] = data["name"][: -len(data["brand"]) - 1].strip()

    pyramid = parse_pyramid(soup)
    if pyramid:  # HTML pyramid is more complete than the description, which truncates long lists
        for key in ("top_notes", "middle_notes", "base_notes", "notes"):
            data.pop(key, None)
        data.update(pyramid)

    rating = soup.select_one('[itemprop="ratingValue"]')
    votes = soup.select_one('[itemprop="ratingCount"]')
    agg = ld.get("aggregateRating") or {}
    data["community_rating"] = _num(rating.get_text() if rating else None) or _num(str(agg.get("ratingValue") or ""))
    data["rating_votes"] = _count(votes.get_text() if votes else None) or _count(str(agg.get("ratingCount") or ""))

    img = _meta(soup, "og:image")
    if not img and (tag := soup.select_one('img[itemprop="image"]')):
        img = tag.get("src")
    data["image_url"] = img or (ld.get("image") if isinstance(ld.get("image"), str) else None)

    data["accords"] = parse_accords(soup)
    data["description"] = desc or None
    data["concentration"] = infer_concentration(data["name"])
    data["source_url"] = canonical_url(url) if url else _meta(soup, "og:url")
    data["source"] = "fragrantica"

    details = FragranceDetails.model_validate(data)
    if not details.all_notes() and not details.accords:
        raise FragranticaError("Fetched the page but found no notes or accords (layout changed or page blocked)")
    return details


def fetch(url: str) -> FragranceDetails:
    canon = canonical_url(url)
    if not canon:
        raise FragranticaError("Not a Fragrantica perfume URL")
    return parse_perfume_page(_get(canon), canon)


def lookup(query: str) -> tuple[FragranceDetails, list[Alternative]]:
    """Search + fetch. Tries the top few candidates in case one page fails to fetch."""
    if canonical_url(query):
        return fetch(query), []
    urls = search(query)
    last_error: Optional[Exception] = None
    for i, url in enumerate(urls[:3]):
        try:
            details = fetch(url)
        except BlockedError:
            raise
        except FragranticaError as exc:
            last_error = exc
            continue
        alts = [Alternative(label=label_from_url(u), url=u) for j, u in enumerate(urls[:8]) if j != i]
        return details, alts
    raise last_error or FragranticaError("No usable Fragrantica page")
