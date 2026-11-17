# docx-agent — project rules for AI assistants

LangGraph agent that edits existing `.docx` files from natural-language instructions.
Graded on **editing reliability + document integrity** first, then architecture, LangGraph use, testing.

## Hard rules
- **Never `git commit`, `git push`, or rewrite history.** The human commits at checkpoints.
- Work only on `main`. No new branches.
- All sample/fixture data is **synthetic**. Never add real names, patient or health data.
- The delivered agent must edit documents **only through our own tools** in `src/docx_agent/document/`.
  No shell, `exec`, or code-execution tools in the agent.

## Integrity invariants (do not break; tests enforce them)
1. Zip parts we did not edit are copied **byte-for-byte** (same names, order, compression).
2. Blocks (paragraphs/tables) not touched by an edit are **C14N-identical** to the input.
3. Inside an edited paragraph, per-character formatting **outside the edited span** is unchanged.
4. Protected content (drawings, fields, footnote refs, `sectPr`) is never silently removed.
5. python-docx is used for fixtures and test assertions only, never to save agent output.

## Workflow (test-and-fix loop)
- `make check` = ruff lint + format check + `mypy --strict` + full pytest with coverage. It is the only gate.
- Hooks in `.claude/settings.json` enforce the loop automatically:
  - PostToolUse: after every `.py` edit → format, lint, `pytest tests/unit`; failures come back as feedback.
  - Stop: the turn cannot end while `make check` is red (capped at 4 attempts, then escalate to the human).
- Write the failing test first for every bug (especially ones found by live evals), then fix.
- Prefer small, pure functions in `document/` with exhaustive unit + property tests; keep LLM-facing
  tools in `agent/tools.py` as thin wrappers.

## Layout
- `src/docx_agent/document/` — OPC package I/O, block index, run-aware ops, integrity checker (no LLM).
- `src/docx_agent/agent/` — LangGraph state, tools, prompts, nodes, graph.
- `tests/unit`, `tests/property`, `tests/graph` (scripted fake LLM), `tests/fixtures` (synthetic builders).
- `evals/` — live LLM evals (manual / workflow_dispatch only). `samples/` — deliverable examples.

## LLM gotchas (Claude 5.5 via langchain-anthropic)
- Don't send `temperature`/`top_p`. Don't force `tool_choice` (`any`/`tool` → 400). Use `auto` + prompt.
- Structured output: `with_structured_output(..., method="json_schema")`.
- Never rewrite message history between retries; append only.
