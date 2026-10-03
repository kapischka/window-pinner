"""Prices: reading them from shop text and comparing them with the UVP
(German products) or a reference import price (Japanese products), so
scalper listings can be shown with their markup and dropped when it gets
too steep."""

from __future__ import annotations

import re
from dataclasses import dataclass

# "54,99 €", "€ 54,99", "EUR 1.299,00", "54.99" (JSON), "54,-"
_AMOUNT = r"(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?:,-)?"
_PRICE_IN_TEXT = re.compile(rf"(?:€|eur)\s*{_AMOUNT}|{_AMOUNT}\s*(?:€|eur\b)", re.IGNORECASE)
# Struck-through comparison prices that come before the real one in a card.
_NOT_THE_PRICE = re.compile(r"(uvp|statt|streichpreis|bisher|zuvor|ursprünglich|unverbindliche)\W*$", re.IGNORECASE)


def parse_price(value) -> float | None:
    """A price as float from a number or a German/English price string."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text:
        return None
    if "," in text and "." in text:
        # The later separator is the decimal one: 1.299,00 or 1,299.00
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in text:
        whole, _, frac = text.rpartition(",")
        text = f"{whole.replace(',', '')}.{frac}" if len(frac) <= 2 else text.replace(",", "")
    elif text.count(".") > 1 or (text.count(".") == 1 and len(text.rpartition(".")[2]) == 3):
        text = text.replace(".", "")  # thousands dots: 1.299
    try:
        amount = float(text)
    except ValueError:
        return None
    return amount if amount > 0 else None


def price_in_text(text: str) -> float | None:
    """The first price in a product card's text, skipping comparison prices
    labelled UVP/statt that shops print next to the real one."""
    for match in _PRICE_IN_TEXT.finditer(text):
        if _NOT_THE_PRICE.search(text[max(0, match.start() - 25) : match.start()]):
            continue
        amount = parse_price(match.group(1) or match.group(2))
        if amount:
            return amount
    return None


def format_euro(amount: float | None) -> str:
    if amount is None:
        return ""
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass
class Reference:
    name: str
    language: str
    price: float
    pattern: re.Pattern
    kind: str = "UVP"  # "UVP" for German retail, "Richtpreis" for imports


def load_references(raw: list[dict]) -> list[Reference]:
    refs = []
    for entry in raw or []:
        try:
            refs.append(
                Reference(
                    name=str(entry["name"]),
                    language=str(entry.get("language", "DE")).upper(),
                    price=float(entry["uvp"]),
                    pattern=re.compile(entry["match"], re.IGNORECASE),
                    kind=str(entry.get("kind", "UVP")),
                )
            )
        except (KeyError, TypeError, ValueError, re.error) as exc:
            raise ValueError(f"Preis-Eintrag {entry!r} ist ungültig: {exc}") from exc
    return refs


def find_reference(title: str, language: str, refs: list[Reference]) -> Reference | None:
    """The first reference whose pattern matches the title, preferring the
    listing's language. More specific entries must come first in the list."""
    candidates = [r for r in refs if r.pattern.search(title)]
    for ref in candidates:
        if ref.language == language:
            return ref
    return candidates[0] if candidates and language == "?" else None


def markup_percent(price: float | None, ref: Reference | None) -> float | None:
    if price is None or ref is None:
        return None
    return round((price / ref.price - 1) * 100, 1)
