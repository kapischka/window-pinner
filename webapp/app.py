from __future__ import annotations

import json
import os
from datetime import datetime
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


def _target_by_id(target_id: str) -> Target | None:
    for t in load_targets(CONFIG_PATH):
        if t.id == target_id:
            return t
    return None


def _lines_from_textarea(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


@app.route("/")
def dashboard():
    rows, last_run = _load_results()
    return render_template("index.html", groups=_group_results(rows), last_run=last_run)


@app.route("/run", methods=["POST"])
def trigger_run():
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
    try:
        run(args)
        flash("Check complete - results updated below.", "success")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI instead of a 500 page
        flash(f"Run failed: {exc}", "error")
    return redirect(url_for("dashboard"))


@app.route("/test-notify", methods=["POST"])
def test_notify():
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    setup_logging(str(LOG_PATH))
    notify("Pokémon Preorder Bot", "Test notification from the web dashboard.")
    flash("Test notification sent (check your desktop, and data/bot.log either way).", "success")
    return redirect(url_for("dashboard"))


@app.route("/targets")
def targets_page():
    targets = load_targets(CONFIG_PATH)
    defaults = load_defaults(CONFIG_PATH)
    return render_template(
        "targets.html",
        targets=targets,
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
    if any(t.id == new_id for t in targets):
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
    return redirect(url_for("targets_page"))


@app.route("/targets/<target_id>/update", methods=["POST"])
def update_target(target_id: str):
    targets = load_targets(CONFIG_PATH)
    for t in targets:
        if t.id == target_id:
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
            break
    else:
        flash(f"Target '{target_id}' not found.", "error")
        return redirect(url_for("targets_page"))

    save_targets(CONFIG_PATH, targets)
    flash(f"Saved changes to '{target_id}'.", "success")
    return redirect(url_for("targets_page"))


@app.route("/targets/<target_id>/toggle", methods=["POST"])
def toggle_target(target_id: str):
    targets = load_targets(CONFIG_PATH)
    for t in targets:
        if t.id == target_id:
            t.enabled = not t.enabled
            break
    save_targets(CONFIG_PATH, targets)
    return redirect(url_for("targets_page"))


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
    app.run(host="127.0.0.1", port=port, debug=False)
