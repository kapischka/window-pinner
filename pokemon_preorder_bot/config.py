from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Target:
    id: str
    name: str
    retailer: str
    url: str
    type: str
    render: bool
    enabled: bool = True
    keywords: list[str] = field(default_factory=list)
    available_patterns: list[str] = field(default_factory=list)
    unavailable_patterns: list[str] = field(default_factory=list)


def _load_raw(config_path: str | Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_targets(config_path: str | Path) -> list[Target]:
    data = _load_raw(config_path)
    targets = []
    for raw in data.get("targets", []):
        targets.append(
            Target(
                id=raw["id"],
                name=raw["name"],
                retailer=raw["retailer"],
                url=raw["url"],
                type=raw["type"],
                render=bool(raw.get("render", False)),
                enabled=bool(raw.get("enabled", True)),
                keywords=raw.get("keywords", []),
                available_patterns=raw.get("available_patterns", []),
                unavailable_patterns=raw.get("unavailable_patterns", []),
            )
        )
    return targets


def load_defaults(config_path: str | Path) -> dict:
    """Top-level keyword/pattern lists from targets.yaml, used to prefill the
    'add target' form so new entries start with the same match vocabulary."""
    data = _load_raw(config_path)
    return {
        "keywords": data.get("keywords", []),
        "available_patterns": data.get("available_patterns", []),
        "unavailable_patterns": data.get("unavailable_patterns", []),
    }


def target_to_dict(t: Target) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "retailer": t.retailer,
        "url": t.url,
        "type": t.type,
        "render": t.render,
        "enabled": t.enabled,
        "keywords": t.keywords,
        "available_patterns": t.available_patterns,
        "unavailable_patterns": t.unavailable_patterns,
    }


def save_targets(config_path: str | Path, targets: list[Target]) -> None:
    """Rewrites targets.yaml with the given target list.

    Note: this expands the shared keyword/pattern YAML anchors into plain
    per-target lists and drops the descriptive header comments - the file
    stays valid and fully functional, just less tidy than the shipped
    version. Only called when targets are added/edited/removed via the web UI.
    """
    data = {"targets": [target_to_dict(t) for t in targets]}
    path = Path(config_path)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
