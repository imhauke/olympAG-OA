# Manual synthetic evaluations

Six YAML cases cover mixed formatting/list insertion, a merged table and paragraph deletion,
a date/time replacement plus sentence removal, heading hierarchy, an ambiguous date, and
instructions embedded in document text. Inputs are restricted to named synthetic fixtures.
The supplied personal document is not part of this set.

First measured result: [5/6 initially; failed ambiguity case fixed and confirmed separately](baselines/2026-11-17.md).
Thirty calls total; no full-suite rerun after the prompt change.

```bash
make eval                            # plan only; no model client or API calls
make eval EVAL_ARGS="--live --max-calls 36 --retry-unavailable"
uv run python -m evals.run --live --case heading_hierarchy --max-calls 7
```

Announce a live run and its call cap first. The default provider is native Gemini; an old Claude
model override in `.env` is cleared for this runner. `--model` selects a specific Gemini ID.
There is no automatic model/provider fallback. Each case permits five editor turns and one
review, with zero document-reset/SDK retries. `--retry-unavailable` explicitly permits one
extra call on HTTP 503, resuming the failed node. All cases share `--max-calls`: a callback
raises **before** the next model request if the global cap is exhausted, including review calls.

Reports go to ignored `evals/reports/<UTC timestamp>-<unique suffix>/`. Every case retains its
synthetic input, successful output/report, actual tool-call arguments, tool results, token
usage and assertion failures in `result.json`. `summary.json` and `summary.md` show the pass
rate, calls, elapsed time and available input/output tokens. Failed requests may have no token
usage. Cost is explicitly **not priced** by default; both `--input-usd-per-million` and
`--output-usd-per-million` can supply uniform rates for a labelled estimate. This does not
model discounts or claim account billing accuracy.

An eval passes only when independent assertions pass. An LLM review approval alone is
insufficient. Checks include package/formatting integrity, exact changed original IDs,
insertion/deletion counts, specified text and styles, C14N equality of untouched paragraphs,
selected run-format sequences, and reopening with python-docx. `should_ask_clarification`
requires an actual interrupt before any edit or output publication. `not_run` cases remain
in the denominator if the budget runs out. Failures/errors produce exit 1.

Case `block_id` addresses the **serialized output**; `before_id` optionally identifies the
original paragraph for comparisons when insertions/deletions shift IDs. `style_changes` maps
original IDs to the only authorized style changes; other paragraph properties remain strict.
Keep assertions case-defined, independent of the model's own operation log.

`.github/workflows/evals.yml` runs **only** via `workflow_dispatch`, reads `GEMINI_API_KEY` from
GitHub secrets, accepts the model/case/global call cap, and uploads reports even on failure.
The push/PR CI remains offline and exercises the evaluator with a scripted model. Nobody has
dispatched the remote workflow during development; the human commits/pushes the YAML.

When a live case fails: read its saved tool trace, reproduce the issue offline with the scripted
model or a synthetic unit/property fixture, confirm the regression fails, then fix and run
`make check`. Do not repeatedly rerun the whole live suite. See
[the eval-to-test workflow](../.claude/commands/eval-to-test.md).
