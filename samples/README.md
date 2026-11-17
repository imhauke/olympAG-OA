# Synthetic samples

Each folder contains `input.docx`, `instruction.txt`, actual Gemini `output.docx`, `report.json`,
`diff.md` and `run.json` (calls/tokens, timestamps and SHA-256 provenance). All content is fictitious.

| Sample | Changes | Preserved features |
|---|---|---|
| [Session note](therapy_session_note/diff.md) | Date, table value, inserted bullet | Mixed runs, list style, table, header/footer field |
| [Treatment plan](treatment_plan/diff.md) | Interval, table status, paragraph deletion | Logo, hyperlink, numbered list, merged cells |
| [Practice letter](practice_letter/diff.md) | Date/time, sentence removal, paragraph insertion | Logo, signature, superscript reference, section break |

Outputs pass independent integrity checks and reopen with python-docx; tests assert exact
changes and styles. The superscript is ordinary formatted text, not a real footnote.

```sh
make samples          # regenerate deterministic inputs/instructions only; no model calls
make samples-check    # compare inputs and verify stored outputs; no model calls
```

Manual live regeneration: `uv run python -m samples.run therapy_session_note` (at most five
editor calls + one review). Optional `--retry-unavailable` adds one 503 resume. Announce the cap
first: this replaces successful sample outputs/reports. No SDK/reset retries or provider fallback.
[Development evidence](../docs/DEVELOPMENT.md); Word layout is not tested in CI.
