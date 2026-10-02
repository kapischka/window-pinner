"""Decides whether a shop listing is a 30th anniversary product and which
language edition it is, purely from its title (plus vendor/type metadata
where a shop exposes it)."""

from __future__ import annotations

import re

# Unique enough on their own: the set's international name ("30th
# Celebration", also used verbatim in Japan) and the Japanese set code.
STRONG_KEYWORDS = ("30th celebration", "m6a")

# Generic anniversary wording (German set name "30 Jahre"), trusted only when
# the listing also mentions Pokémon somewhere, so "30 Jahre LEGO" or a
# "30th Anniversary" plush on a general retailer doesn't count.
WEAK_KEYWORDS = ("30 jahre", "30th anniversary", "30周年")
POKEMON_HINTS = ("pokemon", "pokémon", "ポケモン", "pkm", "pocket monsters")

# Explicit language markers in a title win over anything implied.
_JP_MARKER = re.compile(r"japanisch|japanese|\bjap\b|\bjpn?\b|日本語", re.IGNORECASE)
_EN_MARKER = re.compile(r"englisch|english|\beng?\b", re.IGNORECASE)
_DE_MARKER = re.compile(r"deutsch|german|\bde\b|\bger\b|\bdt\b", re.IGNORECASE)
# Implied: Japanese script or the JP-only set code means a Japanese product,
# the German set name means a German one.
_JP_IMPLIED = re.compile(r"m6a|[぀-ヿ一-鿿]", re.IGNORECASE)
_DE_IMPLIED = re.compile(r"30 jahre", re.IGNORECASE)

UNKNOWN_LANGUAGE = "?"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip().lower()


def is_30th(title: str, context: str = "", assume_pokemon: bool = False) -> bool:
    """True if the title names the 30th anniversary set. `context` is extra
    text (vendor, product type) used only to confirm it's a Pokémon item."""
    text = _normalize(title)
    if any(k in text for k in STRONG_KEYWORDS):
        return True
    if not any(k in text for k in WEAK_KEYWORDS):
        return False
    if assume_pokemon:
        return True
    haystack = f"{text} {_normalize(context)}"
    return any(h in haystack for h in POKEMON_HINTS)


def language(title: str, default: str = UNKNOWN_LANGUAGE) -> str:
    """'DE', 'JP', 'EN' or `default` when the title gives no hint."""
    if _JP_MARKER.search(title):
        return "JP"
    if _EN_MARKER.search(title):
        return "EN"
    if _DE_MARKER.search(title):
        return "DE"
    if _JP_IMPLIED.search(title):
        return "JP"
    if _DE_IMPLIED.search(title):
        return "DE"
    return default


def is_excluded(title: str, excludes: list[str]) -> bool:
    text = _normalize(title)
    return any(e.lower() in text for e in excludes)
