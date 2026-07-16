from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

from pokemon_preorder_bot.config import Target, load_defaults, load_targets, save_targets
from pokemon_preorder_bot.main import DEFAULT_CONFIG, DEFAULT_LOG, DEFAULT_RESULTS, DEFAULT_STATE
from pokemon_preorder_bot.main import parse_args, run
from pokemon_preorder_bot.notifier import notify, setup_logging

app = Flask(__name__)
app.secret_key = "pokemon-preorder-bot-local-dashboard"  # local tool only, not internet-facing

CONFIG_PATH = DEFAULT_CONFIG
RESULTS_PATH = DEFAULT_RESULTS
LOG_PATH = DEFAULT_LOG
STATE_PATH = DEFAULT_STATE

MIN_AUTO_INTERVAL_MINUTES = 5
DEFAULT_AUTO_INTERVAL_MINUTES = 20

logger = logging.getLogger(__name__)


class AutoChecker:
    """Runs a check on a timer for as long as this process is alive, so the
    dashboard doesn't just sit there between manual clicks. A single lock
    also serializes it against the manual "Run check now" button, so the two
    triggers can never run() concurrently and corrupt state.json/results.json."""

    def __init__(self, interval_minutes: int = DEFAULT_AUTO_INTERVAL_MINUTES):
        self.run_lock = threading.Lock()
        self.interval_seconds = max(interval_minutes, MIN_AUTO_INTERVAL_MINUTES) * 60
        self.enabled = False
        self.next_run_at: datetime | None = None
        self.last_run_error: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def do_run(self) -> tuple[bool, str | None]:
        """Runs one check if nothing else is already running. Returns
        (ran, error) - ran=False with error=None means a run was already in
        progress and this call was skipped rather than queued."""
        if not self.run_lock.acquire(blocking=False):
            return False, None
        try:
            Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
            setup_logging(str(LOG_PATH))
            args = parse_args(
                [
                    "--config", str(CONFIG_PATH),
                    "--state", str(STATE_PATH),
                    "--log", str(LOG_PATH),
                    "--results", str(RESULTS_PATH),
                ]
            )
            run(args)
            self.last_run_error = None
            return True, None
        except Exception as exc:  # noqa: BLE001 - surface any failure instead of killing the loop
            self.last_run_error = str(exc)
            logger.exception("Auto-check run failed")
            return True, str(exc)
        finally:
            self.run_lock.release()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.next_run_at = datetime.now() + timedelta(seconds=self.interval_seconds)
            if self._stop_event.wait(self.interval_seconds):
                break
            self.do_run()

    def start(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        self._stop_event.clear()
        self.next_run_at = datetime.now() + timedelta(seconds=self.interval_seconds)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.enabled = False
        self._stop_event.set()
        self.next_run_at = None

    def set_interval_minutes(self, minutes: int) -> None:
        self.interval_seconds = max(minutes, MIN_AUTO_INTERVAL_MINUTES) * 60
        if self.enabled:
            self.next_run_at = datetime.now() + timedelta(seconds=self.interval_seconds)


auto_checker = AutoChecker()


def _load_results() -> tuple[list[dict], datetime | None]:
    path = Path(RESULTS_PATH)
    if not path.exists():
        return [], None
    rows = json.loads(path.read_text(encoding="utf-8"))
    return rows, datetime.fromtimestamp(path.stat().st_mtime)


def _group_results(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = row["target"]
        if key not in grouped:
            grouped[key] = {
                "target": row["target"],
                "retailer": row["retailer"],
                "url": row["url"],
                "rows": [],
            }
            order.append(key)
        grouped[key]["rows"].append(
            {
                "keyword": row.get("keyword", ""),
                "status": row["status"],
                "detail": row.get("detail", ""),
            }
        )
    return [grouped[k] for k in order]


def _find_target(targets: list[Target], target_id: str) -> Target | None:
    return next((t for t in targets if t.id == target_id), None)


def _lines_from_textarea(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


@app.route("/")
def dashboard():
    rows, last_run = _load_results()
    return render_template(
        "index.html",
        groups=_group_results(rows),
        last_run=last_run,
        auto=auto_checker,
        discord_configured=bool(os.environ.get("DISCORD_WEBHOOK_URL")),
    )


@app.route("/run", methods=["POST"])
def trigger_run():
    ran, error = auto_checker.do_run()
    if not ran:
        flash("A check is already running (auto-check or another request) - please wait for it to finish.", "error")
    elif error:
        flash(f"Run failed: {error}", "error")
    else:
        flash("Check complete - results updated below.", "success")
    return redirect(url_for("dashboard"))


@app.route("/auto/toggle", methods=["POST"])
def toggle_auto():
    if auto_checker.enabled:
        auto_checker.stop()
        flash("Auto-check paused.", "success")
    else:
        auto_checker.start()
        flash(f"Auto-check started - every {auto_checker.interval_seconds // 60} min.", "success")
    return redirect(url_for("dashboard"))


@app.route("/auto/interval", methods=["POST"])
def set_auto_interval():
    try:
        minutes = int(request.form["minutes"])
    except (KeyError, ValueError):
        flash("Enter a whole number of minutes.", "error")
        return redirect(url_for("dashboard"))
    if minutes < MIN_AUTO_INTERVAL_MINUTES:
        flash(f"Minimum interval is {MIN_AUTO_INTERVAL_MINUTES} minutes - checking more often just risks retailers blocking your IP.", "error")
        return redirect(url_for("dashboard"))
    auto_checker.set_interval_minutes(minutes)
    flash(f"Auto-check interval set to {minutes} min.", "success")
    return redirect(url_for("dashboard"))


@app.route("/test-notify", methods=["POST"])
def test_notify():
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    setup_logging(str(LOG_PATH))
    notify("Pokémon Preorder Bot", "Test notification from the web dashboard.")
    flash("Test notification sent (check your desktop / Discord, and data/bot.log either way).", "success")
    return redirect(url_for("dashboard"))


@app.route("/targets")
def targets_page():
    targets = load_targets(CONFIG_PATH)
    defaults = load_defaults(CONFIG_PATH)
    return render_template(
        "targets.html",
        targets=targets,
        open_id=request.args.get("open", ""),
        default_keywords="\n".join(defaults["keywords"]),
        default_product_keywords="\n".join(defaults["product_keywords"]),
        default_preorder="\n".join(defaults["preorder_patterns"]),
        default_in_stock="\n".join(defaults["in_stock_patterns"]),
        default_unavailable="\n".join(defaults["unavailable_patterns"]),
    )


@app.route("/targets/add", methods=["POST"])
def add_target():
    targets = load_targets(CONFIG_PATH)
    new_id = request.form["id"].strip()
    if not new_id:
        flash("Target ID is required.", "error")
        return redirect(url_for("targets_page"))
    if _find_target(targets, new_id):
        flash(f"A target with id '{new_id}' already exists.", "error")
        return redirect(url_for("targets_page"))

    targets.append(
        Target(
            id=new_id,
            name=request.form["name"].strip(),
            retailer=request.form["retailer"].strip(),
            url=request.form["url"].strip(),
            type=request.form["type"],
            render="render" in request.form,
            enabled=True,
            keywords=_lines_from_textarea(request.form.get("keywords", "")),
            product_keywords=_lines_from_textarea(request.form.get("product_keywords", "")),
            preorder_patterns=_lines_from_textarea(request.form.get("preorder_patterns", "")),
            in_stock_patterns=_lines_from_textarea(request.form.get("in_stock_patterns", "")),
            unavailable_patterns=_lines_from_textarea(request.form.get("unavailable_patterns", "")),
        )
    )
    save_targets(CONFIG_PATH, targets)
    flash(f"Added target '{new_id}'.", "success")
    return redirect(url_for("targets_page", open=new_id))


@app.route("/targets/<target_id>/update", methods=["POST"])
def update_target(target_id: str):
    targets = load_targets(CONFIG_PATH)
    t = _find_target(targets, target_id)
    if t is None:
        flash(f"Target '{target_id}' not found.", "error")
        return redirect(url_for("targets_page"))

    t.name = request.form["name"].strip()
    t.retailer = request.form["retailer"].strip()
    t.url = request.form["url"].strip()
    t.type = request.form["type"]
    t.render = "render" in request.form
    t.keywords = _lines_from_textarea(request.form.get("keywords", ""))
    t.product_keywords = _lines_from_textarea(request.form.get("product_keywords", ""))
    t.preorder_patterns = _lines_from_textarea(request.form.get("preorder_patterns", ""))
    t.in_stock_patterns = _lines_from_textarea(request.form.get("in_stock_patterns", ""))
    t.unavailable_patterns = _lines_from_textarea(request.form.get("unavailable_patterns", ""))

    save_targets(CONFIG_PATH, targets)
    flash(f"Saved changes to '{target_id}'.", "success")
    return redirect(url_for("targets_page", open=target_id))


@app.route("/targets/<target_id>/toggle", methods=["POST"])
def toggle_target(target_id: str):
    targets = load_targets(CONFIG_PATH)
    t = _find_target(targets, target_id)
    if t is None:
        flash(f"Target '{target_id}' not found.", "error")
        return redirect(url_for("targets_page"))
    t.enabled = not t.enabled
    save_targets(CONFIG_PATH, targets)
    return redirect(url_for("targets_page", open=target_id))


@app.route("/targets/<target_id>/delete", methods=["POST"])
def delete_target(target_id: str):
    targets = [t for t in load_targets(CONFIG_PATH) if t.id != target_id]
    save_targets(CONFIG_PATH, targets)
    flash(f"Deleted target '{target_id}'.", "success")
    return redirect(url_for("targets_page"))


@app.route("/logs")
def logs_page():
    path = Path(LOG_PATH)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()[-300:]
    else:
        lines = []
    return render_template("logs.html", lines=lines, log_path=str(path))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    auto_checker.start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
