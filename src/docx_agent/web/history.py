"""Metadata-only history of processed documents (no database, no document copies).

One small JSON file holds the most recent entries: file name, instruction, model, outcome,
number of changes and time. Document bytes are never written; results live in memory only.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".docx-agent" / "history.json"
LIMIT = 100


def history_path() -> Path:
    override = os.environ.get("DOCX_AGENT_HISTORY")
    return Path(override).expanduser() if override else DEFAULT_PATH


class History:
    def __init__(self, path: Path, limit: int = LIMIT) -> None:
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()

    def entries(self) -> list[dict[str, Any]]:
        """Newest first. A missing or unreadable file is an empty history, never an error."""
        with self._lock:
            return self._read()

    def add(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entries = [entry, *self._read()][: self.limit]
            self._write(entries)

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(self.path)
