# HANDOFF — living status doc

> Read this first, then `AGENTS.md` (rules). Written so any agent (Claude, Codex, Cursor, Gemini…)
> with **zero prior context** can continue. Updated at every commit checkpoint.

**Last updated:** 2026-11-17 · after step 2 (package layer) · **Deadline:** Thu 2026-11-19

## 1. The task
Olymp technical case study: a **LangGraph agent (Python)** that edits existing `.docx` files from
natural-language instructions (insert / replace / remove text), preserving all unaffected
formatting, images and tables. The editing tools must be our own (no shell/code-exec as the
editing mechanism). Deliverables: repo + setup instructions, README (architecture, decisions,
limitations), ≥1 sample (input, instruction, output), explanation of integrity testing.
Graded on: **editing reliability & document integrity**, architecture & code quality, appropriate
LangGraph use, testing. Optional challenge we take: show our AI dev workflow — focus on
**test-and-fix loops** (`docs/DEVELOPMENT.md`). Phase 2 (later, not started): FastAPI + frontend,
deploy to a subdomain of janguzman.com.

## 2. Status
| # | Step | State |
|---|------|-------|
| 1 | Scaffold: uv, ruff/mypy/pytest, Makefile, CI, hooks, AGENTS/CLAUDE.md | ✅ done |
| 2 | Package layer (`document/package.py`) + no-op round-trip tests + fixture builders | ✅ done |
| 3 | Block index + run-aware ops (`blocks.py`, `runs.py`, `ops.py`) + unit & property tests | ⏭ next |
| 4 | Integrity checker (`integrity.py`) + corruption tests + CLI `inspect` / `verify` | ☐ |
| 5 | LangGraph graph, tools, prompts + fake-LLM graph tests + CLI `edit` | ☐ |
| 6 | First live run (Gemini free tier), fix provider gotchas | ☐ |
| 7 | Synthetic samples (`samples/`), committed outputs verified in CI | ☐ |
| 8 | Small live eval set + `evals.yml` (workflow_dispatch) + eval→regression loop | ☐ |
| 9 | README + `docs/DEVELOPMENT.md` final write-up | ☐ |

## 3. How to run
```bash
uv sync                 # Python 3.12 venv + locked deps (uv installs Python itself)
make check              # THE gate: ruff lint + format check + mypy --strict + pytest/coverage
make test-fast          # unit tests only (inner loop)
uv run docx-agent --help
```
CI: `.github/workflows/ci.yml` runs the same gate on every push to `main` (no LLM calls).

## 4. Decisions (and why)
- **Own OPC layer (`zipfile` + `lxml`) instead of saving through python-docx.** Only parts we open
  via `DocxPackage.xml()` are re-serialized; every other zip entry keeps its original bytes,
  name, order, ZipInfo. Makes "untouched = identical" provable. python-docx is a **test/fixture
  dependency only**.
- **Defensive loading:** no entity resolution / network in the XML parser; limits on part count and
  uncompressed size; encrypted zips, duplicate part names, malformed `_rels/.rels`, corrupt members
  and malformed XML parts are all rejected as `PackageError` (one error type for callers/API).
- "Untouched" means identical **uncompressed part bytes** + same name/order/ZipInfo metadata;
  compressed streams may differ (zlib re-deflate). Stated precisely in README later.
- **Stable block IDs (step 3):** assigned once at load in document order — `p0…` top-level
  paragraphs, `t0` tables, `t0.r1.c2.p0` cell paragraphs; kept in a Python dict → element (never
  written into XML). Inserted paragraphs get `n0…`; deleted IDs are tombstoned. IDs never shift.
- **LLM provider:** configurable. **Default: Google Gemini (free tier, 500 req/day, EU — not used
  for training)**; Anthropic Claude optional (only ~$2 credit). Env vars in `.env.example`
  (`GEMINI_API_KEY`, `GEMINI_MODELS`, `ANTHROPIC_API_KEY`, `DOCX_AGENT_*`). Plan: LangChain
  `init_chat_model` / `langchain-google-genai` native client (decide in step 5; the
  OpenAI-compatible `GEMINI_BASE_URL` is a fallback).
- **CI never calls an LLM.** Graph tests use a scripted fake chat model. Live evals only via
  manual `workflow_dispatch`. Never run live calls casually (token budget).
- Single `main` branch; **the human commits** — agents never commit/push.

## 5. Design spec for the remaining steps
**Step 3 — runs & ops (`src/docx_agent/document/`)**
- `blocks.py`: `BlockIndex` over the main document body (incl. table-cell paragraphs) with the ID
  scheme above; `get(id)`, `text(id)`, `insert_after/before`, tombstones.
- `runs.py`: per-paragraph **character map** over run text atoms: `w:t` (per char), `w:tab`,
  `w:br`, `w:cr`, `w:noBreakHyphen` (1 char each); includes runs inside `w:hyperlink`,
  `w:smartTag`, `w:ins`. Runs containing `w:drawing`, `w:pict`, `w:object`, `w:fldChar`,
  `w:instrText`, footnote/endnote refs are **protected/atomic**.
  `replace_span(p, start, end, new_text)`: split boundary runs (clone `w:rPr`), new text goes into
  the first run of the span (inherits its formatting), drop the rest of the span, set
  `xml:space="preserve"` when needed. Refuse (structured error) if the span touches protected
  content or crosses a hyperlink/field boundary. Insert = zero-width replace inheriting the
  preceding run's format (following run at offset 0). Delete = replace with "".
- `ops.py`: `replace_text`, `insert_text`, `delete_text` (match by exact text, whitespace-normalized
  fallback, `occurrence` index; ambiguous → error listing candidates), `insert_paragraph`
  (deep-copy anchor `pPr` + first run `rPr`, never copy `sectPr`), `delete_paragraph` (refuse if
  it carries `sectPr` or protected content unless `allow_media=True`). Leave bookmarks/proofErr/
  comment ranges in place.
- Tests: unit per op + every refusal path (use `tests/fixtures/builders.py`), and **hypothesis**
  property tests: random run splits/formats + random spans ⇒ text == expected string op AND
  per-char formatting outside span unchanged.

**Step 4 — integrity (`integrity.py`)** `check(before_pkg, after_pkg, touched_ids, edit_log)`:
(1) parts other than edited XML byte-identical, same part set; (2) untouched blocks C14N-identical;
(3) touched paragraphs: `pPr` unchanged + per-char (char, rPr-hash) outside edited spans
identical; (4) counts of drawings/tables/sections/bookmarks/hyperlinks unchanged unless an op
removed them; (5) output reopens in python-docx. Tests inject corruptions and assert detection.
CLI: `docx-agent inspect IN.docx` (ID-tagged view the LLM sees), `docx-agent verify A B`.

**Step 5 — LangGraph (`src/docx_agent/agent/`)** explicit `StateGraph` (not `create_react_agent`):
`load → agent ⇄ tools`; `ask_clarification` → `clarify` (`interrupt()`, CLI resumes with
`Command(resume=…)`, `--non-interactive` exits 3); `finish` → `validate` (deterministic integrity
+ op-effect check; on fail reset doc to original and feed back the diff, ≤2 retries) → `review`
(LLM judge on before/after of touched blocks, structured `{approved, reason}`, ≤2) → `save`
(writes output + `report.json`; nothing written on failure unless `--force`; `--dry-run`).
Document lives in a `DocumentSession` passed via LangGraph runtime context; checkpointer
`InMemorySaver`. Tools (`@tool`, Pydantic args): `view_document`, `find_text`, `replace_text`,
`insert_text`, `delete_text`, `insert_paragraph`, `delete_paragraph`, `ask_clarification`, `finish`.
Message history is append-only. Graph tests with a scripted fake LLM: happy path, ambiguity →
interrupt → resume, validation fail → retry, review reject, retries exhausted, dry-run.

**Steps 7–9:** `samples/generate.py` (deterministic python-docx generators: session note, treatment
plan with merged table + image + hyperlink, practice letter with section break) — all marked
SYNTHETIC; outputs from real agent runs, verified in CI by `test_samples.py`. Small eval set in
`evals/cases/*.yaml` run by `evals/run.py`, manual only. README: setup, architecture diagram,
decisions, limitations, how integrity is tested.

## 6. Gotchas / environment
- **iCloud Desktop sync breaks the venv.** The project currently lives in `~/Desktop` (iCloud
  Drive). iCloud marks `.venv` files `hidden` and creates `name 2.ext` conflict copies; Python
  3.12.16 **skips hidden `.pth` files**, so the editable install silently disappears
  (`ModuleNotFoundError: docx_agent`). Mitigation in repo: pytest `pythonpath = [".", "src"]`.
  Manual fix: `chflags nohidden .venv/lib/python3.12/site-packages/*.pth` and delete `* 2.pth`.
  Real fix: move the project out of iCloud-synced folders (recommended).
- Claude Code hooks (`.claude/`) only work in Claude Code; other agents run `make check` manually.
- `/code-review` must get this repo's **absolute path**; otherwise it may review another git repo
  that happens to be among the session's working directories (happened once in step 2).
- Typer needs `@app.callback()` to stay a command group while only one command exists.
- lxml stubs: `etree.tostring(standalone=None)` doesn't type-check → branch instead.
- Claude 5.5 models: no `temperature`/`top_p`, no forced `tool_choice`; structured output via
  `method="json_schema"`.

## 7. Next action
Start **step 3** (spec above). Use `build_rich_docx()` / `build_paragraphs_docx()` fixtures; keep
`document/` free of any LLM code. Finish with `/checkpoint` (Claude) or `make check` + manual
review + update this file (other agents), then stop for the human to commit.
