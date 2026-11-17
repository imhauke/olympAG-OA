# Synthetic editing samples

All practices, people, appointments and notes here are fictitious. Each folder contains the
original Word document, its natural-language instruction, the **actual Gemini agent output**,
an integrity/review report, a readable diff, and call/token counts plus SHA-256 provenance in
`run.json`. Nothing was staged or committed by the agent.

| Sample | Edits | Features preserved | Calls in saved run |
|---|---|---|---|
| [Session note](therapy_session_note/diff.md) | Date, table value, new bullet | Mixed bold/italic/color runs, list style, table, header/footer page field | 6, including one HTTP 503 retry |
| [Treatment plan](treatment_plan/diff.md) | Review interval, table status, paragraph removal | Logo image, hyperlink, numbered list, merged table cells | 5 |
| [Practice letter](practice_letter/diff.md) | Appointment date/time, obsolete sentence removal, new paragraph | Logo, bold date, italic time, signature, superscript reference, section break | 5 |

The letter's superscript reference is ordinary formatted text, not a real Word footnote.
All three outputs pass independent package/XML/formatting checks and reopen with python-docx.
Tests assert the exact requested text, changed block IDs, inserted/deleted counts and bullet
style. CI repeats these checks without making model calls, and uploads the sample files.
Visual review in Word/LibreOffice remains a human check; it has not been claimed here.

```bash
make samples          # deterministic inputs + instructions only; no model calls
make samples-check    # regeneration comparison + static output verification; no model calls
uv run docx-agent verify samples/practice_letter/input.docx samples/practice_letter/output.docx
```

Regeneration fixes core-property dates and every ZIP timestamp. It never overwrites outputs.
Do not run the following command casually: it makes **live API calls** and replaces successful
sample output/report/provenance files. Announce the call cap first.

```bash
# Each selected sample: at most 5 editor + 1 review calls, no SDK/reset retries.
uv run python -m samples.run therapy_session_note
# Explicit option permits one additional call only after HTTP 503 (7 total).
# The graph resumes the failed node with its completed edits, rather than restarting.
uv run python -m samples.run therapy_session_note --retry-unavailable
```

The runner selects Gemini and the first `GEMINI_MODELS` entry even if an older `.env` sets a
Claude model. `--model` explicitly selects another Gemini ID; no model/provider fallback is
automatic. The application CLI still stops on a provider error; the extra transport retry
exists only in this opt-in manual harness. See [live-run log](../docs/LIVE_RUNS.md) for failed
attempts and the fixes they produced.
