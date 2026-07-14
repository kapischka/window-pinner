from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    pass


def fetch_static(url: str, timeout: int = 20, retries: int = 2) -> str:
    """Plain HTTP GET for server-rendered pages."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("static fetch failed (attempt %d) for %s: %s", attempt + 1, url, exc)
            time.sleep(2 * (attempt + 1))
    raise FetchError(f"static fetch failed for {url}: {last_error}")


def fetch_rendered(url: str, wait_ms: int = 4000, timeout_ms: int = 30000) -> str:
    """Load a page in headless Chromium for JS-driven storefronts."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "playwright is not installed; run `pip install playwright && "
            "playwright install chromium`"
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                    locale="en-US",
                )
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(wait_ms)
                return page.content()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - surface any playwright error uniformly
        raise FetchError(f"rendered fetch failed for {url}: {exc}") from exc


def fetch(url: str, render: bool) -> str:
    return fetch_rendered(url) if render else fetch_static(url)
