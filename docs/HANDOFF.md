# HANDOFF — living status doc

> Read this first, then `AGENTS.md` (rules). Written so any agent (Claude, Codex, Cursor, Gemini…)
> with **zero prior context** can continue. Updated at every commit checkpoint.

**Last updated:** 2026-11-17 · after steps 6–7 (live Gemini + verified samples) · **Deadline:** Thu 2026-11-19

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
| 5 | LangGraph graph, tools, prompts + fake-LLM graph tests + CLI `edit` | ✅ done |
| 6 | First live run (Gemini), fix provider gotchas | ✅ done |
| 7 | Synthetic samples (`samples/`), outputs verified in CI | ✅ done |
| 8 | Small live eval set + `evals.yml` (workflow_dispatch) + eval→regression loop | ⏭ next |
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
- **LLM provider:** Google Gemini by default, using native `langchain-google-genai` (added and
  locked in step 5); Anthropic remains optional. `DOCX_AGENT_PROVIDER=google_genai|anthropic`,
  `DOCX_AGENT_MODEL` (or CLI `--model`), `DOCX_AGENT_EFFORT=low|medium|high`.
  An empty model selects the first nonempty `GEMINI_MODELS` entry for Gemini, or
  `claude-sonnet-5-5` for Anthropic. Keys: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`.
  `.env.example` now matches this default; `GEMINI_BASE_URL` is unused and removed from the
  example. No automatic provider/model fallback. **Verified live in steps 6–7:**
  `gemini-3.5-flash-lite`, medium thinking, native tool continuation and strict JSON-schema
  review. Other IDs/providers are still unverified. Do not assume provider quotas/data terms.
- **Call budgets:** SDK retries are disabled, editor turns capped across all attempts
  (`DOCX_AGENT_MAX_TURNS`, default 30, range 1–100), validation/review share at most two resets
  (`DOCX_AGENT_MAX_RETRIES`, default 2). Separate structured review calls are at most retries+1.
  The CLI announces those upper bounds. Provider refusals stop immediately.
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

**Step 5 — delivered** — `make check`: **226 tests, 99.41% overall branch-aware coverage**.
- `agent/session.py`: `DocumentSession(original_bytes)`, package/index/styles, `edits`, `reset`,
  `apply`, `validate`. Final integrity uses unions of `.touched` / `.removed`. Each successful
  operation also records before/after package bytes and the target's IDs as a fresh disk index
  would assign them (stable session IDs may differ after insertions/deletions). Validation
  checks each snapshot's integrity and claimed effect: text ops must be modified with the
  claimed text, insert/delete paragraphs must be inserted/deleted. Exact no-ops are allowed.
  Journal continuity detects changes outside the wrappers. This addresses step 4's disguised
  insert/delete pairing gap without rejecting multi-op edits to the same block or cancelling
  edits by comparing intermediate text with the final output. Snapshot memory/CPU cost is
  proportional to document size × successful operations; fine for case-study documents.
- `agent/tools.py`: nine typed `@tool` wrappers (inferred Pydantic schemas). Only document
  operations are exposed, no shell/code execution. Structured errors are recoverable feedback.
  A custom tools node runs batches **sequentially**; finish/clarification must be called alone
  (mixed batches are rejected before executing any edits).
- `agent/graph.py`: explicit `load → agent ⇄ tools`; `clarify` is a separate `interrupt()` node,
  so resume never replays edits. `finish → validate → review → save`. Validation failure or
  review rejection resets the document and appends reason/diff + fresh view to the history,
  sharing ≤2 retries. `AgentState` is checkpointed with `InMemorySaver`; mutable session and
  output options live in runtime `AgentContext`. Messages are append-only (`operator.add`).
  Review is a separate model call with strict `{approved: bool, reason: str}` JSON schema,
  original view, user answers, complete diff and operation log. Original view is budgeted;
  deterministic checks still cover the full document. Malformed reviews/model failures stop
  without saving. Unreadable output becomes validation feedback. Refusals stop further calls.
- `agent/prompts.py`: quote/ID contracts, ambiguity policy, protected-content rules, reset
  semantics; embedded document instructions are treated as data.
- `agent/models.py`: pydantic-settings + `.env`, native Gemini and Anthropic constructors.
  No implicit SDK retries; effort/timeout configured, no temperature or forced tool choice.
  Reviewer uses `with_structured_output(..., method="json_schema")`.
- CLI `edit IN.docx "instruction" -o OUT.docx [--report path.json] [--model ID]
  [--provider google_genai|anthropic] [--non-interactive] [--dry-run] [--force]`.
  Default report: `OUT.report.json` (e.g. `out.docx` → `out.report.json`). Normal success exits 0;
  failed checks/provider calls exit 1; bad input/config/I/O exits 2; clarification required in
  non-interactive mode exits 3. Interactive answers resume the same checkpoint/context.
  `--dry-run` still calls models and validates/reviews, but writes **no files**; diff is printed.
  `--force` saves the final failed-check attempt/report after retries and still exits 1;
  it does not override model failures, clarification, or the editor turn cap.
- `agent/artifacts.py`: stages both files, then atomic per-file renames; ordinary publication
  errors roll back previous file contents and clean temporary files. Two files are **not** a
  crash-atomic transaction. CLI rejects input overwrites and document/report aliases (including
  hardlinks). Existing explicit output paths may be replaced. New files use private temp-file
  permissions. No directories are created automatically.
- Tests: `tests/graph/fake.py` scripted `BaseChatModel`; real graph happy path, ambiguity/resume,
  validation retry, review retry/exhaustion, force, dry-run, invalid tools/control batches,
  sequential dependent edits, bounded turns, refusal handling and unreadable output.
  `tests/test_cli_edit.py` exercises actual CLI + graph with the fake. Unit tests cover model
  configuration, all wrappers, journal effects, unjournaled corruption and save rollback.
  CI blanks both provider keys. No live LLM calls were made in step 5.

**Steps 6–7 — delivered** — 243 tests, 99.43% overall coverage at the full gate.
- **33 real Gemini model invocations** across smoke/sample attempts, including three failed
  HTTP 503 calls. No Anthropic calls. Three successful output documents, each independently
  verified and approved by structured review. Exact attempt counts and outcomes:
  `docs/LIVE_RUNS.md`; final per-call tokens/timestamps/hash provenance: `samples/*/run.json`.
  No further live calls are needed for these steps.
- Two live failures became offline regressions before fixing:
  1. Reviewer incorrectly rejected an inherited bullet because its payload lacked output
     style evidence. It now receives `edited_blocks`: views of all live touched blocks,
     including inserted paragraph styles. Prompt explains that plain-text diffs omit styles.
  2. Broad replacement of a bold date + italic time spread bold into the unchanged middle.
     `ops._replacement_changes` matches whole tokens, keeps meaningful unchanged anchors,
     and groups tiny common fragments into the replacement. Multiple spans are applied in
     reverse order on a copy of the full XML context; only successful paragraph children
     are transferred back. Refusals remain atomic, and field context from preceding
     paragraphs survives. New unit + property tests cover this and repeated-letter alignment
     (found by the new property test). Single-span/no-op behavior stays on the existing path.
     Multi-span replacements add a document-tree copy; acceptable at case-study sizes.
- `samples/generate.py`: three independent python-docx builders; fixed core dates and ZIP
  timestamps for deterministic inputs/instructions. **No model calls and no output rewrites.**
  Session note: mixed bold/italic/color runs, bullets, goals table, header/footer page field.
  Treatment plan: numbered list, logo, hyperlink, merged header cells, goals table.
  Practice letter: logo, bold date/italic time, signature, section break, superscript reference.
  The superscript is ordinary formatted text, not an actual Word footnote.
- Each sample folder: `input.docx`, `instruction.txt`, **real** `output.docx`, `report.json`,
  `diff.md`, `run.json`. Saved runs: treatment 5 calls, letter 5 calls, session 6 calls
  (one 503 retry included). All three requested changes are asserted for each output.
- `samples/run.py`: manual harness, defaults to native Gemini/first `GEMINI_MODELS` candidate
  (explicitly clears any stale Claude model override). Five editor calls + one review per
  sample, zero SDK/reset retries. `--retry-unavailable` **explicitly** allows one extra call
  after HTTP 503 only, using `graph.invoke(None, same_config, same_context)` at the failed
  node. A scripted regression proves completed edits are not replayed. This opt-in harness
  retry does not change application CLI behavior. Never run it casually or from CI.
- `tests/test_samples.py`: regenerate input bytes/instructions, verify output integrity
  independently, compare with saved reports, reopen with python-docx, assert exact texts,
  changed IDs, insertion/deletion counts, inherited bullet style, hashes and live provenance.
  `tests/test_sample_runner.py` + scripted sample runs keep the harness tested offline.
  `make samples-check` runs regeneration comparison + these tests. Full `make check` includes
  them automatically. CI uploads sample deliverables after passing tests.
- `samples/README.md` explains the deliverables/runs. Root README links to examples/evidence.
  Word/LibreOffice visual review was not performed; it remains a human check.

**Steps 8–9:** small eval set in `evals/cases/*.yaml` run by `evals/run.py`, manual only;
`evals.yml` workflow_dispatch. Final README: architecture diagram, decisions, limitations,
testing explanation; finish `docs/DEVELOPMENT.md`.

**Additional checkpoint — paragraph style support (2026-11-17):** a user-supplied document
request exposed that existing paragraphs could not receive heading styles. Added
`ops.set_paragraph_style(index, block_id, style, styles)` and a thin LLM-facing tool. It changes
only `w:pPr/w:pStyle`, resolves existing catalog names/IDs, and preserves runs, other properties
and protected content. `EditResult.style` records the requested ID (omitted from JSON on old
operation types for compatibility). Session validation passes exact per-block style permissions
to `check(..., style_changes={original_id: expected_style_id})`; other paragraph properties and
run formatting stay protected, and the requested style must actually be present. Authorized
style changes also inform paragraph alignment for combined title rename + style edits.
Default `verify` remains strict about style changes; programmatic checks need the explicit map.
Synthetic unit/graph regressions cover styling, rename/alignment, protected objects, refusal,
no-op, wrong targets and unrelated property corruption. Gate: **249 tests, 99.44% coverage**.
The supplied document and outputs stay only in ignored `out/`; no real document fixture was
added. Automatic approval review rejected sending its content to Gemini without explicit
disclosure authorization, so **zero live calls** were made for this request. A separately
labelled local-tools output passed session integrity checks and python-docx reopening.

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
- Step 5: `uv add langchain-google-genai` needed network/cache sandbox approval; dependency
  install succeeded, with no model calls. For sandboxed local checks use
  `UV_CACHE_DIR=/tmp/docx-agent-uv-cache make check` if the default uv cache is unwritable.
- iCloud hid `docx_agent.pth` again during step 5. Cleared its hidden flag locally;
  `uv run docx-agent edit --help` works. This is an environment repair, not a tracked change.
- Clarification checkpoints are **in-process only**. Resume must use the same graph/thread ID
  and the same mutable session; there is no cross-process resume CLI or durable storage yet.
- Step 6 found the ignored local `.env` still selecting Claude while the default provider was
  Gemini. The live harness overrides it explicitly; its `DOCX_AGENT_MODEL` line was also cleared
  locally so normal CLI usage now selects the tested first Gemini candidate. Keys were preserved
  and never printed. This local config edit is untracked.
- Gemini returned three HTTP 503s during real runs; no silent model/provider fallback was added.
  The opt-in manual harness can retry the pending node once; see `docs/LIVE_RUNS.md`.
- Known limitations so far (README later): a field spanning paragraphs is detected by counting
  `fldChar` begin/end before the paragraph (TOC entries are read-only); edits never span
  paragraphs; hyperlink targets are not editable; ParagraphMap is rebuilt per op (fine for
  case-study sizes, cache if long documents get slow).

## 7. Next action
Step 5 was committed by the human before steps 6–7. **Steps 6–7 are now ready for human review
and commit**; nothing was staged or committed by this agent. Suggested commit:
`feat: verify live Gemini editing and add reproducible synthetic samples`.

Next: **step 8**, a small manual eval harness + cases (reuse sample inputs), with assertions on
expected content, untouched blocks/formatting, and should-ask-clarification. Add
`.github/workflows/evals.yml` as `workflow_dispatch` only. Keep CI offline. Announce the exact
live-run call cap first and preserve the low-credit budget; do not rerun all samples needlessly.
Turn each newly found failure into a deterministic failing regression before fixing it.
Then step 9 finalizes architecture/limitations/testing documentation and the workflow write-up.

The additional paragraph-style checkpoint above is also uncommitted; the human makes commits.
A live run on a user-provided document now needs explicit consent for external disclosure if
automatic approval review blocks it. Local editing and verification can proceed without that.

API references consulted in step 5:
[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts),
[native Gemini integration](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai).
