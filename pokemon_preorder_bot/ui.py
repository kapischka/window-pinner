"""The live monitor's own window: a tiny local web server serving one page
(ui.html) plus the monitor's state as JSON, opened once as an app window.

Products are never opened automatically. The page lists them and opens a
shop only when you click it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("ui.html")
PORT_ATTEMPTS = 10
MAX_BODY = 4096

# Chromium browsers can open a page as a standalone app window without tabs
# or address bar. Tried in order, the default browser is the fallback.
_MAC_APP_BROWSERS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium")
_LINUX_APP_BROWSERS = ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser")


def _handler(snapshot: Callable[[], dict], actions: dict[str, Callable[[dict], dict]]):
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", page)
            elif path == "/api/state":
                self._send(200, "application/json", json.dumps(snapshot(), ensure_ascii=False).encode())
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):  # noqa: N802 - http.server API
            action = actions.get(self.path.split("?", 1)[0].removeprefix("/api/"))
            # Requiring a JSON content type keeps other websites from
            # triggering actions: a cross-site form can't send one.
            if action is None or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._send(404, "text/plain", b"not found")
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON object expected")
                result = action(payload)
            except (ValueError, TypeError) as exc:
                self._send(400, "application/json", json.dumps({"error": str(exc)}).encode())
                return
            self._send(200, "application/json", json.dumps(result).encode())

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # the page polls every second, keep the terminal quiet
            pass

    return Handler


def serve(snapshot: Callable[[], dict], port: int, actions: dict[str, Callable[[dict], dict]] | None = None) -> str:
    """Starts the server in a background thread and returns its URL. Moves
    to the next port if the requested one is taken. `actions` are exposed
    as POST /api/<name> taking and returning JSON."""
    handler = _handler(snapshot, actions or {})
    for candidate in range(port, port + PORT_ATTEMPTS):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
        except OSError:
            continue
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, name="ui", daemon=True).start()
        return f"http://127.0.0.1:{candidate}/"
    raise OSError(f"no free port between {port} and {port + PORT_ATTEMPTS - 1}")


def open_window(url: str) -> None:
    """Opens the page once, as an app window where possible."""
    try:
        if sys.platform == "darwin":
            for browser in _MAC_APP_BROWSERS:
                if Path(f"/Applications/{browser}.app").exists():
                    subprocess.Popen(["open", "-na", browser, "--args", f"--app={url}", "--window-size=1180,860"])
                    return
        elif sys.platform.startswith("linux"):
            for browser in _LINUX_APP_BROWSERS:
                if shutil.which(browser):
                    subprocess.Popen([browser, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
    except OSError as exc:
        logger.debug("app window failed (%s), using the default browser", exc)
    webbrowser.open(url, new=1)
