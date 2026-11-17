# Handoff

Read [AGENTS.md](../AGENTS.md) for working rules. All nine planned steps are complete; the
project is in delivery polish. Work only on `main`; the human stages, commits and pushes. No live
model calls without announcing a call cap first.

## Current state

- **Entry points.** `uv run docx-agent` (no command) opens the interactive terminal menu
  ([`menu.py`](../src/docx_agent/menu.py)): edit, preview, verify, switch model, open the web
  UI. It lists `documents/*.docx`, `documents/edited/*.docx` and `samples/*/input.docx`, and writes
  results to `documents/edited/<name>.edited.docx` (+ `.report.json`), never overwriting.
  `documents/` contents are git-ignored except its README. One-shot commands `edit`, `inspect`,
  `verify`, `ui` and `version` remain for scripts, CI and evals.
- **Web UI** (`uv run docx-agent ui`, [`web/`](../src/docx_agent/web/)): upload, instruction,
  live progress, inline clarification, word-level diff, download of document and report,
  metadata-only history in `~/.docx-agent/history.json`. English and German via the EN · DE switch
  ([`static/i18n.js`](../src/docx_agent/web/static/i18n.js); server messages are translated client-side,
  the model's own text is not). A **How it was built** page at `/docs`
  ([`static/docs.html`](../src/docx_agent/web/static/docs.html)) explains scope, planning
  decisions, portability, architecture, integrity, the test-and-fix loop, evidence, timeline and
  limits. Its figures (tests, coverage, eval results) are hand-written: update them when they change.
- Menu and web share progress text and failure wording (`progress_detail`, `friendly_reason`,
  `NODE_STEP` in [`web/jobs.py`](../src/docx_agent/web/jobs.py)) and `PROVIDER_LABELS`/`Provider`
  from [`agent/models.py`](../src/docx_agent/agent/models.py).
- **Speed/cost:** an edit may end with `finish` in the same turn (2 model calls for a confident
  edit instead of 3). Offline graph tests cover it; **live evals predate this change**.
- Providers: native Gemini `gemini-3.5-flash-lite` (default, evals and samples) and Claude Sonnet
  5.5 (live smoke edit and unseen cases, verify OK). Eval evidence: first baseline 5/6 + targeted
  1/1; 6/6 before the same-turn finish optimisation. See
  [final](../evals/baselines/2026-11-17-final.md) and [first](../evals/baselines/2026-11-17.md).
- Budget: Anthropic credit **$1.93 of the original $2 remains** (about $0.07 spent on Claude
  verification); Gemini runs are free tier. Reviewers' use of the courtesy key draws on it.
- **Pending human checks:** run the README quick start from zero (menu and web UI) with both
  models, and give feedback on the UI and on the same-turn finish optimisation.
- Last gate: `HYPOTHESIS_PROFILE=ci make check`: 319 tests, 98.14% source coverage. The menu and
  docs page were also rendered and checked headlessly (scripted model; desktop and 390 px).

## Verify and maintain

```sh
uv sync --locked
make format && make test-fast
make check
```

Read [architecture/checker limits](ARCHITECTURE.md) and [development evidence](DEVELOPMENT.md)
before editing. Keep the deterministic engine/tests and static sample/eval provenance.
Style edits use exact `style_changes` permissions; standalone `verify` has no style flag.
Ambiguity policy is prompt-based; a model can still guess. Checkpoints resume only in-process.

`.env`, `.venv`, caches, `out/`, `documents/*` and raw eval reports are local and ignored. Keep
private documents out of fixtures. Courtesy Gemini and Anthropic keys last seven days from 2026-11-17 (until
2026-11-24); the owner must actually revoke both. No application expiry or scheduler exists.

Known local issue: iCloud (File Provider) hides editable-install `.pth` files and ignores
`.nosync`. On the owner's machine `.venv` is a symlink to `~/.venvs/olympag-oa` (created with
`UV_PROJECT_ENVIRONMENT`), outside iCloud; see README step 2. Moving a venv requires reinstalling.
Remote evals are manual-only; no further model calls are needed for delivery.
