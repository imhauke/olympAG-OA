"""Shared helpers for the Claude Code test-and-fix hooks.

The hooks are plain Python (stdlib only) so they run with any python3 and
delegate the real work to `uv run ...` inside the project environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
LOG = ROOT / ".claude" / "loop-log.jsonl"
STATE = ROOT / ".claude" / ".state"


def read_event() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    proc = subprocess.run(  # fixed argv, no shell
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def log(entry: dict[str, Any]) -> None:
    """Append one line of evidence about a test-and-fix iteration."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
