# AI-assisted development

The human owned requirements, credentials, review and every commit. AI agents implemented
small checkpoints on `main`: OPC preservation → run-aware edits → integrity checking →
LangGraph → live samples/evaluations. This made document damage observable before adding a model.

## Test-and-fix loop

```text
change → format + fast unit tests → make check → review → human commit → CI
live failure → recorded trace → offline regression → fix → targeted live confirmation
```

`make check` is the single gate: lint/format, strict source typing, pytest and coverage.
Graph/CLI tests use scripted models; normal CI never calls a provider. Early Claude-specific
hooks ran these commands automatically; later Codex work ran them manually. Those development-only
hooks and generated logs were removed from the final deliverable. The gate and CI remain.

## Representative failures and fixes

| Failure | Evidence and correction |
|---|---|
| Broad replacement spread bold from a date into an italic time and unchanged text. | Integrity rejected it before saving. [Unit regression](../tests/unit/test_ops.py); token-aligned disjoint edits, prepared atomically. A [property test](../tests/property/test_ops_properties.py) found and retains a repeated-letter counterexample. |
| Judge rejected a correctly inherited bullet because it saw only text. | [Graph regression](../tests/graph/test_graph.py); review now receives actual touched-block styles. |
| Buggy insert/delete could appear as one modification in the final diff. | [Session tests](../tests/unit/test_agent_session.py); verify intermediate operation snapshots/effects, not only final text. |
| Editor changed both ambiguous dates; judge approved. | Independent eval failed. [Recorded synthetic trace](../tests/fixtures/ambiguous_date_trace.json) remains a [negative scoring regression](../tests/evals/test_eval.py). Prompts now require scope clarification; this is not a deterministic intent guard. |
| Provider 503 after completed work. | Explicit opt-in resume at the failed node; [scripted test](../tests/evals/test_eval.py) checks no edit replay and counts failed requests toward the shared budget. |
| Sample reproducibility passed on macOS but failed in Linux CI. | Pillow font rendering changed logo bytes. Reuse the original synthetic PNG; [regression](../tests/test_samples.py) disables host font loading. Existing DOCX/provenance stay unchanged. |

## Measured evidence

- Three [live sample outputs](../samples/README.md), with input/output hashes, tokens and reports.
  Sample/smoke attempts used **33 Gemini invocations**, including three 503s; failures were not
  published as outputs. Final saved runs: session 6 calls (one retry), treatment 5, letter 5.
- [Eval baseline](../evals/baselines/2026-11-17.md): **5/6 initially**, then **1/1** on the failed
  ambiguity case after a prompt-only correction, **30 calls total**.
- [Final-prompt run](../evals/baselines/2026-11-17-final.md): **6/6**, 45 calls; all non-passes were
  provider 429/503 (free-tier 15 requests/minute), fixed by running cases one at a time. Each case
  passed once: not a statistical reliability claim.
- Last full gate: **271 tests, 99.44% source coverage**; same checks passed with the CI Hypothesis
  profile. Local/static integrity tests, corruption tests and real graph execution are separate evidence.

All public fixtures are synthetic. A private user document was edited locally and remains
outside the deliverable; it is not live Gemini evidence. No Anthropic calls were made.
Visual layout remains a human check. Live samples/evals require explicit bounded runs; normal
tests do not spend quota. Courtesy credentials are supplied privately and revoked by their owner.
