# docx-agent — rules for any AI coding agent

LangGraph agent that edits existing `.docx` files from natural-language instructions.
Graded on **editing reliability + document integrity** first, then architecture, LangGraph use, testing.
**Current status and next steps: [docs/HANDOFF.md](docs/HANDOFF.md) — read it first.**

## Hard rules
- **Never `git commit`, `git push`, or rewrite history.** The human commits at checkpoints.
- Work only on `main`. No new branches.
- All sample/fixture data is **synthetic**: fictitious therapy-practice content (placeholder names like
  "Max Mustermann", invented notes) is expected and fine. Never add *real* people, patient or health data.
- The delivered agent edits documents **only through our own tools** in `src/docx_agent/document/`.
  No shell, `exec`, or code-execution tools in the agent.
- **Token budget:** never run live LLM calls/evals casually (free-tier Gemini quota, ~$2 Anthropic credit).
  Tests use a scripted fake LLM. Announce any live run and its call count first.
- Keep the project small: no speculative features, no extra dependencies without a reason.
- At every checkpoint, update `docs/HANDOFF.md` so an agent with zero context can continue.

## Integrity invariants (tests enforce them)
1. Zip parts we did not edit keep **identical bytes** (same names, order, compression type).
2. Blocks (paragraphs/tables) not touched by an edit are **C14N-identical** to the input.
3. Inside an edited paragraph, per-character formatting **outside the edited span** is unchanged.
4. Protected content (drawings, fields, footnote refs, `sectPr`) is never silently removed.
5. python-docx is used for fixtures and test assertions only, never to save agent output.

## Workflow (test-and-fix loop)
- `make check` = ruff lint + format check + `mypy --strict` + full pytest with coverage. The only gate.
  CI (`.github/workflows/ci.yml`) runs the same gate on every push to `main`.
- Write the failing test first for every bug (especially ones found by live evals), then fix.
- Inner loop after each edit: `make format && make test-fast`. Outer loop before finishing: `make check`.
- Small pure functions in `document/` with unit + property tests; LLM-facing tools in
  `agent/tools.py` stay thin wrappers.

## Layout
- `src/docx_agent/document/` — OPC package I/O, block index, run-aware ops, integrity checker (no LLM).
- `src/docx_agent/agent/` — LangGraph state, tools, prompts, nodes, graph.
- `tests/unit`, `tests/property`, `tests/graph` (scripted fake LLM), `tests/fixtures` (synthetic builders).
- `evals/` — live LLM evals (manual / workflow_dispatch only). `samples/` — deliverable examples.
- `docs/HANDOFF.md` — living status doc. `docs/DEVELOPMENT.md` — AI-assisted workflow write-up.
