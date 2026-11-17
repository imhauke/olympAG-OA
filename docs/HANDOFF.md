# HANDOFF — living status doc

> Read this first, then `AGENTS.md` (rules). Written so any agent (Claude, Codex, Cursor, Gemini…)
> with **zero prior context** can continue. Updated at every commit checkpoint.

**Last updated:** 2026-11-17 · after step 4 (integrity checker, view, CLI inspect/verify) · **Deadline:** Thu 2026-11-19

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
| 3 | Block index + run-aware ops (`blocks.py`, `runs.py`, `ops.py`) + unit & property tests | ✅ done |
| 4 | Integrity checker (`integrity.py`) + corruption tests + CLI `inspect` / `verify` | ✅ done |
| 5 | LangGraph graph, tools, prompts + fake-LLM graph tests + CLI `edit` | ⏭ next |
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
- **Stable block IDs:** assigned once at load in document order — `p0…` top-level
  paragraphs, `t0` tables, `t0.r1.c2.p0` cell paragraphs; kept in a Python dict → element (never
  written into XML). Inserted paragraphs get `n0…`; deleted IDs are tombstoned. IDs never shift.
- **Edits address text by quoting it**, not by offsets: exact match first, forgiving fallback,
  ambiguity is an error with candidates (agent resolves from context or asks the user) — never a guess.
- **Protected-by-default:** run children are whitelisted; unknown content is read-only. A refused
  edit is always better than a silently damaged document.
- **Minimal edits:** replace is trimmed to the characters that really change; split runs are
  re-merged, so a one-word change usually leaves the run structure as it was.
- **Paragraph-level `\n`:** a newline in new text becomes a line break (`w:br`) inside the same
  paragraph; new paragraphs only via `insert_paragraph`.
- **LLM provider:** configurable. **Default: Google Gemini (free tier, 500 req/day, EU — not used
  for training)**; Anthropic Claude optional (only ~$2 credit). Env vars in `.env.example`
  (`GEMINI_API_KEY`, `GEMINI_MODELS`, `ANTHROPIC_API_KEY`, `DOCX_AGENT_*`). Plan: LangChain
  `init_chat_model` / `langchain-google-genai` native client (decide in step 5; the
  OpenAI-compatible `GEMINI_BASE_URL` is a fallback).
- **CI never calls an LLM.** Graph tests use a scripted fake chat model. Live evals only via
  manual `workflow_dispatch`. Never run live calls casually (token budget).
- Single `main` branch; **the human commits** — agents never commit/push.

## 5. Design spec for the remaining steps
**Step 3 — delivered (`src/docx_agent/document/`)** — 131 tests, 99 % branch coverage on `document/`.
- `errors.py`: `EditError(code, message, **details)`; `.to_dict()` is what tools will return to
  the LLM. Codes: `not_found`, `ambiguous` (+`candidates`, `total`), `invalid_occurrence`,
  `protected` (+`protected` reason), `crosses_boundary`, `invalid_args`, `invalid_text`,
  `invalid_span`, `unknown_block`, `deleted_block`, `not_a_paragraph`, `unknown_style` (+`available`).
  Every refusal leaves the document unchanged.
- `blocks.py`: `BlockIndex.from_package(pkg)` — IDs `p0…`, `t0`, `t0.r1.c2.p0` (nested tables
  continue the pattern), block-level `w:sdt`/`w:customXml` walked transparently. `get/paragraph/
  kind/id_of/ids()/paragraph_ids(scope)`, `register(el)` → `n0…`, `remove(id)` tombstones the
  block **and every ID nested in it**. Helpers `children()`, `logical_container()`.
- `runs.py`: `ParagraphMap(p)` = runs + atoms (one per visible char: `w:t` chars, tab `\t`,
  br/cr `\n`, noBreakHyphen U+2011, softHyphen U+00AD; objects U+FFFC). Run children are
  **whitelisted**: anything unknown is a protected atom. Protected: images/objects, complex fields
  (begin…end incl. cached result, **carried across paragraphs** via `open_fields_before(p)`),
  `fldSimple`, `w:ins`/`w:moveTo` runs, footnote/endnote/comment refs, page/column breaks, sym,
  ptab. `w:del`/`w:moveFrom` text is invisible. `replace_span(p, start, end, new, attach=)`:
  splits boundary runs (clone rPr), new text in first span run, removes the rest, re-merges
  same-format plain runs it split, sets `xml:space`, drops inline containers it emptied (returned,
  e.g. `["hyperlink"]`). Insert placement: inherits the preceding run's format (following at
  offset 0); at a container edge (hyperlink) or next to protected content it creates a top-level
  run **outside** the container/field, formatted like the nearest plain top-level run.
  `attach="left"|"right"` forces joining a neighbour (used by replace → see `minimal_change`).
  `char_formats(p)` = (char, container-context + rPr C14N) per char — **use this in step 4's
  integrity check 3**.
- `ops.py`: `find_text(index, query, scope)` (exact; if no exact match anywhere in scope, a
  forgiving pass: whitespace runs collapsed, curly quotes/dashes/NBSP folded, soft hyphens ignored;
  never spans paragraphs). `replace_text / delete_text / insert_text(anchor+side | position)` take
  optional `block_id` (paragraph or table scope) and 1-based `occurrence` (counted within
  `block_id`, else document-wide; ambiguous-candidate occurrences are **per block**, pass both).
  `_span_edit` normalises CRLF, then `minimal_change` trims the common prefix/suffix so quoted
  context keeps its formatting and "Page 1"→"Page 1 of 3" next to a field succeeds.
  `insert_paragraph(index, anchor_id, text, side, style, styles)` copies anchor pPr (minus
  `sectPr`/`pPrChange`) + first text run rPr (minus revision marks); with `style` → only that
  pStyle. `delete_paragraph(index, id, allow_media)` refuses: sectPr carrier, protected content
  (images, fields incl. bare fldChar, footnote/comment refs, equations, tracked changes) unless
  `allow_media`, last paragraph of body/cell/content control, only paragraph between two tables;
  moves bookmarks/comment ranges to the next (else previous) paragraph and reports it as touched.
  `EditResult(op, block_id, before, after, touched, removed)`.
- `styles.py`: `StyleCatalog.from_package` (read-only; never opens styles.xml for editing),
  `resolve(name_or_id)` case-insensitive, `default_id`. `package.related_part(source, type_suffix)`.
- Tests: `tests/unit/test_{runs,blocks,ops,styles}.py`; `tests/property/test_runs_properties.py`
  (hypothesis: plain random runs ⇒ exact text + per-char formatting outside span + inherited format;
  mixed hyperlinks/images/fields/bookmarks ⇒ refused-and-unchanged **or** exact text + formatting
  + no protected element lost). Stress-checked with 5 000 examples each. Fixture helpers:
  `xml_paragraph()`, `xml_body()`, `build_xml_docx()` in `tests/fixtures/builders.py`.

**Step 4 — delivered** — 179 tests, 99 % coverage.
- `document/integrity.py`: `check(before_bytes, after_bytes, *, touched=None, allow_removed=())
  -> IntegrityReport(violations, changes[BlockChange(kind, before_id, after_id, before_text,
  after_text)], changed_parts, ok, to_dict())`. Works on **bytes only** (independent of the
  editor's bookkeeping) — the validate node serialises the session package and calls it.
  Checks: (1) same part names/order, every non-main part byte-identical, zip compression type and
  date unchanged; (2) *skeleton* (document with all `w:p` removed: tables/cells/tcPr, sections,
  sdt wrappers) C14N-identical; (3) paragraphs aligned by diff on (cell container, C14N), changed
  regions paired by text similarity (pPr-aware, memoised DP, capped at 40k cells → positional);
  modified pairs: pPr identical + per-char formatting identical on unchanged stretches ≥ 3 chars
  (whole paragraph if text identical; skipped if similarity < 0.5 = rewritten); (4) protected
  content from the single shared table `runs.PROTECTED` (images, objects, fields, refs, bookmarks,
  comment ranges, hyperlinks, sectPr, tracked changes, equations, sym) fingerprinted and compared
  as multisets — a category in `allow_removed` may only be lost **from changed (and touched)
  paragraphs**; (5) new structural hazards only (body/cell not ending in a paragraph, touching
  tables) and new droppable edge whitespace (space/tab/newline without `xml:space`; NBSP is fine);
  (6) with `touched`: every modified/deleted original ID must be touched, inserted paragraphs ≤
  number of `n*` IDs in touched. python-docx reopen is a **test-only** check (dev dependency).
- `document/view.py`: `render(index, styles, from_id=, to_id=, max_chars=20000)` — one line per
  block: `[p2] (List Bullet) "text"` (JSON string: `\t`, `\n`), `[t0] TABLE 3 rows x 3 cols` with
  indented cell paragraphs, objects as `[IMAGE]`, `[read-only: field|section break|...]` flags.
  Over budget: paragraphs shortened to 80 chars, then tail cut with `(... N more blocks from pX; use
  from_id=pX.)`. Header line (`view.HEADER`) explains quoting rules — reuse it in the prompt.
- CLI: `docx-agent inspect IN.docx [--from-id --to-id --max-chars]`; `docx-agent verify BEFORE
  AFTER [--json] [--allow-removed image ...]` → exit 0 ok, 1 violations, 2 unusable input.
- `ops.delete_paragraph` now derives refusals/declared removals from `runs.PROTECTED`
  (bookmarks/comment ranges are moved; hyperlinks are declared in `removed`, no `allow_media`).
- Tests: `tests/unit/test_integrity.py` (clean sessions pass; 10+ injected corruptions each
  detected with a specific message; scoping, alignment, hazards), `test_view.py`, `tests/test_cli.py`,
  and `tests/property/test_session_properties.py`: random op sequences on the rich fixture ⇒
  `check(touched, allow_removed)` ok **and** python-docx reopens with the expected texts
  (stress-run 2 000 sessions).

**Step 5 — LangGraph (`src/docx_agent/agent/`)** explicit `StateGraph` (not `create_react_agent`):
`load → agent ⇄ tools`; `ask_clarification` → `clarify` (`interrupt()`, CLI resumes with
`Command(resume=…)`, `--non-interactive` exits 3); `finish` → `validate` (deterministic integrity
+ op-effect check; on fail reset doc to original and feed back the diff, ≤2 retries) → `review`
(LLM judge on before/after of touched blocks, structured `{approved, reason}`, ≤2) → `save`
(writes output + `report.json`; nothing written on failure unless `--force`; `--dry-run`).
Document lives in a `DocumentSession` passed via LangGraph runtime context; checkpointer
`InMemorySaver`. Tools (`@tool`, Pydantic args): `view_document`, `find_text`, `replace_text`,
`insert_text`, `delete_text`, `insert_paragraph`, `delete_paragraph`, `ask_clarification`, `finish`.
Message history is append-only. **Validate must also check each op's effect against the
report** (replace/insert/delete_text ⇒ that block `modified` with `EditResult.after` text;
insert_paragraph ⇒ an `inserted` change with that text; delete_paragraph ⇒ `deleted`), because
the checker alone can pair a buggy insert with a deleted paragraph (review step 4).
Pass `touched` = union of `EditResult.touched`, `allow_removed` = union of `.removed`.
Graph tests with a scripted fake LLM: happy path, ambiguity →
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
- **Project moved** to `~/Desktop/X/DEV/Personal/OlympAG OA` (still iCloud-synced): the old `.venv`
  had absolute shebangs → `rm -rf .venv && uv sync`. For ad-hoc scripts outside pytest use
  `PYTHONPATH=src:. uv run python …` (hidden `.pth`, see above).
- **Claude Code tool input decodes `\uXXXX`**: escapes written via Write/Edit land as literal
  characters (then RUF001 fails). Use `chr(0x…)` constants for special characters.
- Coverage gate: `fail_under = 85` (global) in `pyproject.toml`.
- Known limitations so far (README later): a field spanning paragraphs is detected by counting
  `fldChar` begin/end before the paragraph (TOC entries are read-only); edits never span
  paragraphs; hyperlink targets are not editable; ParagraphMap is rebuilt per op (fine for
  case-study sizes, cache if long documents get slow).

## 7. Next action
Start **step 5** (spec in §5): `src/docx_agent/agent/` — `DocumentSession` (original bytes,
`DocxPackage`, `BlockIndex`, `StyleCatalog`, edit log of `EditResult`s, `reset()` = reload from the
original bytes), thin `@tool` wrappers over `document.ops` returning `EditResult.to_dict()` /
`EditError.to_dict()` as JSON, `view_document` = `view.render`, explicit `StateGraph`
(load → agent ⇄ tools → clarify(interrupt) / validate → review → save), scripted fake chat model
for graph tests, CLI `edit`. LLM provider decision (Gemini default vs Anthropic) is still open —
read §4 first; never make live calls in tests. Finish with `/checkpoint`, then stop for the human.
