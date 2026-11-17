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
compatibility still need the planned Step 6 live smoke test; deterministic tests use fakes.

Tests execute the real LangGraph with a scripted chat model and make no API calls. See
[HANDOFF](docs/HANDOFF.md) for current status, graph details, and the iCloud `.pth` workaround
if an editable install reports `ModuleNotFoundError`.
