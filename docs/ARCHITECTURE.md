# Architecture, decisions and integrity

Start with the walkthrough in [English](../README.md) or [German](../README.de.md).
This page explains the implementation and the limits of its guarantees.

## The workflow

An explicit LangGraph `StateGraph` runs load → agent/tools → validation → review → save.
`ask_clarification` routes to a separate node calling `interrupt()`. The CLI resumes using
`Command(resume=answer)` with the same thread ID/context, so completed editing tools are not
replayed. `InMemorySaver` supports in-process pauses; it is not durable storage.

Checkpointed `AgentState` contains the instruction, append-only messages, counters and reports.
`AgentContext` contains the mutable `DocumentSession`, paths and limits. The document is never
encoded into checkpointed messages as a mutable Python object; the model receives text views.

Tools run sequentially, including multiple calls in one response. `finish` and
`ask_clarification` must be called alone. Schema/operation errors become tool feedback.
`finish` triggers deterministic validation, then a separate strict JSON-schema model judgment
`{approved, reason}`. The judge receives original text, changes, operation log, user answers
and the actual styles of live touched blocks.

Validation failure or rejection can reset the document to the original and append feedback
plus a fresh view. They share a maximum of two resets by default. Editor turns are capped
across attempts, SDK retries are disabled, and model failures/refusals stop without saving.
`--force` can save the final failed-check attempt after retries, still exiting 1; it does not
override provider errors, clarification or the editor turn limit.

## Why these choices

| Choice | Reason and tradeoff |
|---|---|
| Own OPC package layer (`zipfile` + `lxml`) | Keep non-main part contents unchanged; avoid re-saving the whole package through python-docx. More XML code, but a smaller and testable edit boundary. |
| Quoted text + stable IDs | Natural-language-friendly addressing. Exact matching precedes a normalized fallback. Ambiguity is an error with candidates, never an automatic first match. |
| Run-aware edits | Split/clone formatting at boundaries. Trim unchanged prefixes/suffixes; for separate changes, keep meaningful token anchors. Prepare multi-span replacements in a copied XML context for atomic refusal. |
| Protected-by-default elements | Refuse fields, drawings, references and unknown structures rather than silently lose them. Some valid-looking requests therefore remain unsupported. |
| Narrow paragraph-style changes | Resolve existing catalog styles; change only `w:pPr/w:pStyle`. Preserve other paragraph properties and all runs/objects. |
| Explicit StateGraph | Make clarification, deterministic checks, review, retries and saving visible and testable. No generic code-executing agent. |
| Separate deterministic checks and model review | Code checks document integrity; the model judges intent. Neither a judge approval nor a pass in a small eval establishes general semantic correctness. |
| Scripted models in CI | Test graph behavior without credentials, quota or provider nondeterminism. Live measurements remain manual and bounded. |

IDs (`p0`, `t0.r1.c2.p0`, inserted `n0`, etc.) live only in an in-memory index; nothing is
stamped into XML. Deleted IDs are tombstoned. A fresh load numbers the serialized document
again; IDs after insertion/deletion can differ from those in the session's operation log.

`DocumentSession` keeps original bytes, package/index/styles and successful operation receipts.
Each receipt contains before/after bytes and the target IDs a fresh disk index would assign.
Validation checks final integrity, receipt continuity, per-operation integrity and claimed
effects. This handles several edits to one paragraph, cancelling edits and a disguised buggy
insert/delete that a final paragraph diff might pair as a modification.

## Integrity checks and their limits

1. **Package parts:** same names/order, same compression/date metadata; every non-main part's
   uncompressed bytes remain identical. ZIP compressed streams may be re-deflated and the main
   XML is reserialized. We do not claim byte-identical complete `.docx` files after edits.
2. **Structure/blocks:** the document skeleton without paragraphs is C14N-identical, covering
   tables/cells/section wrappers. Paragraphs are aligned within containers. With touched IDs,
   changed originals must be accounted for, and inserted paragraphs cannot exceed logged inserts.
3. **Formatting:** paragraph properties stay identical except exact declared per-block style
   assignments. Equal-text paragraphs compare every character's run/container formatting.
   For changed text, equal stretches shorter than three characters are skipped; comparisons
   are skipped for rewritten paragraph pairs below 0.5 text similarity. Alignment is heuristic,
   not a proof of edit intent. Unit/property tests separately assert exact formatting outside
   edited spans and inherited formatting.
4. **Protected content:** fingerprints/multisets cover images, fields, references, bookmarks,
   links, section properties and other shared protected categories. Intended removals are
   limited to changed/touched paragraphs; new/altered protected content is still checked.
5. **Output hazards:** detect newly introduced invalid body/cell endings, touching tables and
   whitespace Word would drop. Pre-existing hazards are not blamed on the edit.
6. **Operation effects:** modified text must match its receipt; inserted/deleted paragraphs
   must have the claimed effect. Unjournaled mutations fail validation.

`python-docx` is used for fixture creation and reopening assertions in tests/evaluations only.
It never saves agent output. Word/LibreOffice rendering and pixel comparison are not part of CI.

The standalone `verify` command assumes text edits with unchanged paragraph styles. An edit
session supplies its exact `style_changes` map when heading formatting is intended; standalone
`verify` has no matching CLI flag. A generic permit-all-formatting switch is deliberately absent.

## Files, models and boundaries

- `src/docx_agent/document/`: package loading, block index, run map, operations, catalog, view,
  integrity checks. No LLM dependency in the editing mechanism.
- `src/docx_agent/agent/`: state/context, typed tools, prompts, providers and graph. Exposes
  document actions only, with no shell or arbitrary code execution.
- `tests/`: unit/property tests plus scripted graph/CLI tests; synthetic fixtures only.
- `samples/`: reproducible synthetic inputs and actual live outputs/reports/provenance.
- `evals/`: strict synthetic cases, independent scoring, manual runner, qualified baseline.

The main document body and table-cell text are editable. Other parts, including styles,
relationships, images and headers/footers, are preserved. No header/footer or footnote editing,
image/table insertion, hyperlink-target editing, style creation or automatic TOC rebuilding is
provided. Existing tracked-change/field runs are protected; text operations do not cross paragraphs.

Gemini is the default provider. Anthropic is optional but unverified live. The model selects
existing operations; it cannot invent executable code or alter arbitrary filesystem paths.
The CLI owns output/report paths. Saving stages both files before publishing, rolls back
ordinary publication errors and rejects input/output aliases; the pair is not crash-atomic.

Snapshots and context copies cost memory/time proportional to document size and operations.
The implementation targets case-study documents; it offers no large-document performance claim.
Defensive ZIP/XML loading adds part-count/size limits and disables XML entity/network resolution.

## What the evidence establishes

The [sample outputs](../samples/README.md) pass independent integrity checks and reopen with
python-docx. The [eval baseline](../evals/baselines/2026-11-17.md) honestly preserves the initial
5/6 and targeted 1/1 after the prompt-only ambiguity correction. The editor and judge can still
misinterpret scope; that policy has no deterministic semantic guard. See the
[development record](DEVELOPMENT.md) for concrete failures, regressions and fixes.
