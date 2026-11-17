# Manual evaluations

Six strict YAML cases cover mixed text/list/table edits, deletion, date/time formatting, heading
hierarchy, ambiguity and embedded instructions. Sources are restricted to named synthetic fixtures.

```sh
make eval  # plan only; no model constructed
uv run python -m evals.run --live --max-calls 36 --retry-unavailable
uv run python -m evals.run --live --case heading_hierarchy --max-calls 7
```

Announce the cap first. Five editor turns/one review per case; zero SDK/reset retries. The optional
503 retry resumes a failed node once per case. A shared callback budget blocks excess requests
before both editor and judge calls. No provider/model fallback; `--model` selects a Gemini ID.

Scoring is independent of the judge: case-defined changed IDs/counts, text/styles, untouched C14N,
selected run formats, integrity and reopening must pass. Clarification requires a real interrupt
before any edit/publication. Output IDs are serialized IDs; `before_id` maps comparisons to the
original. `style_changes` declares the only permitted paragraph-style changes.

Ignored `evals/reports/<timestamp>-<suffix>/` retains input, successful output, tool arguments/results,
failures, tokens and JSON/Markdown summaries. Unrun cases stay in the denominator; failures/errors
exit 1. Cost is unpriced unless both `--input-usd-per-million` and `--output-usd-per-million` are
supplied for a uniform-rate estimate; failed-request usage is unknown.

GitHub runs only via `workflow_dispatch`, uses the `GEMINI_API_KEY` secret and uploads reports
even on failure. Ordinary CI stays offline. On a live failure, save the trace, reproduce offline,
fix, run `make check`, then rerun only the affected case. [First baseline: 5/6 + targeted 1/1](baselines/2026-11-17.md);
[6/6 before the same-turn finish optimization](baselines/2026-11-17-final.md), 35 calls, plus nine unseen live cases. On the free tier (15 requests/minute)
run cases individually, a minute apart: the whole suite at once hits 429s.
