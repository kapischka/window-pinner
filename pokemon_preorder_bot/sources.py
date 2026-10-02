"""Fast, browser-free product lookups for the live monitor.

Each shop platform gets the cheapest request that still answers "is it
orderable right now": Shopify's search-suggest and collection JSON,
WooCommerce's public Store API, and for everything else a plain HTML fetch
read via JSON-LD, microdata or product-card text. The parse_* functions are
pure (no network) so they can be unit tested against fixtures.
"""

from __future__ import annotations

import html as html_lib
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .fetcher import USER_AGENT
from .matcher import (
    STATUSES_ACTIONABLE,
    classify_status,
    extract_jsonld_product_blocks,
    find_blocked_phrase,
    find_card_container,
    first_offer,
    status_from_availability,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
SHOPIFY_SUGGEST_LIMIT = 10  # Shopify's hard maximum for search/suggest
WOOCOMMERCE_PAGE_SIZE = 50
_BLOCK_STATUS_CODES = {403, 429, 503}


@dataclass
class Listing:
    title: str
    url: str
    available: bool | None  # None: listed, but no clear stock signal
    price: str = ""
    context: str = ""  # vendor / product type, used to confirm "is Pokémon"


@dataclass
class Shop:
    name: str
    platform: str  # shopify | woocommerce | html | auto
    url: str
    queries: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)  # shopify only
    pages: list[str] = field(default_factory=list)  # html only, fixed URLs
    search_url: str = ""  # html only, template containing {q}
    language: str = "?"  # assumed when a title carries no language hint
    assume_pokemon: bool = False  # every listing here is Pokémon TCG
    interval: float | None = None
    enabled: bool = True


@dataclass
class StatusPatterns:
    preorder: list[str]
    in_stock: list[str]
    unavailable: list[str]


class PollError(Exception):
    def __init__(self, message: str, blocked: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.blocked = blocked
        self.retry_after = retry_after


_TRACKING_PARAM = re.compile(r"^(_.*|utm_.*|srsltid|shpxid|lang|ref|fbclid|gclid)$", re.IGNORECASE)


def canonical_url(url: str) -> str:
    """Drops tracking parameters (Shopify appends ?_pos=1&_sid=…), the
    fragment and a trailing slash so the same product always maps to the
    same key. Other parameters stay: some shops identify products by them
    (index.php?a=123)."""
    parts = urlsplit(url)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAM.match(k)))
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))


def _format_euro(amount: float) -> str:
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _price_from_text(value) -> str:
    try:
        return _format_euro(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return ""


# --- parsers ------------------------------------------------------------------


def parse_shopify_suggest(data: dict, base_url: str) -> list[Listing]:
    products = data.get("resources", {}).get("results", {}).get("products", [])
    return [
        Listing(
            title=p.get("title", ""),
            url=canonical_url(urljoin(base_url, p.get("url", ""))),
            available=p.get("available"),
            price=_price_from_text(p.get("price")),
            context=f"{p.get('vendor', '')} {p.get('type', '')}",
        )
        for p in products
        if p.get("title") and p.get("url")
    ]


def parse_shopify_collection(data: dict, base_url: str) -> list[Listing]:
    listings = []
    for p in data.get("products", []):
        variants = p.get("variants") or []
        if not p.get("title") or not p.get("handle"):
            continue
        listings.append(
            Listing(
                title=p["title"],
                url=canonical_url(urljoin(base_url, f"/products/{p['handle']}")),
                available=any(v.get("available") for v in variants) if variants else None,
                price=_price_from_text(variants[0].get("price")) if variants else "",
                context=f"{p.get('vendor', '')} {p.get('product_type', '')}",
            )
        )
    return listings


def parse_woocommerce(data: list, base_url: str) -> list[Listing]:
    listings = []
    for p in data if isinstance(data, list) else []:
        if not p.get("name") or not p.get("permalink"):
            continue
        prices = p.get("prices") or {}
        price = ""
        if prices.get("price"):
            try:
                price = _format_euro(int(prices["price"]) / 10 ** int(prices.get("currency_minor_unit", 2)))
            except (TypeError, ValueError):
                pass
        listings.append(
            Listing(
                title=html_lib.unescape(p["name"]),
                url=canonical_url(urljoin(base_url, p["permalink"])),
                available=bool(p.get("is_in_stock") and p.get("is_purchasable", True)),
                price=price,
                context=" ".join(c.get("name", "") for c in p.get("categories") or []),
            )
        )
    return listings


def _available_from_schema(value) -> bool | None:
    status = status_from_availability(value)
    if status is None or status == "unknown":
        return None
    return status in STATUSES_ACTIONABLE


def _itemprop_value(scope, prop: str) -> str:
    tag = scope.find(attrs={"itemprop": prop})
    if tag is None:
        return ""
    for attr in ("content", "href"):
        if tag.get(attr):
            return tag[attr]
    return tag.get_text(" ", strip=True)


def parse_html(html: str, page_url: str, accept: Callable[[str], bool], patterns: StatusPatterns) -> list[Listing]:
    """Listings from a server-rendered product or search page, most reliable
    source first: JSON-LD, then schema.org microdata, then the visible text
    of product cards whose link text passes `accept`."""
    found: dict[str, Listing] = {}

    def add(listing: Listing) -> None:
        if listing.title and listing.url not in found:
            found[listing.url] = listing

    blocks = extract_jsonld_product_blocks(html)
    for block in blocks:
        offer = first_offer(block)
        url = block.get("url") or offer.get("url")
        if not url:
            if len(blocks) > 1:
                continue  # can't tell which listing it belongs to, the card fallback below can
            url = page_url  # a product page describing itself
        add(
            Listing(
                title=html_lib.unescape(str(block.get("name", ""))).strip(),
                url=canonical_url(urljoin(page_url, url)),
                available=_available_from_schema(offer.get("availability")),
                price=_price_from_text(offer.get("price") or offer.get("lowPrice")),
            )
        )

    soup = BeautifulSoup(html, "lxml")
    for scope in soup.select('[itemtype*="schema.org/Product"]'):
        link = scope.find("a", href=True)
        url = _itemprop_value(scope, "url") or (link["href"] if link else page_url)
        add(
            Listing(
                title=_itemprop_value(scope, "name"),
                url=canonical_url(urljoin(page_url, url)),
                available=_available_from_schema(_itemprop_value(scope, "availability")),
                price=_price_from_text(_itemprop_value(scope, "price")),
            )
        )

    for link in soup.find_all("a", href=True):
        title = link.get("title") or link.get_text(" ", strip=True)
        if not title or not accept(title):
            continue
        url = canonical_url(urljoin(page_url, link["href"]))
        if url in found:
            continue
        card = find_card_container(link)
        status = classify_status(
            card.get_text(" ", strip=True) if card else "",
            patterns.preorder,
            patterns.in_stock,
            patterns.unavailable,
        )
        add(Listing(title=title, url=url, available=None if status == "unknown" else status in STATUSES_ACTIONABLE))

    return [listing for listing in found.values() if accept(listing.title)]


# --- network ----------------------------------------------------------------


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
            "Cache-Control": "no-cache",
        }
    )
    return session


def _get(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    # A throwaway query param defeats CDN/edge caches that would otherwise
    # serve a stale "sold out" for minutes after stock comes in.
    params = {**(params or {}), "_": random.randint(0, 10**9)}
    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise PollError(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code in _BLOCK_STATUS_CODES:
        retry_after = resp.headers.get("Retry-After", "")
        raise PollError(
            f"HTTP {resp.status_code} on {url}",
            blocked=True,
            retry_after=float(retry_after) if retry_after.isdigit() else None,
        )
    if resp.status_code >= 400:
        raise PollError(f"HTTP {resp.status_code} on {url}")
    return resp


_warned: set[str] = set()


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        logger.warning(message)


def _json(resp: requests.Response):
    try:
        return resp.json()
    except ValueError as exc:
        raise PollError(f"no JSON from {resp.url} - is the platform setting right?") from exc


def detect_platform(session: requests.Session, base_url: str) -> str:
    """'shopify' or 'woocommerce', probed through their public JSON
    endpoints. Raises PollError when neither answers."""
    base = base_url.rstrip("/")
    probes = (
        ("shopify", f"{base}/search/suggest.json", {"q": "pokemon", "resources[type]": "product"}, dict, "resources"),
        ("woocommerce", f"{base}/wp-json/wc/store/v1/products", {"per_page": 1}, list, None),
    )
    for platform, url, params, shape, key in probes:
        try:
            data = _get(session, url, params).json()
        except PollError as exc:
            if exc.blocked:
                raise
            continue
        except ValueError:
            continue
        if isinstance(data, shape) and (key is None or key in data):
            return platform
    raise PollError(f"Plattform von {base} nicht erkannt, bitte platform: html mit search_url eintragen")


def poll_shop(
    session: requests.Session, shop: Shop, accept: Callable[[str], bool], patterns: StatusPatterns
) -> tuple[list[Listing], bool]:
    """Every listing the shop currently shows for its queries/pages, deduped
    by URL, and whether every source answered (False when a page or
    collection was skipped, so missing products prove nothing)."""
    base = shop.url.rstrip("/")
    listings: list[Listing] = []
    complete = True
    if shop.platform == "auto":
        shop.platform = detect_platform(session, base)
        logger.info("%s: Plattform erkannt: %s", shop.name, shop.platform)

    if shop.platform == "shopify":
        for handle in shop.collections:
            try:
                resp = _get(session, f"{base}/collections/{handle}/products.json", {"limit": 250})
            except PollError as exc:
                # A renamed collection must not take the search queries down with it.
                if exc.blocked:
                    raise
                _warn_once(f"{shop.name}: Kollektion {handle!r} übersprungen ({exc})")
                complete = False
                continue
            listings += parse_shopify_collection(_json(resp), base)
        for query in shop.queries:
            params = {
                "q": query,
                "resources[type]": "product",
                "resources[limit]": SHOPIFY_SUGGEST_LIMIT,
                "resources[options][unavailable_products]": "last",
            }
            listings += parse_shopify_suggest(_json(_get(session, f"{base}/search/suggest.json", params)), base)
    elif shop.platform == "woocommerce":
        for query in shop.queries:
            params = {"search": query, "per_page": WOOCOMMERCE_PAGE_SIZE}
            listings += parse_woocommerce(_json(_get(session, f"{base}/wp-json/wc/store/v1/products", params)), base)
    elif shop.platform == "html":
        urls = list(shop.pages)
        if shop.search_url:
            urls += [shop.search_url.replace("{q}", requests.utils.quote(q)) for q in shop.queries]
        failures = []
        for url in urls:
            try:
                html = _get(session, url).text
            except PollError as exc:
                # One moved category page must not hide the others.
                if exc.blocked:
                    raise
                failures.append(exc)
                _warn_once(f"{shop.name}: {exc}")
                complete = False
                continue
            blocked = find_blocked_phrase(html)
            if blocked:
                raise PollError(f'bot check on {url} (matched "{blocked[0]}")', blocked=True)
            listings += parse_html(html, url, accept, patterns)
        if urls and len(failures) == len(urls):
            raise failures[0]
    else:
        raise PollError(f"unknown platform {shop.platform!r}")

    unique: dict[str, Listing] = {}
    for listing in listings:
        # A collection hit and a search hit for the same product can
        # disagree for a few seconds while caches catch up; trust "available".
        prev = unique.get(listing.url)
        if prev is None or (listing.available and not prev.available):
            unique[listing.url] = listing
    return list(unique.values()), complete
