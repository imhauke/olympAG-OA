"""Document-level edit operations: what the agent's tools call.

Text is addressed the way an LLM naturally refers to it: by quoting it. A quote is matched
exactly first; only if there is no exact match anywhere in scope, a forgiving match is tried
(whitespace runs collapsed, typographic quotes/dashes folded, soft hyphens ignored). Matches
never span paragraphs. Several matches without an `occurrence` is an "ambiguous" error that
lists every candidate, so the agent (or the user) can choose instead of us guessing.

Every operation either applies completely and returns an `EditResult`, or raises an
`EditError` and leaves the document unchanged.
"""

from __future__ import annotations

import copy
import difflib
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from lxml import etree

from docx_agent.document.blocks import BlockIndex, children, logical_container
from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.runs import (
    MARKERS,
    SOFT_HYPHEN,
    Attach,
    ParagraphMap,
    check_new_text,
    clean_rpr,
    paragraph_text,
    protected_categories,
    replace_span,
    set_run_text,
)
from docx_agent.document.styles import StyleCatalog

_E = etree._Element

_P = qn("w:p")
_R = qn("w:r")
_TBL = qn("w:tbl")
_PPR = qn("w:pPr")
_RPR = qn("w:rPr")
_SECT_PR = qn("w:sectPr")
_SDT_CONTENT = qn("w:sdtContent")
_P_STYLE = qn("w:pStyle")
_VAL = qn("w:val")

# Protected categories (runs.PROTECTED) a paragraph deletion handles without allow_media:
# range markers are moved to a neighbour; hyperlinks go with their text but are declared.
_MOVED = {"bookmark", "comment range"}
_DECLARED_ONLY = {"hyperlink"}
# Range markers are moved to a neighbouring paragraph instead of being deleted: every marker a
# span edit leaves in place (except proofing marks, which are just spell-check hints) plus the
# move-range markers of tracked moves.
_RANGE_MARKERS = (MARKERS - {qn("w:proofErr")}) | {
    qn(f"w:{t}")
    for t in ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")
}
_PPR_DROP = {_SECT_PR, qn("w:pPrChange")}

MAX_CANDIDATES = 10
_CONTEXT = 30
_ELLIPSIS, _OPEN, _CLOSE = chr(0x2026), chr(0x00AB), chr(0x00BB)


@dataclass(frozen=True)
class EditResult:
    op: str
    block_id: str
    before: str  # block text before the op ("" for an inserted paragraph)
    after: str  # block text after the op ("" for a deleted paragraph)
    touched: tuple[str, ...]  # every block whose XML changed
    removed: tuple[str, ...] = ()  # structures removed on purpose (e.g. "hyperlink", "image")
    style: str | None = None  # explicitly requested paragraph styleId, otherwise unchanged

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.style is None:
            result.pop("style")
        return result


@dataclass(frozen=True)
class Match:
    block_id: str
    start: int
    end: int
    context: str  # surrounding text, the match wrapped in guillemets


# ---------------------------------------------------------------------------- matching


def _chars(*codes: int) -> str:
    return "".join(map(chr, codes))


_FOLD = str.maketrans(
    {
        **dict.fromkeys(_chars(0x2018, 0x2019, 0x201A, 0x201B, 0x2032), "'"),
        **dict.fromkeys(_chars(0x201C, 0x201D, 0x201E, 0x201F, 0x2033), '"'),
        **dict.fromkeys(_chars(0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212), "-"),
        **dict.fromkeys(_chars(0x00A0, 0x2007, 0x202F), " "),
    }
)


def _normalize(text: str) -> tuple[str, list[int]]:
    """Forgiving form of `text` plus, for each kept character, its index in `text`."""
    out: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, raw in enumerate(text):
        if raw == SOFT_HYPHEN:
            continue
        ch = raw.translate(_FOLD)
        is_space = ch.isspace()
        if is_space and prev_space:
            continue
        prev_space = is_space
        out.append(" " if is_space else ch)
        index.append(i)
    return "".join(out), index


def _occurrences(haystack: str, needle: str) -> list[int]:
    found: list[int] = []
    i = haystack.find(needle)
    while i != -1:
        found.append(i)
        i = haystack.find(needle, i + len(needle))
    return found


def _context(text: str, start: int, end: int) -> str:
    left = text[max(0, start - _CONTEXT) : start]
    right = text[end : end + _CONTEXT]
    return (
        (_ELLIPSIS if start > _CONTEXT else "")
        + left
        + _OPEN
        + text[start:end]
        + _CLOSE
        + right
        + (_ELLIPSIS if end + _CONTEXT < len(text) else "")
    )


def find_text(index: BlockIndex, query: str, scope: str | None = None) -> list[Match]:
    """All matches of `query` in the paragraphs of `scope` (default: whole document)."""
    if not query:
        raise EditError("invalid_args", "The text to find must not be empty.")
    texts = [(bid, paragraph_text(index.paragraph(bid))) for bid in index.paragraph_ids(scope)]
    matches = [
        Match(bid, s, s + len(query), _context(text, s, s + len(query)))
        for bid, text in texts
        for s in _occurrences(text, query)
    ]
    if matches:
        return matches
    needle = _normalize(query)[0].strip()
    if not needle:
        return []
    for bid, text in texts:
        norm, idx = _normalize(text)
        for s in _occurrences(norm, needle):
            start, end = idx[s], idx[s + len(needle) - 1] + 1
            matches.append(Match(bid, start, end, _context(text, start, end)))
    return matches


def _resolve(index: BlockIndex, text: str, block_id: str | None, occurrence: int | None) -> Match:
    matches = find_text(index, text, block_id)
    where = f"block {block_id}" if block_id else "the document"
    if not matches:
        raise EditError(
            "not_found",
            f"{text!r} was not found in {where}. Quote the text exactly as view_document shows "
            "it; a match never spans two paragraphs.",
        )
    if occurrence is not None:
        if not 1 <= occurrence <= len(matches):
            raise EditError(
                "invalid_occurrence",
                f"occurrence={occurrence}, but {text!r} occurs {len(matches)} time(s) in {where}.",
            )
        return matches[occurrence - 1]
    if len(matches) > 1:
        raise EditError(
            "ambiguous",
            f"{text!r} occurs {len(matches)} times in {where}. Retry with the block_id and "
            "occurrence of the candidate you mean (occurrence counts within that block), or "
            "ask the user which one.",
            candidates=_candidates(matches[:MAX_CANDIDATES]),
            total=len(matches),
        )
    return matches[0]


def _candidates(matches: list[Match]) -> list[dict[str, Any]]:
    """Candidates with the occurrence number to pass *together with* their block_id."""
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for m in matches:
        seen[m.block_id] = seen.get(m.block_id, 0) + 1
        out.append({"block_id": m.block_id, "occurrence": seen[m.block_id], "context": m.context})
    return out


# ---------------------------------------------------------------------------- text ops


def replace_text(
    index: BlockIndex,
    old: str,
    new: str,
    *,
    block_id: str | None = None,
    occurrence: int | None = None,
) -> EditResult:
    """Replace one occurrence of `old` with `new`; the new text takes the old text's format."""
    m = _resolve(index, old, block_id, occurrence)
    return _span_edit("replace_text", index, m, new)


def delete_text(
    index: BlockIndex,
    text: str,
    *,
    block_id: str | None = None,
    occurrence: int | None = None,
) -> EditResult:
    """Delete one occurrence of `text` (include a neighbouring space to avoid double spaces)."""
    m = _resolve(index, text, block_id, occurrence)
    return _span_edit("delete_text", index, m, "")


def insert_text(
    index: BlockIndex,
    text: str,
    *,
    block_id: str | None = None,
    anchor: str | None = None,
    side: Literal["before", "after"] = "after",
    position: Literal["start", "end"] | None = None,
    occurrence: int | None = None,
) -> EditResult:
    """Insert `text` before/after an anchor quote, or at the start/end of a paragraph.

    The new text takes the formatting of the character before it (after it at the start).
    """
    if not text:
        raise EditError("invalid_args", "The text to insert must not be empty.")
    if (anchor is None) == (position is None):
        raise EditError("invalid_args", "Give exactly one of `anchor` or `position`.")
    if side not in ("before", "after"):
        raise EditError("invalid_args", "`side` must be 'before' or 'after'.")
    if position is not None:
        if position not in ("start", "end"):
            raise EditError("invalid_args", "`position` must be 'start' or 'end'.")
        if block_id is None:
            raise EditError("invalid_args", "`position` needs a paragraph `block_id`.")
        k = 0 if position == "start" else len(paragraph_text(index.paragraph(block_id)))
        return _span_edit("insert_text", index, Match(block_id, k, k, ""), text)
    assert anchor is not None
    m = _resolve(index, anchor, block_id, occurrence)
    k = m.start if side == "before" else m.end
    return _span_edit("insert_text", index, Match(m.block_id, k, k, ""), text)


def _span_edit(op: str, index: BlockIndex, span: Match, new: str) -> EditResult:
    bid = span.block_id
    p = index.paragraph(bid)
    before = paragraph_text(p)
    new = check_new_text(new)  # normalise CRLF first so trimming compares like with like
    start, end, new = minimal_change(before, span.start, span.end, new)
    attach: Attach = "auto"
    if start == end and span.start < span.end:  # a replace trimmed to a pure insert
        attach = "left" if start > span.start else "right"
    changes = _replacement_changes(before[start:end], new, start)
    removed = (
        _replace_changes(p, changes, span.start)
        if len(changes) > 1
        else replace_span(p, start, end, new, attach=attach)
    )
    return EditResult(op, bid, before, paragraph_text(p), (bid,), tuple(removed))


def _replacement_changes(old: str, new: str, offset: int) -> list[tuple[int, int, str]]:
    """Keep meaningful unchanged anchors; tiny coincidences belong to their replacement.

    For example, do not treat the zeroes shared by '09:00' and '10:30' as anchors:
    the time is one replacement inheriting its original run's format.
    """
    # Match whole tokens first so repeated letters in different words cannot
    # displace an unchanged phrase between the edited words.
    old_tokens = list(re.finditer(r"\w+|[^\w\s]+|\s+", old))
    new_tokens = list(re.finditer(r"\w+|[^\w\s]+|\s+", new))
    old_offsets = [match.start() for match in old_tokens] + [len(old)]
    new_offsets = [match.start() for match in new_tokens] + [len(new)]
    groups: list[tuple[int, int, int, int]] = []
    for tag, a, b, c, d in difflib.SequenceMatcher(
        a=[match.group() for match in old_tokens],
        b=[match.group() for match in new_tokens],
        autojunk=False,
    ).get_opcodes():
        if tag == "equal":
            continue
        lo, hi, new_lo, new_hi = old_offsets[a], old_offsets[b], new_offsets[c], new_offsets[d]
        if groups and lo - groups[-1][1] < 3:
            previous = groups.pop()
            groups.append((previous[0], hi, previous[2], new_hi))
        else:
            groups.append((lo, hi, new_lo, new_hi))
    return [(offset + lo, offset + hi, new[new_lo:new_hi]) for lo, hi, new_lo, new_hi in groups]


def _replace_changes(p: _E, changes: list[tuple[int, int, str]], quote_start: int) -> list[str]:
    """Preserve unchanged runs between disjoint changes, atomically.

    Work in a copied XML context so a later protected-content refusal cannot leave a
    partial edit. Copying ancestors keeps fields opened in preceding paragraphs protected.
    Only the successful paragraph's children are transferred back; its stable ID survives.
    """
    path: list[int] = []
    root = p
    while (parent := root.getparent()) is not None:
        path.append(parent.index(root))
        root = parent
    candidate = copy.deepcopy(root)
    for position in reversed(path):
        candidate = candidate[position]
    removed: set[str] = set()
    for start, end, new in reversed(changes):
        attach: Attach = "auto"
        if start == end:
            attach = "left" if start > quote_start else "right"
        removed.update(replace_span(candidate, start, end, new, attach=attach))
    p[:] = list(candidate)
    return sorted(removed)


def minimal_change(text: str, start: int, end: int, new: str) -> tuple[int, int, str]:
    """Shrink "replace text[start:end] with new" to the characters that really change.

    LLMs quote generously ("replace 'Max attended' with 'Erika attended'"). Trimming the
    common prefix/suffix keeps unchanged characters, their runs and their formatting
    untouched, and lets an edit next to protected content (a field, an image) succeed when
    the protected part itself is quoted but not changed.
    """
    old = text[start:end]
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return start + prefix, end - suffix, new[prefix : len(new) - suffix]


# ---------------------------------------------------------------------------- paragraph ops


def set_paragraph_style(
    index: BlockIndex, block_id: str, style: str, styles: StyleCatalog
) -> EditResult:
    """Apply an existing paragraph style, retaining runs and every other paragraph property."""
    p = index.paragraph(block_id)
    sid = styles.resolve(style)
    if sid is None:
        raise EditError(
            "unknown_style",
            f"There is no paragraph style {style!r} in this document.",
            available=styles.display_names(),
        )
    text = paragraph_text(p)
    ppr = p.find(_PPR)
    if ppr is None:
        ppr = etree.Element(_PPR)
        p.insert(0, ppr)
    pstyle = ppr.find(_P_STYLE)
    if pstyle is None:
        pstyle = etree.Element(_P_STYLE)
        ppr.insert(0, pstyle)
    pstyle.set(_VAL, sid)
    return EditResult("set_paragraph_style", block_id, text, text, (block_id,), style=sid)


def insert_paragraph(
    index: BlockIndex,
    anchor_id: str,
    text: str,
    *,
    side: Literal["before", "after"] = "after",
    style: str | None = None,
    styles: StyleCatalog | None = None,
) -> EditResult:
    """Insert a new paragraph next to a paragraph or table.

    Without `style`, the new paragraph copies the anchor paragraph's properties (style, list
    numbering, alignment; never its section break) and the formatting of its first text run,
    so a new list item looks like its neighbours. With `style` (display name or styleId), it
    gets just that paragraph style and default run formatting.
    """
    if side not in ("before", "after"):
        raise EditError("invalid_args", "`side` must be 'before' or 'after'.")
    anchor = index.get(anchor_id)
    text = check_new_text(text)
    new_p = etree.Element(_P)
    rpr: _E | None = None
    if style is not None:
        catalog = styles or StyleCatalog({})
        sid = catalog.resolve(style)
        if sid is None:
            raise EditError(
                "unknown_style",
                f"There is no paragraph style {style!r} in this document.",
                available=catalog.display_names(),
            )
        if sid != catalog.default_id:
            etree.SubElement(etree.SubElement(new_p, _PPR), _P_STYLE).set(_VAL, sid)
    elif anchor.tag == _P:
        ppr = anchor.find(_PPR)
        if ppr is not None:
            new_ppr = copy.deepcopy(ppr)
            for child in list(new_ppr):
                if child.tag in _PPR_DROP:
                    new_ppr.remove(child)
            mark_rpr = new_ppr.find(_RPR)
            if mark_rpr is not None:
                cleaned = clean_rpr(mark_rpr)
                assert cleaned is not None
                new_ppr.replace(mark_rpr, cleaned)
            if len(new_ppr) or new_ppr.attrib:
                new_p.append(new_ppr)
        pm = ParagraphMap(anchor)
        first = next((r for r in pm.runs if r.end > r.start and not r.protected), None)
        if first is not None:
            rpr = clean_rpr(first.el.find(_RPR))
    if text:
        run = etree.SubElement(new_p, _R)
        if rpr is not None:
            run.append(rpr)
        set_run_text(run, text)
    if side == "after":
        anchor.addnext(new_p)
    else:
        anchor.addprevious(new_p)
    bid = index.register(new_p)
    return EditResult("insert_paragraph", bid, "", paragraph_text(new_p), (bid,))


def delete_paragraph(index: BlockIndex, block_id: str, *, allow_media: bool = False) -> EditResult:
    """Delete a whole paragraph.

    Refused for paragraphs carrying a section break, when the table cell / document / content
    control would no longer end with a paragraph (Word requires one), and, unless
    `allow_media`, for paragraphs containing images, fields, footnote/comment references,
    equations or tracked changes. Bookmarks and comment ranges move to a neighbouring
    paragraph instead of disappearing.
    """
    p = index.paragraph(block_id)
    ppr = p.find(_PPR)
    if ppr is not None and ppr.find(_SECT_PR) is not None:
        raise EditError(
            "protected",
            f"{block_id} carries a section break (page size/orientation, headers). Deleting "
            "it would merge two sections; edit its text instead.",
            protected="section break",
        )
    pm = ParagraphMap(p)
    categories = protected_categories(p)
    reasons = {a.protected for a in pm.atoms} | {r.protected for r in pm.runs}
    reasons |= categories - _MOVED - _DECLARED_ONLY
    contains = sorted(r for r in reasons if r)
    if contains and not allow_media:
        raise EditError(
            "protected",
            f"{block_id} contains {', '.join(contains)}. Only delete it if the user explicitly "
            "asked to remove that content (then pass allow_media=true).",
            protected=contains,
        )

    _check_still_ends_with_paragraph(p, block_id)
    touched = [block_id]
    markers = list(p.iter(*_RANGE_MARKERS))
    if markers:
        neighbour = _move_markers(p, markers)
        if neighbour is None:
            raise EditError(
                "protected",
                f"{block_id} holds bookmarks/comment ranges and has no neighbouring paragraph "
                "to keep them in; delete its text instead.",
            )
        nid = index.id_of(neighbour)
        if nid is not None:
            touched.append(nid)
    removed = set(contains) | (categories & _DECLARED_ONLY)
    before = pm.text
    index.remove(block_id)
    return EditResult(
        "delete_paragraph", block_id, before, "", tuple(touched), tuple(sorted(removed))
    )


def _check_still_ends_with_paragraph(p: _E, block_id: str) -> None:
    blocks = list(children(logical_container(p), (_P, _TBL)))
    i = blocks.index(p)
    if 0 < i < len(blocks) - 1 and blocks[i - 1].tag == _TBL and blocks[i + 1].tag == _TBL:
        raise EditError(
            "protected",
            f"{block_id} is the only paragraph between two tables; deleting it would make "
            "Word merge the tables. Delete its text instead.",
        )
    containers = [logical_container(p)]
    parent = p.getparent()
    if parent is not None and parent.tag == _SDT_CONTENT:
        containers.append(parent)
    for container in containers:
        rest = [b for b in children(container, (_P, _TBL)) if b is not p]
        if not rest or rest[-1].tag != _P:
            where = {qn("w:tc"): "table cell", _SDT_CONTENT: "content control"}.get(
                container.tag, "document"
            )
            raise EditError(
                "protected",
                f"{block_id} cannot be deleted: a {where} must end with a paragraph. "
                "Delete its text instead.",
            )


def _move_markers(p: _E, markers: list[_E]) -> _E | None:
    """Move range markers out of `p`: to the start of the next paragraph in the same
    container, else to the end of the previous one. Returns the receiving paragraph."""
    blocks = list(children(logical_container(p), (_P, _TBL)))
    i = blocks.index(p)
    nxt = blocks[i + 1] if i + 1 < len(blocks) else None
    prev = blocks[i - 1] if i > 0 else None
    if nxt is not None and nxt.tag == _P:
        at = 1 if nxt.find(_PPR) is not None else 0
        for k, marker in enumerate(markers):
            nxt.insert(at + k, marker)
        return nxt
    if prev is not None and prev.tag == _P:
        for marker in markers:
            prev.append(marker)
        return prev
    return None
