# Steps 6–7: live-run evidence (2026-11-17)

Provider: native `langchain-google-genai`, model `gemini-3.5-flash-lite`, medium thinking.
Only synthetic content was submitted. Anthropic was not called. SDK retries and document-reset
retries were disabled for these runs. These are model invocation counts, including failed calls.

| Run | Calls | Result |
|---|---:|---|
| Rich-fixture smoke: table `8h` -> `7.5h` | 3 | Edit and tool continuation worked; review returned HTTP 503. Nothing saved. |
| Session note, first attempt | 5 | Reviewer rejected an inherited bullet as a plain paragraph. No output saved. |
| Treatment plan | 5 | Saved; three requested changes, integrity passed, review approved. |
| Practice letter, first attempt | 4 | Integrity rejected formatting spread from a broad date/time replacement. No output saved. |
| Session note, after reviewer evidence fix | 5 | HTTP 503 in review; no output saved. |
| Practice letter, after replacement fix | 5 | Saved; three requested changes, integrity passed, review approved. |
| Session note, with one explicitly enabled 503 retry | 6 | HTTP 503 on editor call 2; resumed the failed node once, then saved with integrity/review approval. |
| **Total** | **33** | **Three deliverable outputs; three provider 503 responses.** |

The announced run caps were 4 for the smoke, 18 for the first sample batch, 12 for the two
post-fix samples, and 7 for the remaining sample. Actual calls stayed within every cap.
Final runs have timestamped call/token records and input/output/instruction SHA-256 hashes in
`samples/*/run.json`. Token usage for failed HTTP calls is unavailable; no precise cost is claimed.

The two discovered correctness issues were reproduced offline **before** fixing them:

- The reviewer saw plain-text edits without the actual output styles. It now receives views
  of all live touched blocks, including inserted paragraphs and their styles. A graph regression
  asserts that the reviewer sees the inserted `List Bullet` style.
- A quote containing both a bold date and italic time spread the first changed run's format
  over the unchanged text between them. Replacement now keeps meaningful unchanged token
  anchors and applies the separate changes in reverse order. Edits are prepared in a copied
  XML context, so a protected-content refusal remains atomic and fields opened in preceding
  paragraphs remain protected. Tests cover the exact date/time failure, atomic refusal, and
  a property test for formatted text between separate replacements. That property test found
  a repeated-letter alignment case; token matching fixes it, with an explicit example retained.

Repeated 503s motivated an **opt-in**, single-call retry in the manual sample runner only.
A scripted regression proves that a failed review resumes without replaying completed edits.
All ordinary tests and CI remain offline; committed outputs are verified as static artifacts.

Native tools, continuation messages, thinking configuration, and strict JSON-schema review
all worked on the named model. The three successful samples establish smoke/sample behavior;
they are not a statistical reliability evaluation. The broader eval set remains step 8.
