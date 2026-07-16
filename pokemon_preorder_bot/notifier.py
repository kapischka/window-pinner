from __future__ import annotations

import logging
import os

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


def notify(title: str, message: str) -> None:
    logger.info("ALERT: %s - %s", title, message)
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=15)
    except Exception as exc:  # noqa: BLE001 - desktop notifications are best-effort
        logger.warning("desktop notification failed (%s); alert was still logged above", exc)
    _notify_discord(title, message)
