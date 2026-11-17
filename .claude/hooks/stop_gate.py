"""Stop hook: the outer test-and-fix loop.

Claude may not end its turn while `make check` (lint + strict types + full test
suite with coverage) is red. Exit code 2 blocks the stop and feeds the failure
back, so Claude keeps fixing. A consecutive-block counter (scoped to the current
session) caps the loop at MAX_BLOCKS so an unfixable state is escalated to the
human instead of spinning.
"""

from __future__ import annotations

import json
import sys

from _common import STATE, log, read_event, run, tail

MAX_BLOCKS = 4
CHECK_TIMEOUT = 600  # must stay well below the 900s hook timeout in settings.json


def _load(session: str) -> int:
    try:
        data = json.loads(STATE.joinpath("stop_blocks.json").read_text())
        return int(data["blocks"]) if data.get("session") == session else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0  # missing/corrupt state never disables the gate


def _save(session: str, blocks: int) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.joinpath("stop_blocks.json").write_text(
        json.dumps({"session": session, "blocks": blocks})
    )


def main() -> int:
    session = str(read_event().get("session_id", ""))
    blocks = _load(session)

    code, out = run(["make", "check"], timeout=CHECK_TIMEOUT)
    if code == 0:
        if blocks:
            log({"hook": "stop_gate", "ok": True, "after_blocks": blocks})
        _save(session, 0)
        return 0

    blocks += 1
    log({"hook": "stop_gate", "ok": False, "attempt": blocks})
    if blocks > MAX_BLOCKS:
        _save(session, 0)
        print(
            f"`make check` still failing after {MAX_BLOCKS} fix attempts; "
            "stopping so the human can decide.",
            file=sys.stderr,
        )
        return 0
    _save(session, blocks)
    print(
        f"`make check` is failing (attempt {blocks}/{MAX_BLOCKS}). "
        f"Fix it before finishing:\n{tail(out, 80)}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
