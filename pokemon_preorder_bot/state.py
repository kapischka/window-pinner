from __future__ import annotations

import json
from pathlib import Path


class StateStore:
    """Tracks the last known status per (target, keyword) so we only alert on changes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def _key(target_id: str, keyword: str) -> str:
        return f"{target_id}::{keyword}"

    def last_status(self, target_id: str, keyword: str) -> str | None:
        return self._data.get(self._key(target_id, keyword))

    def set_status(self, target_id: str, keyword: str, status: str) -> None:
        self._data[self._key(target_id, keyword)] = status

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
