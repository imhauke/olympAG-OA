"""Turn integrity-report changes into word-level segments for display."""

from __future__ import annotations

import difflib
import re
from typing import Any, Literal

from docx_agent.document.runs import OBJECT_CHAR

Kind = Literal["equal", "insert", "delete"]
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")
REWRITE_RATIO = 0.5  # below this word similarity, show old and new text whole


def _display(text: str) -> str:
    return text.replace(OBJECT_CHAR, "[object]")


def segments(before: str, after: str) -> list[dict[str, str]]:
    """Word-level diff: [{'kind': 'equal'|'insert'|'delete', 'text': ...}], merged runs."""
    a, b = _TOKEN.findall(_display(before)), _TOKEN.findall(_display(after))
    out: list[dict[str, str]] = []

    def push(kind: Kind, text: str) -> None:
        if not text:
            return
        if out and out[-1]["kind"] == kind:
            out[-1]["text"] += text
        else:
            out.append({"kind": kind, "text": text})

    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    words_a = [t for t in a if t[0].isalnum()]
    words_b = [t for t in b if t[0].isalnum()]
    words = difflib.SequenceMatcher(None, words_a, words_b, autojunk=False)
    if words_a and words_b and words.ratio() < REWRITE_RATIO:
        # Mostly rewritten: old text struck through, then the new text, reads better than an
        # interleaving of tiny fragments.
        push("delete", "".join(a))
        push("insert", "".join(b))
        return out
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            push("equal", "".join(a[i1:i2]))
        else:
            push("delete", "".join(a[i1:i2]))
            push("insert", "".join(b[j1:j2]))
    return out


def location(block_id: str | None) -> str:
    """'p3' -> 'Paragraph 4'; 't0.r1.c2.p0' -> 'Table 1 · row 2, cell 3'."""
    if not block_id:
        return "Document"
    parts = block_id.split(".")
    if len(parts) == 1:
        number = int(parts[0][1:]) + 1 if parts[0][1:].isdigit() else 0
        return f"Paragraph {number}" if parts[0].startswith("p") else "Block"
    table = re.match(r"t(\d+)", parts[0])
    row = re.match(r"r(\d+)", parts[1]) if len(parts) > 1 else None
    cell = re.match(r"c(\d+)", parts[2]) if len(parts) > 2 else None
    if table and row and cell:
        return f"Table {int(table[1]) + 1} · row {int(row[1]) + 1}, cell {int(cell[1]) + 1}" + (
            " (nested)" if len(parts) > 4 else ""
        )
    return "Table"


def present(change: dict[str, Any]) -> dict[str, Any]:
    kind = change["kind"]
    block = change["after_id"] if kind != "deleted" else change["before_id"]
    return {
        "kind": kind,
        "location": location(block),
        "segments": segments(change["before_text"], change["after_text"]),
    }
