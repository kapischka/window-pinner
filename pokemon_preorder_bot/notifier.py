from __future__ import annotations

import logging

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


def notify(title: str, message: str) -> None:
    logger.info("ALERT: %s - %s", title, message)
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=15)
    except Exception as exc:  # noqa: BLE001 - desktop notifications are best-effort
        logger.warning("desktop notification failed (%s); alert was still logged above", exc)
