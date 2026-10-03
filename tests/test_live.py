import json
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from pokemon_preorder_bot import catalog, live, ui
from pokemon_preorder_bot.live import LiveMonitor, Settings, load_config
from pokemon_preorder_bot.sources import (
    Listing,
    PollError,
    Shop,
    StatusPatterns,
    canonical_url,
    detect_platform,
    parse_html,
    parse_shopify_collection,
    parse_shopify_suggest,
    parse_woocommerce,
    poll_shop,
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
        ("Pokemon 30-Jahre Ordner", "", True),
        ("Drehmaschine XM6A1", "", False),
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
        ("Pokémon 30th Celebration Elite Trainer Box", "EN"),
        ("Pokémon 30th Celebration Poster Collection", "EN"),
        ("Pokemon 30 Jahre Poster-Kollektion", "DE"),
        ("30th Celebration Futuristic Box", "JP"),
        ("Japan Import 30th Celebration Booster", "JP"),
        ("30th Celebration Booster Box Simplified Chinese", "OTHER"),
        ("30th Celebration Display Koreanisch", "OTHER"),
        ("포켓몬 30th Celebration", "OTHER"),
        ("Pokémon 30th Celebration Booster", "?"),
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
    [listing] = parse_html(html, "https://www.netto-online.de/p-1?_pos=1&utm_source=x", accept, PATTERNS)
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
    assert canonical_url("https://Shop.DE/products/x/?_pos=1&_sid=a&utm_medium=b#top") == "https://shop.de/products/x"
    # Product ids in the query must survive, or every product of such a shop collapses into one.
    assert canonical_url("https://shop.de/index.php?b=2&a=1&_pos=3") == "https://shop.de/index.php?a=1&b=2"


def test_parse_html_ignores_urlless_jsonld_on_listing_pages():
    block = '{"@type": "Product", "name": "%s", "offers": {"availability": "https://schema.org/InStock"}}'
    html = (
        '<script type="application/ld+json">[%s, %s]</script>'
        % (block % "Pokémon 30th Celebration Display JP", block % "Pokémon 30 Jahre Top-Trainer-Box")
    )
    assert parse_html(html, "https://x.de/suche", accept, PATTERNS) == []


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self.headers = {}
        self._payload = payload
        self.text = text
        self.url = "fake"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, params=None, timeout=None):
        return self.routes.get(url, FakeResponse(404))


def test_html_shop_survives_one_broken_page():
    shop = Shop(name="S", platform="html", url="https://s.de", pages=["https://s.de/alt", "https://s.de/neu"])
    page = '<div class="product"><a href="/p">Pokemon 30 Jahre Poster-Kollektion</a><button>In den Warenkorb</button></div>'
    session = FakeSession({"https://s.de/neu": FakeResponse(text=page)})
    [listing], complete = poll_shop(session, shop, accept, PATTERNS)
    assert listing.available is True
    assert complete is False  # products of the broken page must not count as delisted

    with pytest.raises(PollError):
        poll_shop(FakeSession({}), shop, accept, PATTERNS)


def test_auto_platform_detection():
    session = FakeSession({"https://w.de/wp-json/wc/store/v1/products": FakeResponse(payload=[])})
    assert detect_platform(session, "https://w.de/") == "woocommerce"
    session = FakeSession({"https://s.de/search/suggest.json": FakeResponse(payload={"resources": {}})})
    assert detect_platform(session, "https://s.de") == "shopify"
    with pytest.raises(PollError):
        detect_platform(FakeSession({}), "https://x.de")


def _monitor(tmp_path, monkeypatch, listings):
    settings = Settings({"languages": ["DE", "JP"], "include_unknown_language": False, "exclude": ["psa "]})
    shop = Shop(name="Testshop", platform="shopify", url="https://shop.de")
    monitor = LiveMonitor(settings, [shop], tmp_path / "state.json", tmp_path / "results.json")
    feed = iter(listings)
    monkeypatch.setattr(live, "poll_shop", lambda *args: (next(feed), True))
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


def test_monitor_marks_delisted_products_gone_and_realerts(tmp_path, monkeypatch):
    ttb = Listing("Pokémon 30 Jahre Top-Trainer-Box Deutsch", "https://shop.de/products/ttb", True)
    rounds = [[ttb]] + [[]] * live.MISSES_UNTIL_GONE + [[ttb]]
    monitor, shop = _monitor(tmp_path, monkeypatch, rounds)

    assert len(monitor.check(shop, None)) == 1
    for _ in range(live.MISSES_UNTIL_GONE - 1):
        monitor.check(shop, None)
        assert monitor.products[ttb.url]["available"] is True
    monitor.check(shop, None)
    assert monitor.products[ttb.url]["available"] is False
    assert len(monitor.check(shop, None)) == 1


def test_monitor_turns_unexpected_errors_into_status(tmp_path, monkeypatch):
    monitor, shop = _monitor(tmp_path, monkeypatch, [])

    def broken(*args):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr(live, "poll_shop", broken)
    with pytest.raises(PollError):
        monitor.check(shop, None)
    assert monitor.shop_status["Testshop"]["status"] == "error"


def test_once_mode_does_not_persist_state(tmp_path, monkeypatch):
    monitor, shop = _monitor(tmp_path, monkeypatch, [[Listing("30th Celebration Display JP", "https://shop.de/d", True)]])
    monitor.persist_state = False
    monitor.check(shop, None)
    assert not (tmp_path / "state.json").exists()
    assert (tmp_path / "results.json").exists()


def test_load_config_rejects_bad_entries(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("shops:\n  - {name: A, platform: magento, url: 'https://a.de'}\n")
    with pytest.raises(ValueError, match="platform"):
        load_config(bad)
    bad.write_text("shops:\n  - {name: A, platform: shopify, url: 'https://a.de'}\n  - {name: A, platform: shopify, url: 'https://b.de'}\n")
    with pytest.raises(ValueError, match="doppelt"):
        load_config(bad)


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
    assert shops and all(s.platform in live.PLATFORMS for s in shops)
    assert all(s.queries for s in shops)


def test_monitor_ignores_misses_on_partial_polls(tmp_path, monkeypatch):
    ttb = Listing("Pokémon 30 Jahre Top-Trainer-Box Deutsch", "https://shop.de/products/ttb", True)
    monitor, shop = _monitor(tmp_path, monkeypatch, [[ttb]])
    monitor.check(shop, None)

    monkeypatch.setattr(live, "poll_shop", lambda *args: ([], False))
    for _ in range(live.MISSES_UNTIL_GONE + 1):
        monitor.check(shop, None)
    assert monitor.products[ttb.url]["available"] is True


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Pokémon 30 Jahre Top-Trainer-Box Deutsch", False),
        ("Pokémon 30 Jahre Top-Trainer-Box Deutsch (Vorbestellung)", True),
        ("PREORDER Pokémon 30th Celebration Display M6a Japanisch", True),
        ("Pokemon 30 Jahre Ordner Kollektion DE ab 16.10.2026", True),
        ("30 Jahre Booster Bundle Release 02.10.2026", False),  # date passed, it's out
        ("30th Celebration Card Set 予約", True),
    ],
)
def test_looks_preorder(text, expected):
    assert catalog.looks_preorder(text, today=date(2026, 10, 3)) is expected


def test_parsers_flag_preorders():
    suggest = {"resources": {"results": {"products": [
        {"title": "Pokémon 30 Jahre Top-Trainer-Box", "url": "/products/a", "available": True, "tags": ["Vorbestellung"]},
        {"title": "Pokémon 30 Jahre Poster-Kollektion", "url": "/products/b", "available": True, "tags": "Neuheit, Pokemon"},
    ]}}}
    flags = {l.url[-1]: l.preorder for l in parse_shopify_suggest(suggest, "https://s.de")}
    assert flags == {"a": True, "b": False}

    collection = {"products": [{"title": "30th Celebration Display JP", "handle": "d", "tags": [],
                                "variants": [{"title": "Pre-Order", "available": True}]}]}
    assert parse_shopify_collection(collection, "https://s.de")[0].preorder is True

    woo = [{"name": "30 Jahre Booster Bundle", "permalink": "https://w.de/p/", "is_in_stock": True,
            "is_on_backorder": False, "stock_availability": {"class": "available-on-backorder"}}]
    assert parse_woocommerce(woo, "https://w.de")[0].preorder is True

    html = """<script type="application/ld+json">{"@type": "Product", "name": "Pokémon 30th Celebration Display JP",
      "offers": {"availability": "https://schema.org/PreOrder"}}</script>"""
    [listing] = parse_html(html, "https://h.de/p", accept, PATTERNS)
    assert listing.available is True and listing.preorder is True


def test_monitor_hides_preorders_unless_enabled(tmp_path, monkeypatch):
    rounds = [[
        Listing("Pokémon 30 Jahre Top-Trainer-Box Deutsch", "https://shop.de/products/ttb", True, preorder=True),
        Listing("Pokémon 30 Jahre Ordner-Kollektion DE ab 24.12.2099", "https://shop.de/products/ordner", True),
        Listing("Pokémon 30 Jahre Poster-Kollektion Deutsch", "https://shop.de/products/poster", True),
    ]]
    monitor, shop = _monitor(tmp_path, monkeypatch, rounds * 2)
    assert [p["url"] for p in monitor.check(shop, None)] == ["https://shop.de/products/poster"]
    assert monitor.products["https://shop.de/products/ttb"]["preorder"] is True

    monitor.settings.include_preorders = True
    assert len(monitor.check(shop, None)) == 2


def test_window_serves_page_and_state(tmp_path, monkeypatch):
    monitor, shop = _monitor(tmp_path, monkeypatch, [[Listing("30th Celebration Display JP", "https://shop.de/d", True)]])
    monitor.check(shop, None)
    url = ui.serve(monitor.snapshot, 18765)
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert b"30 Jahre Radar" in resp.read()
    with urllib.request.urlopen(url + "api/state", timeout=5) as resp:
        state = json.load(resp)
    assert state["products"][0]["url"] == "https://shop.de/d"
    assert state["shops"]["Testshop"]["status"] == "ok"
    # A second server falls back to the next free port instead of crashing.
    assert ui.serve(monitor.snapshot, int(url.rsplit(":", 1)[1].strip("/"))) != url


def test_shipped_config_hides_preorders_and_small_shops():
    settings, shops = load_config(ROOT / "config" / "live_shops.yaml")
    assert settings.include_preorders is False
    on = {s.name for s in shops if s.enabled}
    assert {"cardcosmos", "Card-Corner", "TRADER"} <= on
    assert "Rot der Sammler" not in on
