from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

CONTEXT_WINDOW = 300  # characters of surrounding text checked for status keywords (product_page fallback)
CARD_HINTS = ("product", "card", "tile", "item", "result", "listing", "sku")
FALLBACK_HOPS = 3  # levels to climb when no card-like ancestor class is found
MAX_ANCESTOR_HOPS = 8


@dataclass
class MatchResult:
    keyword: str
    status: str  # "available" | "unavailable" | "unknown"
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


def _status_in_window(window: str, available_patterns: list[str], unavailable_patterns: list[str]) -> str:
    window_lower = window.lower()
    has_unavailable = any(p.lower() in window_lower for p in unavailable_patterns)
    has_available = any(p.lower() in window_lower for p in available_patterns)
    if has_unavailable and not has_available:
        return "unavailable"
    if has_available and not has_unavailable:
        return "available"
    if has_available and has_unavailable:
        # Ambiguous page (e.g. listing with mixed stock) - prefer the more
        # actionable signal since a false "available" just costs one extra look.
        return "available"
    return "unknown"


def match_search_page(
    html: str,
    keywords: list[str],
    available_patterns: list[str],
    unavailable_patterns: list[str],
) -> list[MatchResult]:
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
            status = _status_in_window(window, available_patterns, unavailable_patterns)
            snippet = re.sub(r"\s+", " ", window)[: CONTEXT_WINDOW * 2].strip()
            results.append(MatchResult(keyword=keyword, status=status, snippet=snippet))
    return results


def match_product_page(
    html: str,
    target_name: str,
    available_patterns: list[str],
    unavailable_patterns: list[str],
) -> MatchResult:
    text = _page_text(html)
    status = _status_in_window(text, available_patterns, unavailable_patterns)
    snippet = re.sub(r"\s+", " ", text)[:CONTEXT_WINDOW].strip()
    return MatchResult(keyword=target_name, status=status, snippet=snippet)
