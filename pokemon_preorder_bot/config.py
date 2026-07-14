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
    keywords: list[str] = field(default_factory=list)
    available_patterns: list[str] = field(default_factory=list)
    unavailable_patterns: list[str] = field(default_factory=list)


def load_targets(config_path: str | Path) -> list[Target]:
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

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
                keywords=raw.get("keywords", []),
                available_patterns=raw.get("available_patterns", []),
                unavailable_patterns=raw.get("unavailable_patterns", []),
            )
        )
    return targets
