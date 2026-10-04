"""Decides whether a shop listing is a 30th anniversary product and which
language edition it is, purely from its title (plus vendor/type metadata
where a shop exposes it)."""

from __future__ import annotations

import re
from datetime import date

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


# Wording German shops use for stock that can be ordered but isn't there
# yet. Checked against titles, tags, categories and stock texts. Phrases
# like "Release 02.10." or "lieferbar ab" are left to the date check below,
# since shops keep them in titles long after the release.
_PREORDER = re.compile(r"vorbestell|vorverkauf|pre[\s-]?order|\bvvk\b|reservier|backorder|予約", re.IGNORECASE)
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")


def looks_preorder(text: str, today: date | None = None) -> bool:
    """True if the text announces a preorder, either in words or by naming a
    date that hasn't been reached yet ("Top-Trainer-Box ab 16.10.2026")."""
    if _PREORDER.search(text):
        return True
    today = today or date.today()
    for day, month, year in _DATE.findall(text):
        try:
            when = date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
        except ValueError:
            continue
        if when > today:
            return True
    return False


# Sealed products name their packaging. A title without any of these words
# is a single card, a sleeve or other loose merch.
_SEALED = re.compile(
    r"display|booster|bundle|box|top[\s-]*trainer|elite[\s-]*trainer|\betb\b|kollektion|collection|\btins?\b"
    r"|blister|\bdecks?\b|card[\s-]*set|ordner|binder|poster|futuristic|\bcase\b|\bovp\b|sealed|versiegelt"
    r"|パック|ボックス|デッキ|セット",
    re.IGNORECASE,
)
# Unmistakable single card signs, checked first because singles are often
# advertised with the box they came from ("Nidorina Promo aus Top-Trainer-Box").
_SINGLE = re.compile(
    r"\b\d{1,3}\s*/\s*\d{2,3}\b"  # card number 205/165
    r"|einzelkarte|single[\s-]*card|\bsingles?\b|promo[\s-]*kart|promo[\s-]*card|holo[\s-]*karte"
    r"|\bpsa\b|\bcgc\b|\bbgs\b|\bgraded\b|grading|\bkarte aus\b|aus\s+(der|dem|top|einer)\b|\bnur die karte\b"
    r"|\b(sar|sir|chr|csr|fur|ur|sr|ar|rrr)\b(?!.*\b(display|box|bundle|kollektion|collection|deck)\b)",
    re.IGNORECASE,
)


def is_sealed(title: str) -> bool:
    """True for sealed products (displays, boxes, collections, bundles,
    blisters, decks, tins, single booster packs), False for single cards."""
    if _SINGLE.search(title):
        return False
    return bool(_SEALED.search(title))


def is_excluded(title: str, excludes: list[str]) -> bool:
    text = _normalize(title)
    return any(e.lower() in text for e in excludes)
