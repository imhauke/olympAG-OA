@AGENTS.md

## Claude Code specifics
The test-and-fix loop is automated here via `.claude/settings.json` hooks (other agents run the
same `make` targets manually — see AGENTS.md):
- **PostToolUse** (`.claude/hooks/post_edit.py`): after every `.py` edit → format, lint,
  `pytest tests/unit`; failures are fed back (exit 2).
- **Stop** (`.claude/hooks/stop_gate.py`): the turn cannot end while `make check` is red
  (max 4 consecutive blocks, then escalate to the human). Iterations are logged to `.claude/loop-log.jsonl`.

Project commands (`.claude/commands/`):
- `/checkpoint` — gate + automated review + HANDOFF update, then stop for the human to commit.
- `/eval-to-test <case>` — turn a failing live-eval case into a deterministic regression test, then fix.

LLM gotchas (Claude 5.5 via langchain-anthropic): don't send `temperature`/`top_p`; don't force
`tool_choice` (`any`/`tool` → 400); structured output via `method="json_schema"`; append-only history.
