# docx-agent

A LangGraph agent that edits existing Microsoft Word (`.docx`) documents from natural-language
instructions (insert, replace, remove text), changing only what the instruction requires.

> Work in progress — full architecture, decisions, limitations and testing write-up will follow.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 automatically).

```bash
uv sync                 # create .venv and install locked dependencies
cp .env.example .env    # add GEMINI_API_KEY (default provider)
uv run docx-agent --help
make check              # lint + strict types + tests with coverage (same gate as CI)
```

## CLI

```bash
uv run docx-agent inspect input.docx
uv run docx-agent edit input.docx "Replace 8h with 7.5h in the goals table" -o output.docx
uv run docx-agent verify input.docx output.docx
```

`edit` writes the document plus `output.report.json` after deterministic validation and an
LLM review pass. `--report path.json` selects the report path. Ambiguities prompt for an answer;
`--non-interactive` prints the question and exits 3. `--dry-run` performs the same model calls
and checks, prints the diff, and writes no files. Failed checks retry from the original at most
twice; exhausted retries exit 1 without writing. `--force` saves the final rejected attempt and
its failed report, while still exiting 1. The input path cannot be overwritten.

Gemini uses the native `langchain-google-genai` integration and the first `GEMINI_MODELS` entry.
Set `DOCX_AGENT_MODEL` or `--model` for an explicit model ID. For Anthropic, set
`DOCX_AGENT_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`, or pass `--provider anthropic`.
An explicit model override must match the selected provider. SDK retries are disabled;
`DOCX_AGENT_MAX_TURNS` bounds editor calls across all attempts, and `DOCX_AGENT_MAX_RETRIES`
accepts 0–2. The CLI announces call limits before invoking the models. Model IDs and provider
compatibility was verified with `gemini-3.5-flash-lite`, medium thinking, native tools and
JSON-schema review; deterministic tests use fakes. Other model IDs remain unverified.

Tests execute the real LangGraph with a scripted chat model and make no API calls. See
[HANDOFF](docs/HANDOFF.md) for current status, graph details, and the iCloud `.pth` workaround
if an editable install reports `ModuleNotFoundError`.

## Verified live examples

The [three synthetic samples](samples/README.md) include input documents, instructions, real
Gemini outputs, readable diffs, integrity/review reports and call/token provenance. They cover
mixed formatting, list insertion, text/paragraph removal, table edits, a logo, hyperlink, merged
cells and a section break. `make samples-check` verifies the committed files without API calls;
the full CI gate does the same. See [live-run evidence](docs/LIVE_RUNS.md) for the failures found
and regression-tested fixes. Visual review in Word/LibreOffice remains a manual check.

## Manual evaluations

`make eval` prints the plan without constructing a model. Explicit live runs and the manual-only
GitHub workflow are described in [evals/README.md](evals/README.md). Six synthetic cases check
content, intended changes, formatting, heading styles, clarification and embedded instructions.
The [first baseline](evals/baselines/2026-11-17.md) passed 5/6; the ambiguity failure produced a
prompt correction and a successful targeted follow-up. All ordinary tests and CI stay offline.
