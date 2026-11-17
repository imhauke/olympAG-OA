# How this was built — AI-assisted workflow with test-and-fix loops

> Optional challenge: a record of how AI-assisted changes were developed, tested and reviewed.
> Finalized in step 9. The human owns the requirements, credentials, review and every commit.

## Working method

The implementation was built in small checkpoints on `main`: package preservation first,
run-aware edits next, then independent integrity checks, LangGraph orchestration and bounded
live tests. That order made document damage observable before a model could hide it behind a
plausible answer. The AI proposed and implemented changes; the human selected the scope,
reviewed outputs and committed checkpoints. Agents never committed or pushed.

Claude Code hooks automated some early feedback. Codex later ran the same Make targets
manually and recorded local reviews; the Claude `/code-review` command was unavailable in that
environment. The review log below distinguishes those paths rather than claiming automation
that did not run.

## Loop structure
```
plan (approved by human) ─► step ─► edit ──► [inner loop] format + lint + unit tests ──fail──┐
                                    ▲                                                        │
                                    └────────────────── fix ◄────────────────────────────────┘
step done? ─► [outer loop] `make check` (lint, strict types, all tests, coverage) ──fail──► fix
          ─► /checkpoint: automated code review ─► fix real findings (test first) ─► HANDOFF update
          ─► human reviews + commits ─► CI runs the same gate on GitHub
live eval fails ─► /eval-to-test: reproduce offline as a deterministic test ─► fix ─► CI
```
| Piece | Where | Automated by |
|---|---|---|
| Inner loop | `.claude/hooks/post_edit.py` | Claude Code `PostToolUse` hook (exit 2 feeds failures back) |
| Outer loop | `.claude/hooks/stop_gate.py` → `make check` | Claude Code `Stop` hook (blocks finishing while red, max 4) |
| Review gate | `.claude/commands/checkpoint.md` | `/checkpoint` command |
| Eval → regression | `.claude/commands/eval-to-test.md` | `/eval-to-test` command |
| Remote gate | `.github/workflows/ci.yml` | GitHub Actions on every push |
| Agent-neutral rules | `AGENTS.md`, `docs/HANDOFF.md` | any coding agent can continue |

Claude hook executions append evidence to `.claude/loop-log.jsonl`. That file is not a complete
record of Codex turns: manual gate results, regressions and review outcomes are recorded here,
in the tests and in `docs/HANDOFF.md`.

## Three concrete feedback loops

**A plausible edit damaged formatting.** The live practice-letter request replaced a bold date
and an italic time in one quote. Plain text was correct, but formatting spread into the
unchanged connecting text. The integrity checker rejected the output before saving. An offline
[unit regression](../tests/unit/test_ops.py) reproduced the date/time case before the fix.
Replacement now separates changed spans around meaningful token anchors and prepares the edit
in a copied XML context, retaining atomic refusal and earlier-paragraph field context. A new
[property test](../tests/property/test_ops_properties.py) found a repeated-letter alignment
counterexample; that minimized example remains explicit in the test.

**The reviewer lacked evidence.** A new bullet inherited the correct style, but the judge saw
plain-text changes and rejected it as a normal paragraph. A
[graph regression](../tests/graph/test_graph.py) demonstrated the missing style evidence. The
judge now receives actual views of live touched blocks, including their paragraph styles.
This repaired its information input rather than weakening the integrity checker.

**Both models accepted an ambiguous scope.** The six-case live eval initially passed 5/6. The
editor changed two labelled dates when the user had not selected a field or requested all
matches, and its judge approved. The independent evaluator failed it. The
[real synthetic tool trace](../tests/fixtures/ambiguous_date_trace.json) is replayed by a
[negative eval regression](../tests/evals/test_eval.py): approval must not make it score as a
success. Editor/judge prompts now distinguish knowing old/new values from knowing the intended
scope. Only this failed case was rerun, and it asked before editing in two calls. This is a
prompt-only correction, not a deterministic intent guard or a final-version full-suite 6/6.

## How work was verified

- The full gate is `make check`: lint, formatting, strict source typing and all tests with
  coverage. The same checks run in normal CI, without model access. There is no additional
  hidden gate. The walkthrough gives the four equivalent uv commands for users without Make.
- Scripted models drive the actual graph, including ambiguity interrupts/resume, failed checks,
  review rejection, limits, dry runs and publication failure. Synthetic fixtures isolate
  document operations from provider behavior.
- Unit/property tests compare untouched parts and blocks, per-character formatting outside
  edited spans and protected structures. Corruption tests show that the checker catches faults;
  tests do more than reopen files or compare visible text.
- Live runs were manual and announced with call caps. Step 6–7 sample attempts used 33 Gemini
  invocations; step 8 used 30, including its targeted follow-up. Anthropic was not used. Counts,
  available tokens and hashes are preserved in [sample evidence](LIVE_RUNS.md) and the
  [qualified eval baseline](../evals/baselines/2026-11-17.md).
- Final documentation was checked against local CLI help, offline inspect/verify output,
  locked dependency state, a scripted first-edit walkthrough and relative links. No extra live
  calls were needed to write or verify the guides.

## What remains a human responsibility

Review the resulting document in Word/LibreOffice, approve changes and commit/push the finished
repository. CI does not compare rendered pages. Model judgments and the small eval set do not
guarantee semantic correctness.

Public samples/fixtures contain fictitious data. A user-provided document was edited only with
local tools after external disclosure was blocked; that result is not presented as live Gemini
evidence, and the document was not added as a fixture.

The optional seven-day courtesy key must be supplied privately and actually expired/revoked
by its owner; documentation does not configure provider credentials. The
[handover guide](REVIEW_HANDOFF.md) makes that distinction explicit. Neither a key nor a
scheduled revocation was created during this documentation step.

## Test-and-fix log (notable iterations)
| Step | Caught by | Failure | Fix |
|---|---|---|---|
| 1 | outer loop (`make check`) | unused `noqa` in hook helper | removed |
| 1 | outer loop | Typer collapsed single-command app → `version` arg rejected | `@app.callback()` |
| 2 | inner loop (PostToolUse) | `tests` package not importable from conftest | pytest `pythonpath` |
| 2 | outer loop (mypy strict) | lxml stub rejects `standalone=None` | branch on docinfo |
| 2 | outer loop (tests) | test bug: zip appended into a throwaway `BytesIO` | keep buffer reference |
| 2 | outer loop (tests) | `docx_agent` vanished from venv: iCloud set `hidden` flag, Python skips hidden `.pth` | `pythonpath` incl. `src`; documented |
| 2 | review → regression tests | duplicate zip names silently rewritten; malformed `.rels` / corrupt member escaped as lxml/zlib errors | reject duplicates; wrap in `PackageError` |
| 2 | Stop hook (hardened version, first run) | unescaped regex in `pytest.raises(match=)` | raw string |
| 3 | inner loop (ruff RUF001) | `\uXXXX` escapes in agent-written source arrived as literal look-alike characters (NBSP, U+2011) | build special chars with `chr(0x…)` |
| 3 | outer loop (mypy strict) | lxml stubs: `dict(el.attrib)` / bytes in f-string | copy attributes via `el.items()` |
| 3 | smoke run before tests | replacing "Max Mustermann attended" made " attended" bold (new text takes the first span run's format) | `minimal_change`: trim common prefix/suffix before editing; unit + property test |
| 3 | inner loop (tests) | two test bugs (wrong span offsets; "absent" text that was present) | fixed the tests, code was right |
| 3 | review → regression tests | see review log row 3 | see review log row 3 |
| 4 | outer loop (view test) | budget overshoot: "more blocks" note added after the size was counted | trim until the whole output fits |
| 4 | session property test (hypothesis) | `delete_paragraph` removed a hyperlink without declaring it → checker flagged it | declare `hyperlink` in `EditResult.removed` |
| 4 | session property test, 2 000-example stress | checker false positives: unrelated insert + delete paired as "modified" (pPr / coincidental 1-char matches) | pPr-aware pairing; format check only on ≥3-char unchanged stretches of similar paragraphs |
| 4 | regression test written for a review fix | off-by-one in the new one-pass view trimming (IndexError) | loop bound |
| 5 | graph/session tests | checking only the final diff cannot prove intermediate effects when several operations edit the same paragraph | per-op byte snapshots, disk-ID mapping and effect checks, plus final integrity and journal continuity |
| 5 | failing save regression test | a report write failure left the document published | stage both artifacts; roll back ordinary publication errors; test existing/new artifacts |
| 5 | focused review → failing regression tests | output/report hardlinks passed path checks; unreadable output bypassed retry feedback; provider refusals spent more calls | samefile checks, PackageError → validation failure, immediate refusal stop |
| 5 | outer loop (mypy strict) | TypedDict keyword update, mixed node method inference, provider model alias and CLI model variable shadowing | typed dict updates, explicit node wiring, correct constructor keyword and distinct client variable |
| 5 | real CLI help check (no model call) | iCloud hid editable-install `.pth` again; pytest path configuration masked it | clear hidden flag locally; `docx-agent edit --help` succeeds; environment workaround in HANDOFF |
| 6–7 | live session-note run → failing graph regression | reviewer called a correctly inherited bullet a plain paragraph; it lacked output style evidence | supply views of live touched blocks with actual paragraph styles; prompt + regression |
| 6–7 | live practice-letter run → failing unit regression | a broad date/time replacement spread bold into unchanged connecting text and the italic time | token-aligned disjoint spans; atomic preparation in copied XML context; exact date/time + preceding-field refusal regressions |
| 6–7 | new replacement property test | repeated letters matched across separate words, displacing the unchanged middle phrase | whole-token anchors; retain the shrunk case as an explicit Hypothesis example |
| 6–7 | three live HTTP 503 responses → failing harness regression | restarting a run would spend calls and replay already completed work | explicit opt-in single 503 retry at the pending graph node; test proves no edit replay |
| Heading-style request | synthetic failing unit/graph tests before extending tools | no existing-paragraph style tool; checker correctly rejected all paragraph-property changes | add exact per-block style permissions, effect checks and alignment support; preserve every other property/run/object. 249 tests, 99.44% coverage. External disclosure of the supplied document was blocked by automatic approval review; local-tools output only, zero live calls |
| 8 | six-case live eval, independent assertions | model broadened a singular date request to both labelled dates, and its reviewer approved; initial 5/6 | retain real tool trace as a negative scripted eval; strengthen scope/clarification instructions for editor + judge. Prompt-only correction, no deterministic intent guard. Targeted real follow-up: asked before editing, 1/1 in 2 calls; five other cases not rerun |
| 8 | offline evaluator tests | approving an unchanged/wrong output must not count as success; quota guard must apply before both editor and review calls | case-defined semantic/integrity assertions, actual clarification interrupts before edits, shared callback budget; scripted 503 resume verifies completed edits are not replayed |
| 9 | documentation walkthrough and review | beginner setup needed platform-specific steps, observable outputs and an honest distinction between courtesy access and real key expiry | English/German guides, API-free preview, matching first-edit commands verified with the fake model, 53 link/anchor checks, private handover guide; no provider calls or credential changes |

## Review log (/checkpoint)
| Step | Findings | Fixed | Skipped (reason) |
|---|---|---|---|
| 2 | 1st run reviewed the **wrong repo** (another session working dir) → discarded. 2nd run (explicit path): 10 findings | duplicate part names, malformed `.rels`, corrupt member (all with failing tests first); hook timeouts ≥ hook limit + uncaught TimeoutExpired; stop counter not session-scoped / crash on corrupt state; O(n) part lookup; shared ZipInfo mutation; metadata not asserted in round-trip test; synthetic-data rule ambiguous (clarified) | raw-copy of compressed streams (we guarantee part *content*, documented); formatter rewriting edited files (Claude Code already reports hook modifications) |
| 3 | 10 findings (6 reproduced by the reviewer on inline XML) | complex fields spanning paragraphs (TOC) not protected + `delete_paragraph` could drop a closing `fldChar`; CRLF in new text broke prefix/suffix trimming (duplicated line break); ambiguous-candidate `occurrence` was document-wide but documented as per-block; insert at a paragraph edge leaked into a hyperlink; deleting the only paragraph between two tables (Word merges them); `BlockIndex.remove` did not tombstone nested IDs; duplicated container/marker/soft-hyphen constants. All with failing regression tests first | per-op re-building of character maps (perf only; docs and call counts are small — revisit if long documents get slow) |
| 4 | 10 findings | delete_paragraph ignored tracked changes on the paragraph mark while the checker counted them → one shared `runs.PROTECTED` table for ops + checker; whitespace check blamed pre-existing issues and treated NBSP as droppable; zip compression type/date not compared (invariant 1); `allow_removed` was document-wide → now only from changed+touched paragraphs; O(m·n) pairing DP → memoised + capped; view rendered all blocks for a range; quadratic tail trimming; duplicated pPr compare. Regression tests first | buggy insert placed exactly where a deletion happened is paired as "modified" — the validate node (step 5) must check each op's effect (delete ⇒ reported as deleted), recorded in HANDOFF |
| 5 | Focused local review of tracked diff + new files (Codex; the Claude `/code-review` command is unavailable in this environment) | save failure/rollback, hardlink path aliases, unreadable-output retry handling, refusal call budget; regressions first. Step 4's deferred op-effect gap now covered by snapshot checks. Full gate: 226 tests, 99.41% coverage; no live calls | live model-ID/effort compatibility remains the explicitly planned step 6; per-op snapshots retained for correctness at case-study sizes; artifact pair is not crash-atomic (documented) |
| 6–7 | Focused local review of source, live artifacts and offline regressions (Codex; Claude `/code-review` unavailable) | reviewer now has style evidence; broad replacements preserve unchanged formatted anchors and remain atomic; explicit 503 retry is capped and resumes the same checkpoint; sample provenance/text/change/style checks run offline in CI | no automatic provider fallback; multi-span tree-copy cost retained for integrity; manual Word visual review not claimed. Live evidence: 33 invocations, no Anthropic, three verified outputs. Gate: 243 tests, 99.43% coverage |
| 8 | Focused local review of evaluator, workflow and measured results (Claude `/code-review` unavailable) | independent case-defined scoring, shared editor/review call cap, opt-in 503 resume, safe manual workflow arguments, strict synthetic source list, saved negative ambiguity trace + prompt clarification policy | no whole-suite rerun after prompt fix; baseline explicitly reports initial 5/6 plus targeted 1/1, not final-version 6/6. 30 real Gemini calls, no Anthropic/personal data. Gate: 269 tests, 99.44% source coverage; CI profile passes |
| 9 | Focused local documentation/command review (Claude `/code-review` unavailable) | step-by-step English/German walkthroughs, matching demo commands and exact verification output, linked architecture/checker limits, actual automation boundaries, private seven-day key handover with no false automatic-expiry claim | Windows installation not executed on this macOS host; real credential expiry/revocation remains the owner's handover task. No new live calls. Full CI-profile gate: 269 tests, 99.44% source coverage |
