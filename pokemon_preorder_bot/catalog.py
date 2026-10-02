"""Decides whether a shop listing is a 30th anniversary product and which
language edition it is, purely from its title (plus vendor/type metadata
where a shop exposes it)."""

from __future__ import annotations

import re

# Unique enough on their own: the set's international name ("30th
# Celebration", also used verbatim in Japan) and the Japanese set code.
_STRONG = re.compile(r"30th[\s-]*celebration|\bm6a\b", re.IGNORECASE)

# Generic anniversary wording (German set name "30 Jahre"), trusted only when
# the listing also mentions Pokémon somewhere, so "30 Jahre LEGO" or a
# "30th Anniversary" plush on a general retailer doesn't count.
_WEAK = re.compile(r"30[\s-]*jahre|30th[\s-]*anniversary|30周年", re.IGNORECASE)
POKEMON_HINTS = ("pokemon", "pokémon", "ポケモン", "pkm", "pocket monsters")

# Explicit language markers in a title win over anything implied. Korean,
# Chinese and the European editions are recognized so they can be dropped
# instead of slipping through as "unknown" (Chinese titles share kanji with
# Japanese ones).
_LANGUAGE_MARKERS = (
    ("JP", re.compile(r"japanisch|japanese|\bjapan\b|\bjap\b|\bjpn?\b|日本語", re.IGNORECASE)),
    (
        "OTHER",
        re.compile(
            r"korean|koreanisch|\bkor\b|\bkr\b|chinese|chinesisch|\b[st]-?chn?\b|\bcn\b|simplified|traditional"
            r"|französisch|french|\bfra?\b|italienisch|italian|\bita\b|spanisch|spanish|\besp\b"
            r"|portugiesisch|portuguese|\bthai\b|indonesisch|indonesian|[가-힯]",
            re.IGNORECASE,
        ),
    ),
    ("EN", re.compile(r"englisch|english|\beng?\b", re.IGNORECASE)),
    ("DE", re.compile(r"deutsch|german|\bde\b|\bger\b|\bdt\b", re.IGNORECASE)),
)

# Implied by wording when no marker is present: kana, the JP-only set code
# and the JP-only products (Futuristic Box, Premium Deck Set Espeon &
# Umbreon, Card Sets) mean Japanese, the German product names mean German,
# the English product names (which German shops keep for English stock)
# mean English.
_LANGUAGE_HINTS = (
    (
        "JP",
        re.compile(r"\bm6a\b|[぀-ヿ]|futuristic|premium[\s-]*deck|espeon|umbreon|card[\s-]*set", re.IGNORECASE),
    ),
    ("DE", re.compile(r"30[\s-]*jahre|top[\s-]*trainer|kollektion|ordner|2er[\s-]*pack", re.IGNORECASE)),
    ("EN", re.compile(r"elite[\s-]*trainer|\betb\b|collection|binder|2[\s-]*pack blister", re.IGNORECASE)),
)

UNKNOWN_LANGUAGE = "?"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip().lower()


def is_30th(title: str, context: str = "", assume_pokemon: bool = False) -> bool:
    """True if the title names the 30th anniversary set. `context` is extra
    text (vendor, product type) used only to confirm it's a Pokémon item."""
    text = _normalize(title)
    if _STRONG.search(text):
        return True
    if not _WEAK.search(text):
        return False
    if assume_pokemon:
        return True
    haystack = f"{text} {_normalize(context)}"
    return any(h in haystack for h in POKEMON_HINTS)


def language(title: str, default: str = UNKNOWN_LANGUAGE) -> str:
    """'DE', 'JP', 'EN', 'OTHER' or `default` when the title gives no hint."""
    for patterns in (_LANGUAGE_MARKERS, _LANGUAGE_HINTS):
        for lang, pattern in patterns:
            if pattern.search(title):
                return lang
    return default


def is_excluded(title: str, excludes: list[str]) -> bool:
    text = _normalize(title)
    return any(e.lower() in text for e in excludes)
