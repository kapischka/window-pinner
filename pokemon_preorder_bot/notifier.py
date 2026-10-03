from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def setup_logging(log_path: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def _notify_discord(title: str, message: str) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        import requests

        resp = requests.post(webhook_url, json={"content": f"**{title}**\n{message}"}, timeout=10)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - Discord delivery is best-effort, never fatal
        logger.warning("Discord notification failed (%s); alert was still logged above", exc)


def _notify_telegram(title: str, message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import requests

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{title}\n{message}", "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - Telegram delivery is best-effort, never fatal
        logger.warning("Telegram notification failed (%s); alert was still logged above", exc)


def _notify_macos(title: str, message: str) -> None:
    # plyer's macOS backend needs pyobjus, which rarely installs cleanly;
    # osascript ships with every Mac. JSON string literals are valid
    # AppleScript strings, quotes and newlines included.
    def quote(text: str) -> str:
        return json.dumps(text, ensure_ascii=False)

    script = f'display notification {quote(message)} with title {quote(title)} sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)


def notify(title: str, message: str) -> None:
    logger.info("ALERT: %s - %s", title, message)
    try:
        if sys.platform == "darwin":
            _notify_macos(title, message)
        else:
            from plyer import notification

            notification.notify(title=title, message=message, timeout=15)
    except Exception as exc:  # noqa: BLE001 - desktop notifications are best-effort
        logger.warning("desktop notification failed (%s); alert was still logged above", exc)
    _notify_discord(title, message)
    _notify_telegram(title, message)
