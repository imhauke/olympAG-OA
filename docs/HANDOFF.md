# Handoff

Read [AGENTS.md](../AGENTS.md) for working rules. All nine planned steps are complete.
Continue only on `main`; the human stages/commits/pushes. No live calls without announcing a cap.

## Current state

- CLI: `inspect`, `edit`, `verify`; explicit LangGraph with clarification, validation, review,
  bounded reset/feedback and separate output/report publication.
- Native Gemini `gemini-3.5-flash-lite`, medium thinking, tested live. Claude Sonnet 5.5 (`--provider anthropic`) passed a live smoke edit (verify OK) on 2026-11-17.
- Three real synthetic sample outputs. Eval evidence: first baseline 5/6 + targeted 1/1; final prompt
  6/6 (each case passed once, run case by case on the free tier). See
  [final](../evals/baselines/2026-11-17-final.md) and [first](../evals/baselines/2026-11-17.md) baselines.
- Last gate: `HYPOTHESIS_PROFILE=ci make check` — 271 tests, 99.44% source coverage.
  Passed on macOS and a clean Linux container with locked dependencies; original CI failure reproduced.
  Cleanup verification also passed sample reproducibility, CLI integrity and eval plan (no live calls).
- This cleanup removes tracked coverage conflict copies, the case-study PDF from the deliverable,
  development-only Claude hooks/logs, repeated documentation and unused direct dependencies.
  Original brief/helpers are preserved locally in ignored `out/review-cleanup/`.
- CI failure in sample reproducibility: host-dependent Pillow logo rendering. Generator now uses
  the original `samples/assets/logo.png`; regression tests forbid host font loading. Stored
  DOCX files and provenance hashes are unchanged. Remote CI needs a human commit/push.

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

`.env`, `.venv`, caches, `out/` and raw eval reports are local/ignored. Keep private documents
out of fixtures. Courtesy Gemini and Anthropic keys last seven days from 2026-11-17 (until 2026-11-24); the
owner must actually revoke both review keys. No application expiry/scheduler exists.

Known local issue: iCloud can hide editable-install `.pth` files. Keep the checkout outside
cloud sync; `PYTHONPATH=src:. uv run ...` is a local workaround. Moving a venv requires reinstalling.
Remote evals are manual-only; no agent dispatch or further model calls are needed for delivery.
