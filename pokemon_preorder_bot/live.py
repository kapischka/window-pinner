"""Live monitor: polls German shops every few seconds for 30th anniversary
products (German or Japanese edition) and alerts the moment one becomes
orderable.

    python -m pokemon_preorder_bot.live            # run until Ctrl+C, opens its window
    python -m pokemon_preorder_bot.live --once     # one pass, print a table
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

from . import catalog, pricing, ui
from .main import ROOT
from .notifier import notify, setup_logging
from .sources import Listing, PollError, Shop, StatusPatterns, new_session, poll_shop

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = ROOT / "config" / "live_shops.yaml"
DEFAULT_STATE = ROOT / "data" / "live_state.json"
DEFAULT_RESULTS = ROOT / "data" / "live_results.json"
DEFAULT_LOG = ROOT / "data" / "live.log"

MIN_INTERVAL = 3.0  # hard floor per shop, anything faster just gets an IP banned
MAX_BACKOFF = 300.0
JITTER = 0.2  # +-20 % so requests don't hit at a fingerprintable fixed rhythm
MAX_ITEMS_PER_ALERT = 5
# Polls in a row a known in-stock product may be missing from a shop's
# results before it counts as gone. Many shops hide sold-out items from
# search, so without this a product would stay "available" forever and
# never alert again when it returns; one miss alone is often just ranking
# noise in a capped result list.
MISSES_UNTIL_GONE = 3
PLATFORMS = {"shopify", "woocommerce", "html", "ebay", "auto"}
MARKUP_CHOICES = (10, 25, 50, 100, None)  # offered in the window, None = no limit


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Settings:
    def __init__(self, raw: dict):
        self.interval = max(float(raw.get("interval_seconds", 10)), MIN_INTERVAL)
        self.languages = {lang.upper() for lang in raw.get("languages", ["DE", "JP"])}
        self.include_unknown_language = bool(raw.get("include_unknown_language", True))
        self.include_preorders = bool(raw.get("include_preorders", False))
        self.queries = list(raw.get("queries", []))
        self.exclude = list(raw.get("exclude", []))
        patterns = raw.get("status_patterns", {})
        self.patterns = StatusPatterns(
            preorder=patterns.get("preorder", []),
            in_stock=patterns.get("in_stock", []),
            unavailable=patterns.get("unavailable", []),
        )
        self.max_markup: float | None = None
        self.min_markup = -50.0
        self.references: list[pricing.Reference] = []

    def load_prices(self, raw: dict) -> None:
        limit = raw.get("max_markup_percent", 50)
        self.max_markup = None if limit is None else float(limit)
        # Far below UVP is a single promo card, an empty box or a fake, not a deal.
        self.min_markup = float(raw.get("min_markup_percent", -50))
        self.references = pricing.load_references(raw.get("products", []))


def load_config(path: str | Path) -> tuple[Settings, list[Shop]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    settings = Settings(raw.get("settings", {}))
    settings.load_prices(raw.get("prices", {}))
    shops = []
    for entry in raw.get("shops", []):
        entry = dict(entry)
        entry.setdefault("queries", settings.queries)
        entry["language"] = str(entry.get("language", catalog.UNKNOWN_LANGUAGE)).upper()
        try:
            shop = Shop(**entry)
        except TypeError as exc:
            raise ValueError(f"Shop {entry.get('name', '?')!r} in {path}: {exc}") from exc
        if shop.platform not in PLATFORMS:
            raise ValueError(f"Shop {shop.name!r}: platform muss eins von {sorted(PLATFORMS)} sein")
        if shop.platform == "html" and not (shop.pages or shop.search_url):
            raise ValueError(f"Shop {shop.name!r}: html braucht pages oder search_url")
        if shop.platform == "ebay" and not shop.queries:
            raise ValueError(f"Shop {shop.name!r}: ebay braucht queries")
        if any(s.name == shop.name for s in shops):
            raise ValueError(f"Shop-Name {shop.name!r} ist doppelt")
        shops.append(shop)
    return settings, shops


class LiveMonitor:
    def __init__(
        self,
        settings: Settings,
        shops: list[Shop],
        state_path: Path,
        results_path: Path,
        persist_state: bool = True,
    ):
        self.settings = settings
        self.shops = [s for s in shops if s.enabled]
        self.state_path = state_path
        self.results_path = results_path
        self.persist_state = persist_state
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.settings_path = state_path.with_name("live_settings.json")
        self._load_overrides()
        self.products: dict[str, dict] = self._load_state()
        self.shop_status: dict[str, dict] = {
            s.name: {"status": "pending", "detail": "", "last_check": None, "url": s.url} for s in self.shops
        }

    # --- filtering --------------------------------------------------------

    def wanted(self, shop: Shop, listing: Listing) -> str | None:
        """The listing's language if it's a product we watch, else None."""
        if not catalog.is_30th(listing.title, listing.context, shop.assume_pokemon):
            return None
        if catalog.is_excluded(listing.title, self.settings.exclude + shop.exclude):
            return None
        lang = catalog.language(listing.title, default=shop.language)
        if lang in self.settings.languages:
            return lang
        if lang == catalog.UNKNOWN_LANGUAGE and self.settings.include_unknown_language:
            return lang
        return None

    def _accept_title(self, shop: Shop):
        return lambda title: catalog.is_30th(title, assume_pokemon=shop.assume_pokemon)

    # --- persistence ------------------------------------------------------

    def _load_state(self) -> dict[str, dict]:
        try:
            products = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        for record in products.values():
            # State written by older versions: price as text, no stock/markup fields.
            if isinstance(record.get("price"), str):
                record["price"] = pricing.parse_price(record["price"])
            record.setdefault("in_stock", record.get("available", False))
            self._evaluate(record)
        return products

    def _load_overrides(self) -> None:
        """Settings changed in the window survive a restart."""
        try:
            overrides = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if "max_markup" in overrides:
            self.settings.max_markup = overrides["max_markup"]

    # --- prices -----------------------------------------------------------

    def _evaluate(self, record: dict) -> None:
        """Derives "available" (shown and alerted) from the raw stock state
        and the current markup limit."""
        limit = self.settings.max_markup
        markup = record.get("markup")
        record["too_expensive"] = bool(limit is not None and markup is not None and markup > limit)
        record["available"] = bool(record.get("in_stock")) and not record["too_expensive"]

    def set_max_markup(self, value: float | None) -> None:
        """Changes the limit from the window and re-sorts every known
        listing at once. No alerts: the user just looked."""
        now = _now()
        with self.lock:
            self.settings.max_markup = value
            for record in self.products.values():
                before = record.get("available")
                self._evaluate(record)
                if record["available"] != before:
                    record["since"] = now
            self._write_json(self.settings_path, {"max_markup": value})
            self._save()
        logger.info("Max. Aufpreis: %s", "egal" if value is None else f"{value:g} %")

    def toggle_pause(self) -> bool:
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()
        logger.info("Pausiert" if self.paused.is_set() else "Läuft weiter")
        return self.paused.is_set()

    def _cheapest_per_product(self, candidates: list) -> list:
        """Marketplaces list the same box dozens of times; keep the cheapest
        orderable offer per known product."""
        best: dict[str, tuple] = {}
        for candidate in candidates:
            listing, _, ref = candidate
            if not listing.available or listing.price is None or ref is None:
                continue
            current = best.get(ref.name)
            if current is None or listing.price < current[0].price:
                best[ref.name] = candidate
        return list(best.values())

    def _write_json(self, path: Path, data) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            # Windows refuses the replace while the dashboard is reading the
            # file; the next poll writes it again a few seconds later.
            logger.debug("could not write %s: %s", path, exc)

    def _snapshot_unlocked(self) -> dict:
        active = {s.name for s in self.shops}
        products = sorted(
            (dict(p) for p in self.products.values() if p["shop"] in active),
            key=lambda p: (not p["available"], p["since"]),
        )
        return {
            "updated": _now(),
            "paused": self.paused.is_set(),
            "max_markup": self.settings.max_markup,
            "markup_choices": list(MARKUP_CHOICES),
            "interval_seconds": self.settings.interval,
            "languages": sorted(self.settings.languages),
            "shops": {name: dict(status) for name, status in self.shop_status.items()},
            "products": products,
        }

    def snapshot(self) -> dict:
        """Current state for the window, safe to call from any thread."""
        with self.lock:
            return self._snapshot_unlocked()

    def _save(self) -> None:
        """Caller holds self.lock."""
        if self.persist_state:
            self._write_json(self.state_path, self.products)
        self._write_json(self.results_path, self._snapshot_unlocked())

    # --- polling ----------------------------------------------------------

    def check(self, shop: Shop, session) -> list[dict]:
        """Polls one shop, records the result and returns the products that
        just became orderable."""
        try:
            try:
                listings, complete = poll_shop(session, shop, self._accept_title(shop), self.settings.patterns)
            except PollError:
                raise
            except Exception as exc:  # noqa: BLE001 - e.g. a shop answering with an unexpected JSON shape
                raise PollError(f"{type(exc).__name__}: {exc}") from exc
        except PollError as exc:
            with self.lock:
                self.shop_status[shop.name].update(
                    status="blocked" if exc.blocked else "error", detail=str(exc), last_check=_now()
                )
                self._save()
            raise

        now = _now()
        fresh: list[dict] = []
        with self.lock:
            candidates = []
            for listing in listings:
                lang = self.wanted(shop, listing)
                if lang is None:
                    continue
                ref = pricing.find_reference(listing.title, lang, self.settings.references)
                if ref is None and shop.require_reference:
                    continue
                markup = pricing.markup_percent(listing.price, ref)
                if markup is not None and markup < self.settings.min_markup:
                    continue
                candidates.append((listing, lang, ref))
            if shop.best_per_product:
                candidates = self._cheapest_per_product(candidates)

            seen: set[str] = set()
            for listing, lang, ref in candidates:
                seen.add(listing.url)
                prev = self.products.get(listing.url, {})
                was_available = prev.get("available", False)
                preorder = listing.preorder or catalog.looks_preorder(listing.title)
                if listing.available is None:
                    in_stock = prev.get("in_stock", False)
                else:
                    # In stock means: can be bought and shipped now.
                    in_stock = listing.available and (self.settings.include_preorders or not preorder)
                price = listing.price if listing.price is not None else prev.get("price")
                record = {
                    "shop": shop.name,
                    "title": listing.title,
                    "url": listing.url,
                    "price": price,
                    "language": lang,
                    "in_stock": in_stock,
                    "preorder": preorder,
                    "reference": {"name": ref.name, "kind": ref.kind, "price": ref.price} if ref else None,
                    "markup": pricing.markup_percent(price, ref),
                    "last_seen": now,
                    "misses": 0,
                }
                self._evaluate(record)
                available = record["available"]
                record["since"] = prev.get("since", now) if prev and available == was_available else now
                self.products[listing.url] = record
                if available and not was_available:
                    fresh.append(record)
                elif was_available and not available:
                    reason = "zu teuer" if record["too_expensive"] else "weg"
                    logger.info("🔴 %s: [%s] %s bei %s", reason, lang, listing.title, shop.name)
            for url, record in self.products.items():
                if not complete or record["shop"] != shop.name or url in seen or not record.get("in_stock"):
                    continue
                record["misses"] = record.get("misses", 0) + 1
                if record["misses"] >= MISSES_UNTIL_GONE:
                    record["in_stock"] = False
                    self._evaluate(record)
                    record["since"] = now
                    logger.info("🔴 weg (nicht mehr gelistet): [%s] %s bei %s", record["language"], record["title"], shop.name)
            self.shop_status[shop.name].update(status="ok", detail=f"{len(seen)} Treffer", last_check=now)
            self._save()
        return fresh

    @staticmethod
    def describe_price(record: dict) -> str:
        """'54,99 € (+0 % UVP)' or just the price when no reference is known."""
        text = pricing.format_euro(record.get("price"))
        if record.get("markup") is not None and record.get("reference"):
            text += f" ({record['markup']:+.0f} % {record['reference']['kind']})"
        return text

    def alert(self, shop: Shop, fresh: list[dict]) -> None:
        for p in fresh:
            logger.info("🟢 VERFÜGBAR: [%s] %s %s bei %s → %s", p["language"], p["title"], self.describe_price(p), shop.name, p["url"])
        lines = [f"[{p['language']}] {p['title']} {self.describe_price(p)}".strip() + f"\n{p['url']}" for p in fresh[:MAX_ITEMS_PER_ALERT]]
        if len(fresh) > MAX_ITEMS_PER_ALERT:
            lines.append(f"… und {len(fresh) - MAX_ITEMS_PER_ALERT} weitere")
        title = f"🟢 {shop.name}: {fresh[0]['title']}" if len(fresh) == 1 else f"🟢 {shop.name}: {len(fresh)} Produkte verfügbar"
        print("\a", end="", flush=True)
        notify(title, "\n".join(lines))

    def _shop_loop(self, shop: Shop) -> None:
        session = new_session()
        interval = max(shop.interval or self.settings.interval, MIN_INTERVAL)
        delay = random.uniform(0, interval)  # stagger shops so they don't all fire at once
        while not self.stop.wait(delay):
            if self.paused.is_set():
                delay = 1
                continue
            try:
                fresh = self.check(shop, session)
                if fresh:
                    self.alert(shop, fresh)
                delay = interval
            except PollError as exc:
                delay = min(max(delay * 2, interval * 2), MAX_BACKOFF)
                if exc.retry_after:
                    delay = max(delay, min(exc.retry_after, MAX_BACKOFF))
                logger.warning("%s: %s - nächster Versuch in %.0f s", shop.name, exc, delay)
                continue
            except Exception:  # noqa: BLE001 - one broken shop must never stop the others
                logger.exception("%s: unexpected error", shop.name)
                delay = MAX_BACKOFF
                continue
            delay *= random.uniform(1 - JITTER, 1 + JITTER)

    def run_forever(self, port: int | None = None, open_window: bool = True) -> None:
        if port is not None:
            try:
                url = ui.serve(self.snapshot, port, actions={"pause": self._action_pause, "settings": self._action_settings})
            except OSError as exc:
                logger.warning("Fenster nicht verfügbar: %s", exc)
            else:
                logger.info("Fenster: %s", url)
                if open_window:
                    ui.open_window(url)
        logger.info(
            "Live-Monitor gestartet: %d Shops, alle %.0f s, Sprachen %s",
            len(self.shops),
            self.settings.interval,
            "/".join(sorted(self.settings.languages)),
        )
        threads = [threading.Thread(target=self._shop_loop, args=(s,), name=s.name, daemon=True) for s in self.shops]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Beende …")
            self.stop.set()

    def _action_pause(self, _payload: dict) -> dict:
        return {"paused": self.toggle_pause()}

    def _action_settings(self, payload: dict) -> dict:
        if "max_markup" in payload:
            value = payload["max_markup"]
            if value is not None and not isinstance(value, (int, float)):
                raise ValueError("max_markup muss eine Zahl oder null sein")
            self.set_max_markup(None if value is None else float(value))
        return {"max_markup": self.settings.max_markup}

    def run_once(self) -> None:
        def one(shop: Shop) -> None:
            try:
                self.check(shop, new_session())
            except PollError:
                pass

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(one, self.shops))
        self.print_table()

    def print_table(self) -> None:
        print()
        for name, st in sorted(self.shop_status.items()):
            print(f"  {st['status']:8} {name:24} {st['detail']}")
        print()
        available = [p for p in self.products.values() if p["available"] and p["shop"] in self.shop_status]
        if not available:
            print("Gerade nichts verfügbar.")
        for p in sorted(available, key=lambda p: (p["language"], p["title"])):
            print(f"🟢 [{p['language']}] {p['title']}  {self.describe_price(p)}  ({p['shop']})\n   {p['url']}")
        expensive = sum(1 for p in self.products.values() if p.get("too_expensive") and p["shop"] in self.shop_status)
        if expensive:
            print(f"\n{expensive} weitere lieferbar, aber mehr als {self.settings.max_markup:g} % über UVP.")


def running_instance(port: int) -> str | None:
    """URL of a radar already running on this port, so a second double
    click opens its window instead of polling every shop twice."""
    url = f"http://127.0.0.1:{port}/"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "api/state", timeout=1) as resp:
            if "markup_choices" in json.load(resp):
                return url
    except (OSError, ValueError):
        pass
    return None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live-Monitor für Pokémon 30 Jahre / 30th Celebration Produkte")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--interval", type=float, help=f"Sekunden zwischen zwei Abfragen pro Shop (min. {MIN_INTERVAL:.0f})")
    parser.add_argument("--languages", help="z. B. DE,JP (Standard aus der Config)")
    parser.add_argument("--once", action="store_true", help="Jeden Shop einmal abfragen, Tabelle ausgeben, beenden")
    parser.add_argument("--port", type=int, default=8765, help="Port des Fensters (Standard 8765)")
    parser.add_argument("--no-window", action="store_true", help="Fenster nicht automatisch öffnen")
    parser.add_argument("--no-ui", action="store_true", help="Ganz ohne Fenster, nur Terminal und Benachrichtigungen")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if not args.once and not args.no_ui:
        url = running_instance(args.port)
        if url:
            print(f"Das Radar läuft schon, öffne das Fenster: {url}")
            ui.open_window(url)
            return
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    setup_logging(args.log)
    settings, shops = load_config(args.config)
    if args.interval:
        settings.interval = max(args.interval, MIN_INTERVAL)
    if args.languages:
        settings.languages = {lang.strip().upper() for lang in args.languages.split(",") if lang.strip()}
    # --once is a look, not a baseline: it must not swallow the first alert
    # of the live run that follows.
    monitor = LiveMonitor(settings, shops, Path(args.state), Path(args.results), persist_state=not args.once)
    if args.once:
        monitor.run_once()
    else:
        monitor.run_forever(port=None if args.no_ui else args.port, open_window=not args.no_window)


if __name__ == "__main__":
    main()
