# Handoff

Read [AGENTS.md](../AGENTS.md) for working rules. All nine planned steps are complete.
Continue only on `main`; the human stages/commits/pushes. No live calls without announcing a cap.

## Current state

- CLI: `inspect`, `edit`, `verify`; explicit LangGraph with clarification, validation, review,
  bounded reset/feedback and separate output/report publication.
- Native Gemini `gemini-3.5-flash-lite`, medium thinking, tested live. Anthropic is optional/unverified.
- Three real synthetic sample outputs. Eval evidence: initial 5/6 plus targeted 1/1, not a
  final-version full-suite 6/6. See [baseline](../evals/baselines/2026-11-17.md).
- Last gate: `HYPOTHESIS_PROFILE=ci make check` — 269 tests, 99.44% source coverage.
  Cleanup verification also passed sample reproducibility, CLI integrity and eval plan (no live calls).
- This cleanup removes tracked coverage conflict copies, the case-study PDF from the deliverable,
  development-only Claude hooks/logs, repeated documentation and unused direct dependencies.
  Original brief/helpers are preserved locally in ignored `out/review-cleanup/`.

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
out of fixtures. Courtesy access lasts seven days from the private handover timestamp; the
owner must actually revoke the separate review key. No application expiry/scheduler exists.

Known local issue: iCloud can hide editable-install `.pth` files. Keep the checkout outside
cloud sync; `PYTHONPATH=src:. uv run ...` is a local workaround. Moving a venv requires reinstalling.
Remote evals are manual-only; no agent dispatch or further model calls are needed for delivery.
