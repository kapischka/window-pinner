from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

CONTEXT_WINDOW = 300  # characters of surrounding text checked for status keywords (product_page fallback)
CARD_HINTS = ("product", "card", "tile", "item", "result", "listing", "sku")
FALLBACK_HOPS = 3  # levels to climb when no card-like ancestor class is found
MAX_ANCESTOR_HOPS = 8
LABEL_SNIPPET_LEN = 40  # chars used to disambiguate cards that matched no product keyword

# Status values, most to least actionable:
#   "preorder"    - explicit preorder/reservation/lottery-entry language found
#   "in_stock"    - a regular buy-now/add-to-cart signal found (no preorder wording)
#   "unavailable" - sold out / not yet listed / notify-me language found
#   "unknown"     - product mentioned but no recognizable status language nearby
STATUSES_ACTIONABLE = ("preorder", "in_stock")

# schema.org Offer.availability values (the last URL path segment, lowercased)
# mapped to our status vocabulary. Many storefronts embed this in JSON-LD for
# search engines - when present it's a more reliable signal than guessing
# from visible button text, so it's tried first.
_SCHEMA_AVAILABILITY_MAP = {
    "instock": "in_stock",
    "limitedavailability": "in_stock",
    "onlineonly": "in_stock",
    "instorenow": "in_stock",
    "preorder": "preorder",
    "presale": "preorder",
    "backorder": "preorder",
    "outofstock": "unavailable",
    "soldout": "unavailable",
    "discontinued": "unavailable",
    "invalid": "unavailable",
}

# Phrases that indicate a bot-detection/CAPTCHA interstitial rather than
# real content, so that gets reported distinctly from a genuine "no product
# found yet". Kept deliberately specific and multi-word (real interstitial
# copy, not just "captcha" or "access denied" alone) - short generic phrases
# turned out to false-positive constantly: a bare "captcha" substring check
# also matched "reCAPTCHA", which shows up in the routine legal-disclosure
# footer text ("This site is protected by reCAPTCHA...") that nearly every
# ordinary shop embeds for its contact/newsletter forms, and generic phrases
# like "access denied" or "request blocked" turn out to appear in all sorts
# of unrelated login/permission text.
_BLOCKED_PHRASES = (
    "complete the captcha",
    "solve the captcha",
    "verify the captcha",
    "are you a human",
    "verify you are a human",
    "verifying you are human",
    "checking your browser",
    "unusual traffic from your computer",
    "pardon our interruption",
    "robot check",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "ddos protection by cloudflare",
)


@dataclass
class MatchResult:
    keyword: str
    status: str
    snippet: str


def find_blocked_phrase(html: str) -> tuple[str, str] | None:
    """Returns (matched_phrase, surrounding_snippet) if the page looks like a
    bot-detection/CAPTCHA interstitial, else None. Surfacing the actual match
    (not just a yes/no) is what let the "captcha"/"recaptcha" false positive
    get diagnosed and fixed - any future false positive should be just as
    visible in the dashboard's detail text instead of a guessing game.
    """
    text = _page_text(html)
    text_lower = text.lower()
    for phrase in _BLOCKED_PHRASES:
        match = re.search(r"\b" + re.escape(phrase) + r"\b", text_lower)
        if match:
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            return phrase, snippet
    return None


def looks_blocked(html: str) -> bool:
    """Best-effort check for a bot-detection/CAPTCHA page - see
    `find_blocked_phrase` for the phrase actually matched."""
    return find_blocked_phrase(html) is not None


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ")


def _clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup


def _status_from_availability(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    token = value.rstrip("/").rsplit("/", 1)[-1].strip().lower()
    return _SCHEMA_AVAILABILITY_MAP.get(token)


def _extract_jsonld_products(html: str) -> list[tuple[str, str | None]]:
    """Pull (name, availability) pairs out of any schema.org Product/Offer
    JSON-LD on the page. Most modern storefronts embed this for search
    engines regardless of what JS framework renders the visible page, so
    it's often available even when scraping a heavily client-rendered site.
    """
    soup = BeautifulSoup(html, "lxml")
    stack: list = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack.extend(data if isinstance(data, list) else [data])

    products: list[tuple[str, str | None]] = []
    seen_ids = set()
    while stack:
        block = stack.pop()
        if not isinstance(block, dict) or id(block) in seen_ids:
            continue
        seen_ids.add(id(block))

        if isinstance(block.get("@graph"), list):
            stack.extend(block["@graph"])
        if isinstance(block.get("itemListElement"), list):
            for el in block["itemListElement"]:
                if isinstance(el, dict):
                    stack.append(el.get("item", el))

        type_ = block.get("@type")
        types_lower = {str(t).lower() for t in (type_ if isinstance(type_, list) else [type_]) if t}
        if "product" not in types_lower:
            continue

        name = block.get("name") or ""
        offers = block.get("offers")
        availability = None
        if isinstance(offers, dict):
            availability = offers.get("availability")
        elif isinstance(offers, list):
            for offer in offers:
                if isinstance(offer, dict) and offer.get("availability"):
                    availability = offer["availability"]
                    break
        if name:
            products.append((name, availability))
    return products


def _find_card_container(text_node) -> "BeautifulSoup":
    """Walk up from a matched text node to the element that best represents
    its enclosing product card, so status keywords from a neighbouring card
    on the same listing page don't bleed into this one.

    Prefers the nearest ancestor whose class name looks like a product card
    (handles siblings like price/button that live outside the matched title
    tag). Falls back to a fixed number of hops when no such ancestor exists,
    since stopping at the first small tag (e.g. an <h3> title) would miss
    those sibling elements entirely.
    """
    ancestors = []
    node = text_node.parent
    for _ in range(MAX_ANCESTOR_HOPS):
        if node is None:
            break
        ancestors.append(node)
        node = node.parent

    if not ancestors:
        return text_node.parent

    for ancestor in ancestors:
        classes = " ".join(ancestor.get("class", [])).lower() if hasattr(ancestor, "get") else ""
        if any(hint in classes for hint in CARD_HINTS):
            return ancestor

    return ancestors[min(FALLBACK_HOPS, len(ancestors) - 1)]


def _classify_status(
    window: str,
    preorder_patterns: list[str],
    in_stock_patterns: list[str],
    unavailable_patterns: list[str],
) -> str:
    window_lower = window.lower()
    has_unavailable = any(p.lower() in window_lower for p in unavailable_patterns)
    has_preorder = any(p.lower() in window_lower for p in preorder_patterns)
    has_in_stock = any(p.lower() in window_lower for p in in_stock_patterns)

    # Explicit preorder/reservation/lottery wording is the strongest, most
    # specific signal - trust it even if a generic "sold out" phrase also
    # appears elsewhere in a noisy card (e.g. a *different* size/variant).
    if has_preorder:
        return "preorder"
    if has_unavailable and not has_in_stock:
        return "unavailable"
    if has_in_stock and not has_unavailable:
        return "in_stock"
    if has_in_stock and has_unavailable:
        # Ambiguous (e.g. a disabled "Add to Cart" button next to "Out of
        # Stock" text) - don't guess either way.
        return "unknown"
    return "unknown"


def _has_any(text_lower: str, terms: list[str]) -> bool:
    return any(t.lower() in text_lower for t in terms)


def match_search_page(
    html: str,
    keywords: list[str],
    product_keywords: list[str],
    preorder_patterns: list[str],
    in_stock_patterns: list[str],
    unavailable_patterns: list[str],
) -> list[MatchResult]:
    """Scan a listing/search page for product cards matching `keywords`.

    If `product_keywords` is non-empty, a card must ALSO contain one of those
    terms to count - this is what keeps a generic search for "30th
    Celebration" from also flagging unrelated same-era merch (e.g. the
    separate "Pokémon Day 2026 Collection" or plush/apparel lines) that
    happens to share the word "30th" but isn't actually this TCG release.

    Tries schema.org JSON-LD product data first (see `_extract_jsonld_products`)
    since it's a more reliable signal than guessing from visible text; falls
    back to a DOM-based heuristic when no matching structured data is found.
    """
    jsonld_results: list[MatchResult] = []
    for name, availability in _extract_jsonld_products(html):
        name_lower = name.lower()
        matched_keyword = next((k for k in keywords if k.lower() in name_lower), None)
        if not matched_keyword:
            continue
        if product_keywords and not _has_any(name_lower, product_keywords):
            continue
        status = _status_from_availability(availability) or "unknown"
        jsonld_results.append(
            MatchResult(keyword=f"{matched_keyword} — {name}", status=status, snippet=f"[structured data] {name} (availability: {availability})")
        )
    if jsonld_results:
        return jsonld_results

    soup = _clean_soup(html)
    results: list[MatchResult] = []
    for keyword in keywords:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        seen_containers = set()
        for text_node in soup.find_all(string=pattern):
            container = _find_card_container(text_node)
            if container is None:
                continue
            if id(container) in seen_containers:
                continue
            seen_containers.add(id(container))
            window = container.get_text(" ", strip=True)
            window_lower = window.lower()

            matched_products = [p for p in product_keywords if p.lower() in window_lower]
            if product_keywords and not matched_products:
                continue

            status = _classify_status(window, preorder_patterns, in_stock_patterns, unavailable_patterns)
            snippet = re.sub(r"\s+", " ", window)[: CONTEXT_WINDOW * 2].strip()

            # The label must uniquely identify *this card*, not just the set
            # keyword - otherwise two different products matched on the same
            # page (e.g. an Elite Trainer Box and a Sylveon ex box) collapse
            # onto the same state-tracking key and silently overwrite each
            # other's remembered status between runs.
            if matched_products:
                label = f"{keyword} — {matched_products[0]}"
            else:
                label = f"{keyword} — {snippet[:LABEL_SNIPPET_LEN]}"

            results.append(MatchResult(keyword=label, status=status, snippet=snippet))
    return results


def match_product_page(
    html: str,
    target_name: str,
    keywords: list[str],
    preorder_patterns: list[str],
    in_stock_patterns: list[str],
    unavailable_patterns: list[str],
) -> MatchResult:
    for name, availability in _extract_jsonld_products(html):
        name_lower = name.lower()
        if keywords and not _has_any(name_lower, keywords):
            continue
        status = _status_from_availability(availability)
        if status:
            return MatchResult(keyword=target_name, status=status, snippet=f"[structured data] {name} (availability: {availability})")

    text = _page_text(html)
    text_lower = text.lower()
    if keywords and not _has_any(text_lower, keywords):
        snippet = re.sub(r"\s+", " ", text)[:CONTEXT_WINDOW].strip()
        return MatchResult(
            keyword=target_name,
            status="no_match",
            snippet=f"none of the expected keywords were found on this page - it may have moved. {snippet}",
        )

    status = _classify_status(text, preorder_patterns, in_stock_patterns, unavailable_patterns)
    snippet = re.sub(r"\s+", " ", text)[:CONTEXT_WINDOW].strip()
    return MatchResult(keyword=target_name, status=status, snippet=snippet)
