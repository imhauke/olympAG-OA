---
description: Turn a failing live-eval case into a deterministic regression test, then fix it (no new LLM calls)
argument-hint: <eval case id or path to eval report>
---

A live eval failed: $ARGUMENTS

Work **offline** — do not call the live LLM or re-run evals during this command.

1. Read the eval case (`evals/cases/`) and the latest report in `evals/reports/` for this case:
   the instruction, the agent's tool-call trace, and which assertion failed.
2. Classify the root cause:
   - **tool/ops bug** (wrong edit, formatting lost, integrity violation) → write a failing unit or
     property test in `tests/unit` / `tests/property` reproducing it with a synthetic fixture.
   - **agent/graph behaviour** (wrong routing, missing retry, bad tool error message) → write a
     failing graph test in `tests/graph` that replays the recorded tool calls with the scripted fake LLM.
   - **prompt/model behaviour only** → adjust prompts/tool descriptions, and add a graph test for
     any deterministic guard you add (e.g. a better error message the model can act on).
3. Confirm the new test fails for the right reason, then fix the code until `make check` is green.
4. Append a row to the "Test-and-fix log" in `docs/DEVELOPMENT.md`:
   eval case → root cause → regression test → fix.
5. Tell the human which single eval case is worth re-running live to confirm (do not run it yourself).
