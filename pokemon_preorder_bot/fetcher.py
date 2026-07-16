from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Cookie-consent/region-interstitial buttons that would otherwise sit on top
# of the real content on a first visit (no cookies carried between runs).
# Best-effort: clicked if found within a couple seconds, ignored otherwise -
# a missed banner just means the existing text heuristics have to work
# around it, same as before this existed.
#
# Specific phrases are safe to click anywhere on the page - they're
# unambiguous consent-dialog wording, unlikely to appear as an unrelated
# button elsewhere.
_SPECIFIC_CONSENT_TEXTS = [
    "Accept All", "Accept all", "I Agree",
    "Alle akzeptieren", "Akzeptieren", "Ich stimme zu", "Zustimmen",
    "すべて同意する", "同意する",
]

# Generic phrases (also covers region-selector splash screens - Pokémon
# Center shows a "Choose Your Region" interstitial on a first visit) are
# common enough elsewhere on a normal page (e.g. a "Continue shopping"
# button) that clicking one blindly risks navigating away from the page we
# actually want to read. Only clicked if found *inside* something that looks
# like a cookie/region overlay container - never a bare page-wide search.
# Deliberately no country names here either, since clicking the wrong one
# would silently show the wrong region's stock/pricing rather than just
# being a missed dismissal.
_OVERLAY_ONLY_TEXTS = [
    "Accept", "Agree", "Continue", "Confirm", "Stay on this site",
    "Weiter", "Bestätigen", "Auf dieser Seite bleiben",
    "続ける", "確認",
]

_OVERLAY_CONTAINER_SELECTORS = [
    '[class*="cookie" i]', '[id*="cookie" i]',
    '[class*="consent" i]', '[id*="consent" i]',
    '[class*="gdpr" i]',
    '[class*="region-select" i]', '[class*="locale-select" i]',
    '[class*="country-select" i]',
    '[role="dialog"]',
]

# schema.org JSON-LD availability aside, locale mainly affects which
# language/currency a storefront renders in for anonymous sessions - matters
# for the DE/JP targets specifically, since a hardcoded en-US locale could
# make a German or Japanese site render US pricing/availability instead of
# the region actually being watched.
_LOCALE_HINTS = [
    (("pokemoncenter-online.com", ".co.jp", ".jp/"), ("ja-JP", "ja-JP,ja;q=0.9,en;q=0.5")),
    ((".de/", ".de?", "/de-de/", "/de/"), ("de-DE", "de-DE,de;q=0.9,en;q=0.5")),
]
_DEFAULT_LOCALE = ("en-US", "en-US,en;q=0.9,de;q=0.8")


def _locale_for_url(url: str) -> tuple[str, str]:
    host_and_path = urlparse(url).netloc + urlparse(url).path
    for needles, locale in _LOCALE_HINTS:
        if any(n in host_and_path or n in url for n in needles):
            return locale
    return _DEFAULT_LOCALE


class FetchError(Exception):
    pass


def fetch_static(url: str, timeout: int = 20, retries: int = 2) -> str:
    """Plain HTTP GET for server-rendered pages."""
    _, accept_language = _locale_for_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": accept_language}
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


@contextmanager
def playwright_browser():
    """One Chromium instance shared across every rendered fetch in a run,
    instead of launching a fresh browser per target - launching is by far
    the slowest part of a rendered fetch, and with an always-on auto-check
    loop that cost is paid repeatedly rather than once."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "playwright is not installed; run `pip install playwright && playwright install chromium`"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def _dismiss_consent_banner(page) -> None:
    for text in _SPECIFIC_CONSENT_TEXTS:
        try:
            button = page.get_by_text(text, exact=False).first
            if button.is_visible(timeout=500):
                button.click(timeout=1000)
                page.wait_for_timeout(300)
                return
        except Exception:  # noqa: BLE001 - purely best-effort, never fatal
            continue

    for selector in _OVERLAY_CONTAINER_SELECTORS:
        try:
            container = page.locator(selector).first
            if not container.is_visible(timeout=300):
                continue
        except Exception:  # noqa: BLE001
            continue
        for text in _OVERLAY_ONLY_TEXTS:
            try:
                button = container.get_by_text(text, exact=False).first
                if button.is_visible(timeout=300):
                    button.click(timeout=1000)
                    page.wait_for_timeout(300)
                    return
            except Exception:  # noqa: BLE001
                continue


def _fetch_rendered_once(browser, url: str, wait_ms: int, timeout_ms: int) -> str:
    locale, accept_language = _locale_for_url(url)
    page = browser.new_page(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale=locale,
        extra_http_headers={"Accept-Language": accept_language},
    )
    # `navigator.webdriver` is the single most common tell-tale automated
    # browsers leave behind - some bot-detection challenges (Cloudflare,
    # PerimeterX) specifically probe for it and serve a permanent block/
    # CAPTCHA to sessions that fail this check, regardless of anything else
    # about the request. Hiding it doesn't guarantee passing those checks,
    # but leaving it as-is guarantees failing the ones that look for it.
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    try:
        response = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} {response.status_text}".strip())
        _dismiss_consent_banner(page)
        page.wait_for_timeout(wait_ms)
        # Nudge lazy-loaded/infinite-scroll product grids into loading
        # before we read the DOM - two passes since a single scroll often
        # only triggers the *next* batch's loading spinner, not its content.
        for _ in range(2):
            try:
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001 - best-effort only
                break
        return page.content()
    finally:
        page.close()


def fetch_rendered(url: str, browser=None, wait_ms: int = 4000, timeout_ms: int = 30000, retries: int = 1) -> str:
    """Load a page in headless Chromium for JS-driven storefronts.

    Pass an already-open `browser` (from `playwright_browser()`) to reuse it
    across multiple calls; omit it to launch a one-off browser for this call
    only (used by direct/manual calls and tests).
    """
    if browser is not None:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return _fetch_rendered_once(browser, url, wait_ms, timeout_ms)
            except Exception as exc:  # noqa: BLE001 - surface any playwright error uniformly
                last_error = exc
                logger.warning("rendered fetch failed (attempt %d) for %s: %s", attempt + 1, url, exc)
        raise FetchError(f"rendered fetch failed for {url}: {last_error}")

    with playwright_browser() as owned_browser:
        return fetch_rendered(url, browser=owned_browser, wait_ms=wait_ms, timeout_ms=timeout_ms, retries=retries)


def fetch(url: str, render: bool, browser=None) -> str:
    return fetch_rendered(url, browser=browser) if render else fetch_static(url)
