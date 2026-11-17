# Architecture and integrity

## Control flow

```mermaid
flowchart TD
    Load[Load original] --> Agent[Model]
    Agent --> Tools[Sequential document tools]
    Tools --> Agent
    Tools --> Clarify[interrupt]
    Clarify -->|Command resume| Agent
    Tools -->|finish: alone or ending a successful batch| Validate[Integrity and operation effects]
    Validate -->|Pass| Review[Structured model review]
    Review -->|Approve| Save[Document and report]
    Validate -->|Fail with retries left| Reset[Reset original and append feedback]
    Review -->|Reject with retries left| Reset
    Reset --> Agent
    Validate -->|Exhausted| Stop[No output]
    Review -->|Exhausted| Stop
```

Default flow; `--force` can publish a final rejected attempt, still exiting 1. It does not
override provider failures, clarification or the turn cap. SDK retries are disabled.

## Implementation decisions

| Layer | Choice and reason |
|---|---|
| [`document/`](../src/docx_agent/document/) | Own OPC/ZIP + lxml editing. Non-main parts retain original bytes; python-docx is only a fixture/assertion dependency. |
| Addressing | Exact quotes, then normalized fallback (dashes, curly quotes, tabs/NBSP, soft hyphens). A fallback match writes back the document's own characters wherever the text is unchanged, so a typed '-' never retypes an en dash. Stable in-memory block IDs, inserted `n*` IDs, deleted tombstones; no IDs stamped into XML. |
| Run editing | Trim unchanged context; separate changes around token anchors; prepare multi-span replacements in a copied XML context for atomic refusal. |
| Styles | Apply an existing catalog style by changing only `w:pPr/w:pStyle`; other properties and runs remain protected. |
| [`agent/`](../src/docx_agent/agent/) | Explicit StateGraph with declared conditional routes (the compiled graph's Mermaid view matches the diagram above; a test pins its edges), typed tools, no shell/code execution. Mutable `DocumentSession` in runtime context; append-only checkpointed messages. A turn may apply several edits and end with `finish` (one model round trip less); `finish` is cancelled if any earlier call in that turn failed, and `ask_clarification` must stand alone. |
| [`menu.py`](../src/docx_agent/menu.py) | Interactive terminal menu (`docx-agent` without a command), built with Rich. Lists `documents/`, edited copies and the samples; streams the same graph with `stream_mode="updates"`, asks clarifications inline, writes `documents/edited/<name>.edited.docx` without overwriting earlier results. Factories are injected so tests drive it with a scripted model. |
| [`web/`](../src/docx_agent/web/) | FastAPI + static HTML/CSS/JS (no build step). Jobs run the same graph in a worker thread; `stream_mode="updates"` drives the progress view, `interrupt()`/`Command(resume=...)` the inline question. Uploads are parsed in memory; results stay in memory for the 12 most recent jobs; history is metadata-only JSON. `/docs` serves a static page on the project's decisions. Localhost hardening: `127.0.0.1` bind, Host allow-list, custom header on mutating requests (no CORS). |
| Validation/review | Deterministic checks establish integrity; a separate JSON-schema judge assesses intent using changes, original text and actual touched-block styles. |

The session retains original bytes and per-operation receipts with before/after snapshots and
disk-ID mappings. Validation checks receipt continuity and each claimed effect, including
repeated/cancelling edits. This catches buggy insert/delete pairs that a final diff could call
a modification. Retry resets the document but appends feedback to the conversation.

`InMemorySaver` supports clarification in the same process/context; there is no durable resume.
Sequential tools live outside the interrupt node, so answers do not replay completed edits.
The CLI owns file paths. Saving stages both artifacts and rolls back ordinary publication errors;
the two-file publication is not crash-atomic.

## Integrity checks and limits

1. Same ZIP part names/order and compression/date metadata; every non-main part's **uncompressed
   content** is identical. Re-deflation/main XML serialization may change complete ZIP bytes.
2. Canonical XML equality for untouched paragraphs and the table/cell/section skeleton.
   Touched IDs and inserted counts must account for observed changes.
3. Paragraph properties stay identical except exact declared style assignments. Unchanged text
   keeps its formatting: (character, formatting) pairs are aligned first, so unchanged text pairs
   with itself; text left unmatched but identical (a whole region, or ≥ 3 characters) changed
   format. Pairs below 0.5 text similarity count as rewritten. Paragraph alignment is heuristic.
   Property tests additionally check exact formatting outside spans.
4. Protected-content fingerprints cover drawings, fields, references, links, bookmarks, sections
   and revisions. Intended removals are restricted to changed/touched blocks; unknown content is read-only.
5. New structural/whitespace hazards and each operation's actual before/after effect are checked.
   Tests/evals reopen outputs with python-docx. No automated Word rendering: a manual check on a
   macOS-written `.docx` (no `styles.xml`) rendered input and output via QuickLook and reopened
   them with Apple's parser; Microsoft Word itself was not available.

Standalone `verify` assumes unchanged styles. Session checks accept exact `style_changes` permissions;
the CLI has no matching flag. Integrity and model approval do not guarantee correct user intent.
Snapshot/tree-copy costs target case-study documents, not large-document performance guarantees.
