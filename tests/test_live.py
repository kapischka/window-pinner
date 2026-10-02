from pathlib import Path

import pytest

from pokemon_preorder_bot import catalog, live
from pokemon_preorder_bot.live import LiveMonitor, Settings, load_config
from pokemon_preorder_bot.sources import (
    Listing,
    PollError,
    Shop,
    StatusPatterns,
    canonical_url,
    parse_html,
    parse_shopify_collection,
    parse_shopify_suggest,
    parse_woocommerce,
)

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = StatusPatterns(
    preorder=["vorbestellen"],
    in_stock=["in den warenkorb"],
    unavailable=["ausverkauft", "nicht verfügbar"],
)


def accept(title: str) -> bool:
    return catalog.is_30th(title)


@pytest.mark.parametrize(
    "title, context, expected",
    [
        ("Pokémon 30 Jahre Top-Trainer-Box Deutsch", "", True),
        ("Mega - 30th Celebration m6a Display - Japanisch", "", True),
        ("Japanese Display - 30th CELEBRATION (M6a)", "", True),
        ("30 Jahre Kollektion Feelinara-ex", "Pokémon", True),
        ("30 Jahre Kollektion Feelinara-ex", "", False),
        ("LEGO 30 Jahre Jubiläumsset", "", False),
        ("Pokémon Karmesin & Purpur Display", "", False),
    ],
)
def test_is_30th(title, context, expected):
    assert catalog.is_30th(title, context) is expected


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Pokémon 30 Jahre Top-Trainer-Box Deutsch", "DE"),
        ("Pokémon - 30 Jahre Tech-Sticker-Kollektion - DE", "DE"),
        ("Pokemon 30 Jahre Top-Trainer Box", "DE"),
        ("Pokémon 30th Celebration Display M6a Japanisch", "JP"),
        ("Pokemon 30th Celebration Premium Deck Espeon & Umbreon (JP)", "JP"),
        ("30th CELEBRATION プレミアムデッキセット", "JP"),
        ("Pokémon 30th Celebration Elite Trainer Box (Englisch)", "EN"),
        ("Pokémon 30th Celebration Elite Trainer Box", "?"),
    ],
)
def test_language(title, expected):
    assert catalog.language(title) == expected


def test_parse_shopify_suggest():
    data = {
        "resources": {
            "results": {
                "products": [
                    {
                        "title": "Pokémon 30 Jahre Poster-Kollektion Deutsch",
                        "url": "/products/poster-30-jahre?_pos=1&_sid=abc",
                        "available": True,
                        "price": "39.99",
                        "vendor": "Pokémon",
                        "type": "Kollektion",
                    }
                ]
            }
        }
    }
    [listing] = parse_shopify_suggest(data, "https://shop.de")
    assert listing.url == "https://shop.de/products/poster-30-jahre"
    assert listing.available is True
    assert listing.price == "39,99 €"
    assert "Pokémon" in listing.context


def test_parse_shopify_collection_any_variant_available():
    data = {
        "products": [
            {
                "title": "Display M6a Japanisch",
                "handle": "display-m6a",
                "vendor": "Pokémon",
                "variants": [{"available": False, "price": "159.95"}, {"available": True, "price": "169.95"}],
            }
        ]
    }
    [listing] = parse_shopify_collection(data, "https://shop.de")
    assert listing.available is True
    assert listing.url == "https://shop.de/products/display-m6a"


def test_parse_woocommerce():
    data = [
        {
            "name": "Pokemon 30 Jahre Tech-Sticker &amp; Kollektion",
            "permalink": "https://www.zambomba.de/shop/tech-sticker/",
            "is_in_stock": True,
            "is_purchasable": True,
            "prices": {"price": "3499", "currency_minor_unit": 2},
        }
    ]
    [listing] = parse_woocommerce(data, "https://www.zambomba.de")
    assert listing.title == "Pokemon 30 Jahre Tech-Sticker & Kollektion"
    assert listing.price == "34,99 €"
    assert listing.available is True


def test_parse_html_jsonld_product_page():
    html = """<html><head><script type="application/ld+json">
    {"@type": "Product", "name": "Pokémon TCG 30th CELEBRATION (M6a) Booster Display (Japanische Edition)",
     "offers": {"@type": "Offer", "price": "199.99", "availability": "https://schema.org/InStock"}}
    </script></head><body></body></html>"""
    [listing] = parse_html(html, "https://www.netto-online.de/p-1?x=1", accept, PATTERNS)
    assert listing.available is True
    assert listing.url == "https://www.netto-online.de/p-1"
    assert listing.price == "199,99 €"


def test_parse_html_microdata_listing():
    html = """<div itemscope itemtype="https://schema.org/Product">
      <a href="/Pokemon-30-Jahre-Top-Trainer-Box"><span itemprop="name">Pokemon 30 Jahre Top-Trainer-Box (deutsch)</span></a>
      <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
        <meta itemprop="price" content="64.99"><link itemprop="availability" href="https://schema.org/OutOfStock">
      </div></div>"""
    [listing] = parse_html(html, "https://www.gate-to-the-games.de/?qs=30+jahre", accept, PATTERNS)
    assert listing.available is False
    assert listing.url == "https://www.gate-to-the-games.de/Pokemon-30-Jahre-Top-Trainer-Box"


def test_parse_html_card_text_fallback_keeps_cards_apart():
    html = """<div class="grid">
      <div class="product-box"><a href="/a">Pokemon 30 Jahre Poster-Kollektion</a><span>29,99 €</span>
        <button>In den Warenkorb</button></div>
      <div class="product-box"><a href="/b">Pokemon 30th Celebration Display JP</a><span>Ausverkauft</span></div>
      <div class="product-box"><a href="/c">Pokemon Prismatische Entwicklungen Display</a>
        <button>In den Warenkorb</button></div>
    </div>"""
    listings = {l.url.rsplit("/", 1)[-1]: l.available for l in parse_html(html, "https://x.de/s", accept, PATTERNS)}
    assert listings == {"a": True, "b": False}


def test_canonical_url():
    assert canonical_url("https://Shop.DE/products/x/?_pos=1#top") == "https://shop.de/products/x"


def _monitor(tmp_path, monkeypatch, listings):
    settings = Settings({"languages": ["DE", "JP"], "include_unknown_language": False, "exclude": ["psa "]})
    shop = Shop(name="Testshop", platform="shopify", url="https://shop.de")
    monitor = LiveMonitor(settings, [shop], tmp_path / "state.json", tmp_path / "results.json", open_browser=False)
    feed = iter(listings)
    monkeypatch.setattr(live, "poll_shop", lambda *args: next(feed))
    return monitor, shop


def test_monitor_alerts_only_on_transition_and_filters(tmp_path, monkeypatch):
    de = "Pokémon 30 Jahre Top-Trainer-Box Deutsch"
    rounds = [
        [
            Listing(de, "https://shop.de/products/ttb", False),
            Listing("Pokémon 30th Celebration ETB Englisch", "https://shop.de/products/en", True),
            Listing("Pokémon 30 Jahre Glurak PSA 10", "https://shop.de/products/psa", True),
        ],
        [Listing(de, "https://shop.de/products/ttb", True, "54,99 €")],
        [Listing(de, "https://shop.de/products/ttb", None)],  # unclear signal keeps last state
        [Listing(de, "https://shop.de/products/ttb", False)],
    ]
    monitor, shop = _monitor(tmp_path, monkeypatch, rounds)

    assert monitor.check(shop, None) == []
    assert set(monitor.products) == {"https://shop.de/products/ttb"}

    [fresh] = monitor.check(shop, None)
    assert fresh["language"] == "DE" and fresh["price"] == "54,99 €"

    assert monitor.check(shop, None) == []
    assert monitor.products["https://shop.de/products/ttb"]["available"] is True

    assert monitor.check(shop, None) == []
    assert monitor.products["https://shop.de/products/ttb"]["available"] is False
    assert (tmp_path / "results.json").exists()


def test_monitor_records_poll_errors(tmp_path, monkeypatch):
    monitor, shop = _monitor(tmp_path, monkeypatch, [])

    def blocked(*args):
        raise PollError("HTTP 429", blocked=True)

    monkeypatch.setattr(live, "poll_shop", blocked)
    with pytest.raises(PollError):
        monitor.check(shop, None)
    assert monitor.shop_status["Testshop"]["status"] == "blocked"


def test_shipped_config_loads():
    settings, shops = load_config(ROOT / "config" / "live_shops.yaml")
    assert settings.languages == {"DE", "JP"}
    assert shops and all(s.platform in {"shopify", "woocommerce", "html"} for s in shops)
    assert all(s.queries for s in shops)
