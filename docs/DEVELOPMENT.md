# How this was built — AI-assisted workflow with test-and-fix loops

> Optional-challenge write-up. Finalised in step 9; logs below are appended as work happens.

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

Evidence of loop iterations is appended automatically to `.claude/loop-log.jsonl`.

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

## Review log (/checkpoint)
| Step | Findings | Fixed | Skipped (reason) |
|---|---|---|---|
| 2 | 1st run reviewed the **wrong repo** (another session working dir) → discarded. 2nd run (explicit path): 10 findings | duplicate part names, malformed `.rels`, corrupt member (all with failing tests first); hook timeouts ≥ hook limit + uncaught TimeoutExpired; stop counter not session-scoped / crash on corrupt state; O(n) part lookup; shared ZipInfo mutation; metadata not asserted in round-trip test; synthetic-data rule ambiguous (clarified) | raw-copy of compressed streams (we guarantee part *content*, documented); formatter rewriting edited files (Claude Code already reports hook modifications) |
| 3 | 10 findings (6 reproduced by the reviewer on inline XML) | complex fields spanning paragraphs (TOC) not protected + `delete_paragraph` could drop a closing `fldChar`; CRLF in new text broke prefix/suffix trimming (duplicated line break); ambiguous-candidate `occurrence` was document-wide but documented as per-block; insert at a paragraph edge leaked into a hyperlink; deleting the only paragraph between two tables (Word merges them); `BlockIndex.remove` did not tombstone nested IDs; duplicated container/marker/soft-hyphen constants. All with failing regression tests first | per-op re-building of character maps (perf only; docs and call counts are small — revisit if long documents get slow) |
