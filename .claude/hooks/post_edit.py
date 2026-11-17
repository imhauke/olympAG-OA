"""PostToolUse hook (Edit|Write|MultiEdit): fast inner test-and-fix loop.

After every Python edit it auto-formats the file, lints it, and runs the fast
unit-test subset. Any failure is returned to Claude via exit code 2 (stderr is
fed back to the model), so the next action is a fix, not more feature code.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _common import ROOT, log, read_event, run, tail


def main() -> int:
    event = read_event()
    raw = (event.get("tool_input") or {}).get("file_path", "")
    if not raw:
        return 0
    path = Path(raw)
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return 0  # outside the repo
    if path.suffix != ".py" or rel.parts[0] in {".venv", ".claude"}:
        return 0

    run(["uv", "run", "--quiet", "ruff", "format", str(rel)])
    run(["uv", "run", "--quiet", "ruff", "check", "--fix", "--quiet", str(rel)])
    code, lint_out = run(["uv", "run", "--quiet", "ruff", "check", str(rel)])
    if code != 0:
        log({"hook": "post_edit", "file": str(rel), "stage": "lint", "ok": False})
        print(f"ruff found problems in {rel}:\n{tail(lint_out)}", file=sys.stderr)
        return 2

    code, test_out = run(["uv", "run", "--quiet", "pytest", "-x", "-q", "tests/unit"], timeout=300)
    # exit code 5 == no tests collected; fine during scaffolding
    if code not in (0, 5):
        log({"hook": "post_edit", "file": str(rel), "stage": "unit-tests", "ok": False})
        print(
            f"Fast unit tests fail after editing {rel}. Fix before continuing:\n{tail(test_out)}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
