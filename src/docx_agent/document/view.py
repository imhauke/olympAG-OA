"""ID-tagged text view of a document: what the agent (and `docx-agent inspect`) sees.

One line per block. Paragraph text is a JSON string so tabs and line breaks are unambiguous
(\\t, \\n) and can be quoted back exactly. Objects appear as tokens like [IMAGE]; they are
placeholders, not text. Paragraphs holding read-only content are flagged at the end of the line.
"""

from __future__ import annotations

import json

from lxml import etree

from docx_agent.document.blocks import BlockIndex, children
from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.runs import OBJECT_CHAR, ParagraphMap
from docx_agent.document.styles import StyleCatalog

_E = etree._Element
_P = qn("w:p")
_TR = qn("w:tr")
_TC = qn("w:tc")
_PPR = qn("w:pPr")

HEADER = (
    'Each line: [block_id] (paragraph style) "text". Quote text exactly as it appears inside '
    "the quotes (\\t = tab, \\n = line break). Tokens like [IMAGE] are placeholders for objects "
    "and cannot be quoted or edited; [read-only: ...] lists protected content in a paragraph."
)
TRUNCATE_AT = 80
LONG_NOTE = (
    f"(Long document: paragraphs shortened to {TRUNCATE_AT} chars. "
    "View a range with from_id/to_id for full text.)"
)


def _paragraph_line(bid: str, p: _E, styles: StyleCatalog | None, limit: int | None) -> str:
    pm = ParagraphMap(p)
    text = "".join(
        f"[{(a.protected or 'object').upper()}]" if a.char == OBJECT_CHAR else a.char
        for a in pm.atoms
    )
    shown = text if limit is None or len(text) <= limit else text[:limit] + "..."
    parts = [f"[{bid}]"]
    ppr = p.find(_PPR)
    if ppr is not None:
        style = ppr.find(qn("w:pStyle"))
        if style is not None:
            sid = style.get(qn("w:val"))
            parts.append(f"({styles.name(sid) if styles else sid})")
        if ppr.find(qn("w:numPr")) is not None:
            parts.append("(list item)")
    parts.append(json.dumps(shown, ensure_ascii=False))
    flags = sorted({a.protected for a in pm.atoms if a.protected and a.char != OBJECT_CHAR})
    if ppr is not None and ppr.find(qn("w:sectPr")) is not None:
        flags.append("section break")
    if flags:
        parts.append(f"[read-only: {', '.join(flags)}]")
    return " ".join(parts)


def _lines(
    index: BlockIndex, ids: list[str], styles: StyleCatalog | None, limit: int | None
) -> list[str]:
    lines = []
    for bid in ids:
        el = index.get(bid)
        indent = "  " * bid.count(".r")
        if el.tag == _P:
            lines.append(indent + _paragraph_line(bid, el, styles, limit))
        else:
            rows = list(children(el, (_TR,)))
            cols = max((len(list(children(r, (_TC,)))) for r in rows), default=0)
            lines.append(f"{indent}[{bid}] TABLE {len(rows)} rows x {cols} cols")
    return lines


def render(
    index: BlockIndex,
    styles: StyleCatalog | None = None,
    *,
    from_id: str | None = None,
    to_id: str | None = None,
    max_chars: int = 20_000,
) -> str:
    """The view, optionally restricted to blocks from_id..to_id (inclusive, document order).

    If it exceeds `max_chars` (the fixed HEADER not counted), long paragraphs are shortened,
    then trailing blocks are cut with a note on how to see the rest.
    """
    ids = index.ids()
    for bound in (from_id, to_id):
        if bound is not None:
            index.get(bound)  # raises unknown/deleted block
    lo = ids.index(from_id) if from_id else 0
    hi = ids.index(to_id) + 1 if to_id else len(ids)
    shown = ids[lo:hi]
    if not shown:
        raise EditError("invalid_args", "from_id comes after to_id; nothing to show.")

    lines = _lines(index, shown, styles, None)
    if len("\n".join(lines)) <= max_chars:
        return "\n".join([HEADER, *lines])

    lines = _lines(index, shown, styles, TRUNCATE_AT)
    note = LONG_NOTE

    def tail(k: int) -> str:
        return f"(... {len(lines) - k} more blocks from {shown[k]}; use from_id={shown[k]}.)"

    if len("\n".join([note, *lines])) <= max_chars:
        return "\n".join([HEADER, note, *lines])
    # One pass: the largest prefix that fits together with its "more blocks" note.
    keep, used = 0, len(note)
    # all lines together do not fit, so at least the last one is always cut
    while keep + 1 < len(lines):
        needed = used + 1 + len(lines[keep]) + 1 + len(tail(keep + 1))
        if needed > max_chars:
            break
        used += 1 + len(lines[keep])
        keep += 1
    return "\n".join([HEADER, note, *lines[:keep], tail(keep)])
