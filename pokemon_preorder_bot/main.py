from __future__ import annotations

import argparse
import contextlib
import json
import logging
import random
import time
from pathlib import Path

from .config import load_targets
from .fetcher import FetchError, fetch, playwright_browser
from .matcher import STATUSES_ACTIONABLE, looks_blocked, match_product_page, match_search_page
from .notifier import notify, setup_logging
from .state import StateStore

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "targets.yaml"
DEFAULT_STATE = ROOT / "data" / "state.json"
DEFAULT_LOG = ROOT / "data" / "bot.log"
DEFAULT_RESULTS = ROOT / "data" / "latest_results.json"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pokémon 30th Anniversary preorder watcher")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument(
        "--delay", type=float, default=3.0, help="Base seconds to wait between checking each site (jittered)"
    )
    parser.add_argument(
        "--test-notify", action="store_true", help="Send a test desktop notification and exit"
    )
    return parser.parse_args(argv)


def _notify_for_status(retailer: str, url: str, label: str, status: str) -> None:
    readable = status.replace("_", " ")
    notify(f"{readable.title()}: {retailer}", f"{label} looks like a {readable} on {retailer}\n{url}")


def run(args: argparse.Namespace) -> None:
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    setup_logging(args.log)

    if args.test_notify:
        notify("Pokémon Preorder Bot", "Test notification - notifications are working.")
        return

    targets = load_targets(args.config)
    state = StateStore(args.state)
    summary = []

    needs_browser = any(t.enabled and t.render for t in targets)
    browser_ctx = playwright_browser() if needs_browser else contextlib.nullcontext(None)

    with browser_ctx as browser:
        for target in targets:
            if not target.enabled:
                summary.append(
                    {"target": target.name, "retailer": target.retailer, "url": target.url, "status": "disabled", "detail": "target disabled in config"}
                )
                continue

            logger.info("Checking %s (%s)", target.name, target.url)
            try:
                html = fetch(target.url, target.render, browser=browser)
            except FetchError as exc:
                logger.error("Failed to fetch %s: %s", target.name, exc)
                summary.append(
                    {"target": target.name, "retailer": target.retailer, "url": target.url, "status": "error", "detail": str(exc)}
                )
                time.sleep(args.delay + random.uniform(0, args.delay))
                continue

            if looks_blocked(html):
                logger.warning("%s looks like a bot-detection/CAPTCHA page, skipping", target.name)
                summary.append(
                    {"target": target.name, "retailer": target.retailer, "url": target.url, "status": "blocked", "detail": "page looks like a bot-detection/CAPTCHA challenge, not real content"}
                )
                time.sleep(args.delay + random.uniform(0, args.delay))
                continue

            if target.type == "search_page":
                matches = match_search_page(
                    html,
                    target.keywords,
                    target.product_keywords,
                    target.preorder_patterns,
                    target.in_stock_patterns,
                    target.unavailable_patterns,
                )
                if not matches:
                    summary.append(
                        {"target": target.name, "retailer": target.retailer, "url": target.url, "status": "no_match", "detail": "no matching product keywords found on page"}
                    )
                for m in matches:
                    prev = state.last_status(target.id, m.keyword)
                    if m.status in STATUSES_ACTIONABLE and prev not in STATUSES_ACTIONABLE:
                        _notify_for_status(target.retailer, target.url, m.keyword, m.status)
                    state.set_status(target.id, m.keyword, m.status)
                    summary.append(
                        {"target": target.name, "retailer": target.retailer, "url": target.url, "keyword": m.keyword, "status": m.status, "detail": m.snippet}
                    )
            else:  # product_page
                m = match_product_page(
                    html, target.name, target.keywords, target.preorder_patterns, target.in_stock_patterns, target.unavailable_patterns
                )
                prev = state.last_status(target.id, m.keyword)
                if m.status in STATUSES_ACTIONABLE and prev not in STATUSES_ACTIONABLE:
                    _notify_for_status(target.retailer, target.url, target.name, m.status)
                state.set_status(target.id, m.keyword, m.status)
                summary.append(
                    {"target": target.name, "retailer": target.retailer, "url": target.url, "status": m.status, "detail": m.snippet}
                )

            # Jittered delay: politer on retailers' servers and less
            # fingerprintable than a perfectly uniform interval.
            time.sleep(args.delay + random.uniform(0, args.delay))

    state.save()
    Path(args.results).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nChecked {len(targets)} site(s). Results written to {args.results}\n")
    for row in summary:
        keyword = row.get("keyword", "")
        print(f"[{row['status']:11}] {row['retailer']:20} {keyword}")


def main(argv=None) -> None:
    args = parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
