from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

CONTEXT_WINDOW = 300  # characters of surrounding text checked for status keywords (product_page fallback)
CARD_HINTS = ("product", "card", "tile", "item", "result", "listing", "sku")
FALLBACK_HOPS = 3  # levels to climb when no card-like ancestor class is found
MAX_ANCESTOR_HOPS = 8

# Status values, most to least actionable:
#   "preorder"    - explicit preorder/reservation/lottery-entry language found
#   "in_stock"    - a regular buy-now/add-to-cart signal found (no preorder wording)
#   "unavailable" - sold out / not yet listed / notify-me language found
#   "unknown"     - product mentioned but no recognizable status language nearby
STATUSES_ACTIONABLE = ("preorder", "in_stock")


@dataclass
class MatchResult:
    keyword: str
    status: str
    snippet: str


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
    """
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

            if product_keywords and not _has_any(window_lower, product_keywords):
                continue

            status = _classify_status(window, preorder_patterns, in_stock_patterns, unavailable_patterns)
            snippet = re.sub(r"\s+", " ", window)[: CONTEXT_WINDOW * 2].strip()
            results.append(MatchResult(keyword=keyword, status=status, snippet=snippet))
    return results


def match_product_page(
    html: str,
    target_name: str,
    preorder_patterns: list[str],
    in_stock_patterns: list[str],
    unavailable_patterns: list[str],
) -> MatchResult:
    text = _page_text(html)
    status = _classify_status(text, preorder_patterns, in_stock_patterns, unavailable_patterns)
    snippet = re.sub(r"\s+", " ", text)[:CONTEXT_WINDOW].strip()
    return MatchResult(keyword=target_name, status=status, snippet=snippet)
