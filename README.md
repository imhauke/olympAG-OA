# docx-agent

A Python/LangGraph agent that edits existing Word (`.docx`) documents from plain-language
instructions: insert, replace and delete text, add or remove paragraphs, and apply existing
paragraph styles. Everything you did not ask to change keeps its formatting, images and tables.
You can use it from an interactive terminal menu or a local web UI.

![The local web UI after an edit: verified changes as a word-level diff, ready to download](docs/images/ui-result.png)

[Deutsch](README.de.md) · [Architecture and integrity](docs/ARCHITECTURE.md) ·
[AI-assisted development](docs/DEVELOPMENT.md) · [Measured eval results](evals/baselines/2026-11-17-final.md)
· The web UI also has a **How it was built** page with the decisions behind the project.

## Quick start

Four steps, about five minutes. Open a terminal in this project folder (the one containing this
README) and run every command there, each on its own line.

### Step 1: Install uv (once per machine)

[uv](https://docs.astral.sh/uv/) installs the right Python (3.12) and every dependency for you.
You do not need Python installed.

macOS or Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then open a new terminal and check that `uv --version` prints a version number.

### Step 2: Install the dependencies

```sh
uv sync --locked
```

This downloads Python 3.12 if needed and installs the locked dependencies into `.venv`. It ends
with a line such as `Installed … packages`.

> **macOS and iCloud Drive:** if the project folder is inside iCloud (for example on the Desktop
> or in Documents with iCloud sync on), iCloud hides files inside `.venv`, and you later get
> `ModuleNotFoundError: No module named 'docx_agent'`. Keep the environment outside iCloud:
>
> ```sh
> rm -rf .venv
> UV_PROJECT_ENVIRONMENT="$HOME/.venvs/docx-agent" uv sync --locked
> ln -s "$HOME/.venvs/docx-agent" .venv
> ```

### Step 3: Add an API key

**Reviewers:** copy the `.env` file you received privately into the project folder, next to
this README. That's all; skip to step 4.

Everyone else: create your own `.env` from the template,

```sh
cp .env.example .env
```

(on Windows: `copy .env.example .env`), open `.env` in any text editor and fill in at least one
key:

```dotenv
GEMINI_API_KEY=your_gemini_key
ANTHROPIC_API_KEY=your_anthropic_key
```

- `GEMINI_API_KEY` selects **Gemini 3.5 Flash-Lite** (the default; a
  [free key](https://aistudio.google.com/apikey) is enough).
- `ANTHROPIC_API_KEY` enables **Claude Sonnet 5.5**.
- Optional, to cap spending per edit: `DOCX_AGENT_MAX_TURNS=5` and `DOCX_AGENT_MAX_RETRIES=0`
  (the review `.env` already sets both). Write comments on their own line, never after a value.

The courtesy review keys are valid for seven days from 2026-11-17 (until 2026-11-24); their
owner revokes both afterwards. Never commit `.env` (it is ignored by git).

### Step 4a: Edit from the terminal menu

```sh
uv run docx-agent
```

This opens the menu:

```text
docx-agent  0.1.0
Edit Word documents with plain-language instructions.

Model      Gemini · gemini-3.5-flash-lite
Documents  3 found in documents/ and samples/

  1  Edit a document
  2  Preview a document (what the agent sees)
  3  Verify an edited copy against its original
  4  Switch model
  5  Open the web UI in your browser
  q  Quit

Choose (1):
```

1. Press **Enter** (or type `1`) to edit a document.
2. Pick a document by its number. The list shows your own files from
   [`documents/`](documents/README.md), earlier edited copies, and three synthetic samples.
   Type `p` to paste or drag in the path of any other `.docx`.
3. Type what should change and press Enter. For a first try, pick
   `samples/therapy_session_note/input.docx` and type:
   `In the goals table, change the sleep target from 8h to 7.5h.`
4. Watch the progress: reading, editing, checking integrity, reviewing. If the agent needs to know
   which of several matches you mean, it asks; type your answer and press Enter.
5. Read the result: every change as a word-level diff with its location, and the path of the
   edited copy (`documents/edited/<name>.edited.docx`) plus its JSON report. Your original is
   never modified, and earlier results are never overwritten.

Back in the menu, `3` re-checks any edited copy against its original, `2` shows the document
exactly as the agent sees it, `4` switches between Gemini and Claude (when both keys are set),
`5` starts the web UI, and `q` quits. Ctrl+C also quits.

**Your own documents:** copy `.docx` files into the [`documents/`](documents/README.md) folder;
they appear in the list the next time you pick a document. Everything in that folder stays local
(ignored by git).

### Step 4b: Edit in the browser

```sh
uv run docx-agent ui
```

This starts the app on <http://127.0.0.1:8765> and opens it in your browser (or choose `5` in
the menu). The interface is available in English and German: switch with **EN · DE** at the top.

![The compose screen: a chosen .docx, the instruction and the model picker](docs/images/ui-compose.png)

1. Click **Choose a .docx file** (or drop one onto it).
2. Describe the change under **What should change?**, or click one of the examples.
3. Pick **Gemini** or **Claude**. Only models with a key in `.env` are offered.
4. Press **Edit document** (or ⌘/Ctrl + Enter) and follow the live progress. If the agent asks a
   question, answer it inline.
5. Read the verified changes, then **Download edited document** (and the JSON report if you want
   it). If the checks or the reviewer reject an edit, nothing is produced and you see why.

The **How it was built** tab (<http://127.0.0.1:8765/docs>) explains the decisions behind the
project. Stop the server with Ctrl+C.

- Local only: the server listens on `127.0.0.1`, rejects other `Host` headers and requires a UI
  header on every request that starts work, so other websites cannot spend your API credit.
- Uploaded documents are edited in memory and never written to disk. Results stay available for
  the most recent edits until the server stops.
- **Recent documents** keeps only the file name, instruction, model, outcome and time (never the
  documents) in `~/.docx-agent/history.json`. Move it with `DOCX_AGENT_HISTORY`; empty it with
  **Clear**.
- Options: `--port 8780` for another port, `--no-open` to skip opening a browser tab.

Edits send document text and your instruction to the chosen provider. Use the synthetic samples
or documents you are allowed to share, and never real patient data.

## Review without an API key

These commands make no model calls:

```sh
uv run docx-agent inspect samples/therapy_session_note/input.docx
uv run docx-agent verify samples/therapy_session_note/input.docx samples/therapy_session_note/output.docx
make check
```

`inspect` prints the ID-tagged view the agent reads. `verify` compares an original with an edited
copy and ends with `OK: integrity checks passed.` The [three samples](samples/README.md) contain
the input, instruction, real Gemini output, diff and provenance; open the Word files to inspect
their layout.

`make check` is the single quality gate, run identically in CI: Ruff lint and format, strict
mypy, and the full pytest suite with coverage. Last run: **319 tests, 98.14% coverage** (minimum
85%). Without Make:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov
```

## One-shot commands (for scripts)

The menu is the easiest way in; every action is also a single command:

```text
docx-agent                     interactive menu
docx-agent ui [--port 8765] [--no-open]
docx-agent inspect FILE.docx
docx-agent edit INPUT.docx "instruction" -o OUTPUT.docx [--provider anthropic]
docx-agent verify BEFORE.docx AFTER.docx [--json] [--allow-removed image]
```

```sh
mkdir -p out
uv run docx-agent edit samples/therapy_session_note/input.docx "In the goals table, change the sleep target from 8h to 7.5h." -o out/edited.docx --non-interactive
uv run docx-agent verify samples/therapy_session_note/input.docx out/edited.docx
```

- Quote paths and instructions that contain spaces. Name the location, the old text and the
  replacement.
- A clarification question pauses the run and waits for your answer; `--non-interactive` exits
  with code 3 instead.
- Failed validation or review resets to the original while retries remain. Nothing is written on
  failure; `--force` saves a final rejected attempt and still exits 1.
- `--dry-run` calls the model but writes no files. `--report PATH.json` sets the report path
  (default: `OUTPUT.report.json`). `--model` and `--provider` override settings; environment
  variables override `.env`. See `uv run docx-agent edit --help`.
- Exit codes: 0 success; 1 failed checks or model call; 2 invalid input, configuration or I/O;
  3 clarification needed in non-interactive mode.

## Architecture and limits

An explicit LangGraph `StateGraph`: load → agent/tools → deterministic validation → structured
model review → save, with clarification interrupts and bounded reset-and-feedback loops. The
mutable document lives in runtime context; checkpointed messages are append-only. The model only
gets typed document tools, never shell or code execution. A confident edit takes two model
calls: one turn that applies every edit and ends with `finish` (cancelled automatically if any edit
in that turn fails), plus the review. The terminal menu (`src/docx_agent/menu.py`) and the web UI
(`src/docx_agent/web/`, FastAPI with static HTML/CSS/JS and no build step) run the same graph and
share every check.

Our own `zipfile`/lxml layer re-serialises only the main XML part. Every other part keeps its
exact content after decompression, and untouched blocks are compared as canonical XML; the zip as
a whole need not be byte-identical. python-docx only builds test fixtures and reopens test
outputs; it never saves agent output. Details: [checks, decisions and implementation](docs/ARCHITECTURE.md).

- Supported: body and table-cell text and existing paragraph styles. Not supported: editing
  headers, footers, footnotes, comments or text boxes; inserting tables or images; changing
  hyperlink targets; text spans across paragraphs; rebuilding a table of contents. Protected
  objects, fields and revisions are refused, never silently dropped.
- Languages: **English and German** are supported and tested, for instructions and documents
  (live: a German instruction on a German macOS-written `.docx`, on both models; offline: umlauts,
  ß, „German quotes“ and en dashes). The engine itself is Unicode-safe, so other Latin-script
  languages will likely work but are untested; right-to-left and CJK documents are not supported.
- Standalone `verify` rejects intended heading-style changes; the edit session validates them
  with exact per-block permissions.
- Clarification and model review do not guarantee intent. Paragraph alignment in the checker is
  heuristic, and CI does not check rendered pages.
- Checkpoints live in memory: a paused edit cannot resume after a restart.

## Evaluations

`make eval` prints the plan without calling a model. A live run must state its call cap:

```sh
uv run python -m evals.run --live --max-calls 36 --retry-unavailable
```

Six synthetic cases with a hard shared call budget and at most one 503 resume per case. On
GitHub, evals run only by manual dispatch. [Runner details](evals/README.md).

Measured: the first baseline passed **5/6**; a prompt fix addressed the ambiguity case. With the
prompt before the same-turn `finish` optimisation, **all 6/6 cases passed** (35 Gemini calls, run
one case at a time because the free tier allows 15 requests per minute; every non-pass was a
provider 503). Each case passed once, so this is evidence of correctness, not a measured pass
rate. Nine further unseen instructions ran live on Gemini and Claude Sonnet 5.5: no saved output
violated integrity, and failures were rejections or provider errors.

Spend: every Gemini run used the free tier ($0). Claude Sonnet 5.5 was used only for live
verification, about $0.07 of the $2 budget declared up front: **$1.93 remains**.
[Final evidence](evals/baselines/2026-11-17-final.md) · [first baseline](evals/baselines/2026-11-17.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Got unexpected extra argument(s)` | Something was pasted after the command, such as a `# comment` (zsh does not treat it as a comment). Run the command alone. |
| `ModuleNotFoundError: No module named 'docx_agent'` (macOS) | The project is in iCloud Drive: use the three-line fix in step 2. |
| Menu shows `no API key found` | Step 3: put a `.env` with `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` in the project folder, then restart. |
| `503 UNAVAILABLE` or `429` | The provider is busy or rate-limited (temporary). Wait a minute and retry, or switch model (`4` in the menu). Nothing was saved. |
| `port 8765 is already in use` | The web UI is already running in another terminal: use that one, or stop it there with Ctrl+C. Or pick another port: `uv run docx-agent ui --port 8780`. |
| A page shows `{"detail":"Not Found"}`, or a change does not appear | A web UI started before the code was updated is still running. Stop it with Ctrl+C and start it again. |

## Project layout

```text
src/docx_agent/document/   OPC package I/O, block index, run-aware edits, integrity checker (no LLM)
src/docx_agent/agent/      LangGraph state, tools, prompts, models, graph
src/docx_agent/menu.py     interactive terminal menu
src/docx_agent/web/        local web UI (FastAPI + static files)
documents/                 your .docx files for the menu (contents ignored by git)
samples/                   three synthetic samples with real model outputs
tests/                     unit, property, graph, CLI, menu and web tests (scripted model)
evals/                     manual live evaluations
docs/                      architecture, development workflow, handoff
```
