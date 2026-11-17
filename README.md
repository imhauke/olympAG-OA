# docx-agent

Python/LangGraph agent for instruction-driven `.docx` edits: insert, replace, delete and apply
existing paragraph styles while preserving unaffected content and formatting.

[Deutsch](README.de.md) · [Architecture & integrity](docs/ARCHITECTURE.md) ·
[AI development workflow](docs/DEVELOPMENT.md) · [Measured eval results](evals/baselines/2026-11-17.md)

## Run

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/); Python 3.12 is selected
by `.python-version`. Run from the repository root:

```sh
uv sync --locked
```

On macOS, keep the checkout outside iCloud: it can hide editable-install files and cause
`ModuleNotFoundError`. Recreate `.venv` after moving; locally, prefix commands with `PYTHONPATH=src:.`.

Use the privately supplied review `.env`, or copy `.env.example` to `.env` and set:

```dotenv
GEMINI_API_KEY=your_key
DOCX_AGENT_MODEL=gemini-3.5-flash-lite
DOCX_AGENT_MAX_TURNS=5
DOCX_AGENT_MAX_RETRIES=0
```

The courtesy key is supplied privately for seven days from the stated handover time; the
owner must revoke it at expiry. There is no application-side expiry. Afterward, use your own
[Gemini key](https://aistudio.google.com/apikey). Never commit `.env`.

```sh
mkdir -p out
uv run docx-agent edit samples/therapy_session_note/input.docx "In the goals table, replace the sleep target 8h with 7.5h. Leave everything else unchanged." -o out/edited.docx --non-interactive
uv run docx-agent verify samples/therapy_session_note/input.docx out/edited.docx
```

PowerShell: create the folder with `New-Item -ItemType Directory -Force out`.
Expected: `saved: ...`, `out/edited.docx`, `out/edited.report.json`, and verification ending in
`OK: integrity checks passed.` The input is preserved. Output paths can be overwritten on rerun.

`edit` sends document text/instructions to Gemini. Use the synthetic examples or documents
you are permitted to share; no real patient data. The settings above cap an edit at five editor
calls plus one review. Application defaults are 30 turns and two shared validation/review resets.

## Review without a key

```sh
uv run docx-agent inspect samples/therapy_session_note/input.docx
uv run docx-agent verify samples/therapy_session_note/input.docx samples/therapy_session_note/output.docx
make check
```

`inspect`, `verify` and tests make no model calls. The [three samples](samples/README.md) include
input, instruction, actual Gemini output, diff and integrity/provenance reports. Open the Word
files to inspect their layout.

`make check` = Ruff lint/format + strict source mypy + full pytest/coverage, also run in CI.
Last gate: **271 tests, 99.44% source coverage** (minimum 85%). Without Make, run:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov --cov-report=term --cov-report=xml
```

## Interface and behavior

```text
docx-agent inspect FILE.docx
docx-agent edit INPUT.docx "instruction" -o OUTPUT.docx
docx-agent verify BEFORE.docx AFTER.docx [--json]
```

- Quote paths/instructions containing spaces. Give the location, old text and replacement.
- Clarification pauses the graph; answer in the same process. `--non-interactive` exits 3 instead.
- Validation/review failures reset to the original while retries remain. Nothing is published
  on failure; `--force` saves a final rejected attempt and still exits 1.
- `--dry-run` still calls the model but writes no files. `--report PATH.json` sets the report
  path; otherwise `result.docx` produces `result.report.json`.
- `--model`/`--provider` override settings. Environment variables override `.env`. See
  `.env.example` and `uv run docx-agent edit --help` for options. Native Gemini is live-tested;
  optional Anthropic support is not. SDK retries are disabled; there is no provider fallback.
- Exit codes: 0 success; 1 failed checks/model call; 2 invalid input/configuration or I/O;
  3 non-interactive clarification.

## Architecture and limits

Explicit `StateGraph`: load → agent/tools → validate → structured model review → save, with
clarification interrupts and bounded reset/feedback loops. The mutable document is in runtime
context; checkpointed messages are append-only. Tools expose document operations, no shell/code execution.

Own `zipfile`/lxml operations reserialize only the main XML part. Unaffected part contents stay
byte-identical **after decompression**; untouched blocks are compared by canonical XML. The
complete ZIP need not be byte-identical. python-docx creates fixtures/reopens test outputs;
it never saves agent output. [Checks, decisions and implementation](docs/ARCHITECTURE.md).

- Body/table-cell text and existing paragraph styles only. No header/footer, footnote/comment/
  text-box editing, table/image insertion, hyperlink-target changes, cross-paragraph text spans
  or automatic TOC rebuilding. Protected objects/fields/revisions are refused, not silently dropped.
- Standalone `verify` rejects intended heading-style changes. The edit session validates those
  using exact per-block permissions; the programmatic checker accepts `style_changes`.
- Ambiguity handling and model review are not guarantees of intent. The checker has heuristic
  paragraph alignment/format comparison limits; CI does not validate rendered pages.
- Checkpoints are in-process only. Snapshot/tree-copy costs target case-study documents.

## Evaluations

`make eval` prints a plan without calls. Explicit live runs use:

```sh
uv run python -m evals.run --live --max-calls 36 --retry-unavailable
```

Six synthetic cases; hard shared call budget; optional single 503 resume per case inside that
budget. GitHub evals are `workflow_dispatch` only. [Runner details](evals/README.md).
Measured baseline: **5/6 initially**, then **1/1** on the failed ambiguity case after a prompt
fix; **30 calls total**. The other five were not rerun under the final prompt: no final-version
6/6 or general reliability claim. [Evidence](evals/baselines/2026-11-17.md).
