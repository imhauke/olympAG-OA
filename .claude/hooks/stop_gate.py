"""Stop hook: the outer test-and-fix loop.

Claude may not end its turn while `make check` (lint + strict types + full test
suite with coverage) is red. Exit code 2 blocks the stop and feeds the failure
back, so Claude keeps fixing. A consecutive-block counter caps the loop at
MAX_BLOCKS so an unfixable state is escalated to the human instead of spinning.
"""

from __future__ import annotations

import sys

from _common import STATE, log, read_event, run, tail

MAX_BLOCKS = 4


def main() -> int:
    read_event()
    STATE.mkdir(parents=True, exist_ok=True)
    counter = STATE / "stop_blocks"
    blocks = int(counter.read_text()) if counter.exists() else 0

    code, out = run(["make", "check"], timeout=900)
    if code == 0:
        if blocks:
            log({"hook": "stop_gate", "ok": True, "after_blocks": blocks})
        counter.write_text("0")
        return 0

    blocks += 1
    counter.write_text(str(blocks))
    log({"hook": "stop_gate", "ok": False, "attempt": blocks})
    if blocks > MAX_BLOCKS:
        counter.write_text("0")
        print(
            f"`make check` still failing after {MAX_BLOCKS} fix attempts; "
            "stopping so the human can decide.",
            file=sys.stderr,
        )
        return 0
    print(
        f"`make check` is failing (attempt {blocks}/{MAX_BLOCKS}). "
        f"Fix it before finishing:\n{tail(out, 80)}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
