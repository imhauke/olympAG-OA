"""Run-aware text editing inside one paragraph (`w:p`).

A paragraph's visible text is spread over runs (`w:r`), each carrying its own formatting
(`w:rPr`). `ParagraphMap` flattens it into *atoms* (one per visible character) that remember
which run and XML node they came from. `replace_span` edits a character range by splitting the
boundary runs (cloning their `w:rPr`), so every character outside the range keeps its exact
formatting and new text inherits the formatting of the text it replaces.

Some content must never change through a text edit. It is *protected*: images and embedded
objects, fields (instruction and cached result), footnote/endnote/comment references, tracked
changes, page/column breaks and any run child we do not recognise. Protected content still
occupies offsets (objects appear as U+FFFC) so positions stay honest, but an edit that would
touch it is refused with an `EditError`. Non-run siblings such as bookmarks, proofing marks
and comment ranges are never moved or removed by a span edit.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from lxml import etree

from docx_agent.document.errors import EditError
from docx_agent.document.ns import W, qn

_E = etree._Element

OBJECT_CHAR = chr(0xFFFC)
Attach = Literal["auto", "left", "right"]
NO_BREAK_HYPHEN = chr(0x2011)
SOFT_HYPHEN = chr(0x00AD)

_P = qn("w:p")
_R = qn("w:r")
_T = qn("w:t")
_RPR = qn("w:rPr")
_PPR = qn("w:pPr")
_TAB = qn("w:tab")
_BR = qn("w:br")
_CR = qn("w:cr")
_NO_BREAK_HYPHEN = qn("w:noBreakHyphen")
_SOFT_HYPHEN = qn("w:softHyphen")
_FLD_CHAR = qn("w:fldChar")
_INSTR = qn("w:instrText")
_LAST_RENDERED = qn("w:lastRenderedPageBreak")
_SDT = qn("w:sdt")
_SDT_CONTENT = qn("w:sdtContent")
_XML_SPACE = qn("xml:space")

# Run children that occupy no characters.
_ZERO_WIDTH = {_RPR, _LAST_RENDERED, _FLD_CHAR, _INSTR, qn("w:delInstrText")}
# Run children that a plain text run may consist of (used to decide whether runs can merge).
_PLAIN = {_RPR, _T, _TAB, _BR, _CR, _NO_BREAK_HYPHEN, _SOFT_HYPHEN, _LAST_RENDERED}
# Inline containers whose runs are ordinary, editable text.
_TRANSPARENT = {qn(f"w:{t}") for t in ("hyperlink", "smartTag", "customXml", "dir", "bdo")}
# Inline containers whose runs are visible but read-only.
_PROTECTED_CONTAINERS = {
    qn("w:ins"): "tracked change",
    qn("w:moveTo"): "tracked change",
    qn("w:fldSimple"): "field",
}
# Non-run siblings that may sit between the runs of an edited span (left in place).
MARKERS = {
    qn(f"w:{t}")
    for t in (
        "bookmarkStart",
        "bookmarkEnd",
        "proofErr",
        "commentRangeStart",
        "commentRangeEnd",
        "permStart",
        "permEnd",
    )
}
_OBJECT_REASONS = {
    "drawing": "image",
    "pict": "image",
    "object": "embedded object",
    "AlternateContent": "embedded object",
    "footnoteReference": "footnote reference",
    "endnoteReference": "endnote reference",
    "commentReference": "comment",
    "annotationRef": "comment",
    "sym": "symbol",
    "ptab": "positional tab",
}
_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

# The single source of truth for protected document content: element -> (category, how the
# integrity checker fingerprints it). ops.delete_paragraph refuses/declares by these categories
# and integrity.check verifies them, so the two can never disagree.
PROTECTED: dict[str, tuple[str, Literal["xml", "attrs", "text"]]] = {
    qn("w:drawing"): ("image", "xml"),
    qn("w:pict"): ("image", "xml"),
    qn("w:object"): ("embedded object", "xml"),
    f"{{{_MC}}}AlternateContent": ("embedded object", "xml"),
    qn("w:fldChar"): ("field", "attrs"),
    qn("w:instrText"): ("field", "text"),
    qn("w:fldSimple"): ("field", "attrs"),
    qn("w:footnoteReference"): ("footnote reference", "attrs"),
    qn("w:endnoteReference"): ("endnote reference", "attrs"),
    qn("w:commentReference"): ("comment", "attrs"),
    qn("w:commentRangeStart"): ("comment range", "attrs"),
    qn("w:commentRangeEnd"): ("comment range", "attrs"),
    qn("w:bookmarkStart"): ("bookmark", "attrs"),
    qn("w:bookmarkEnd"): ("bookmark", "attrs"),
    qn("w:hyperlink"): ("hyperlink", "attrs"),
    qn("w:sectPr"): ("section break", "xml"),
    qn("w:ins"): ("tracked change", "attrs"),
    qn("w:del"): ("tracked change", "xml"),
    qn("w:moveFrom"): ("tracked change", "xml"),
    qn("w:moveTo"): ("tracked change", "attrs"),
    f"{{{_MATH}}}oMath": ("equation", "xml"),
    qn("w:sym"): ("symbol", "attrs"),
}


def protected_categories(el: _E) -> set[str]:
    """Categories of protected content inside `el` (including `el` itself)."""
    return {PROTECTED[x.tag][0] for x in el.iter(*PROTECTED)}


_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff\ufffc]")


@dataclass(frozen=True)
class Atom:
    """One visible character of a paragraph."""

    char: str
    run: int  # index into ParagraphMap.runs
    node: _E  # the run child that produced it (w:t, w:tab, w:drawing, ...)
    offset: int  # character index inside node.text (w:t only)
    protected: str | None  # reason it must not be edited, or None


@dataclass(frozen=True)
class RunInfo:
    el: _E
    start: int  # first atom index
    end: int  # one past the last atom index
    protected: str | None  # reason the whole run is read-only (field, tracked change, ...)
    field: int | None  # number of the complex field this run belongs to


class ParagraphMap:
    """Character map of one paragraph. Rebuild it after every structural change."""

    def __init__(self, p: _E) -> None:
        self.p = p
        self.runs: list[RunInfo] = []
        self.atoms: list[Atom] = []
        # A complex field (e.g. a TOC) may open in an earlier paragraph and close in a later
        # one: carry the open-field depth in from everything that precedes this paragraph.
        depth = open_fields_before(p)
        fields = 0 if depth else -1
        for run, inherited in _iter_runs(p, None):
            field = fields if depth else None
            for fc in run.iterchildren(_FLD_CHAR):
                kind = fc.get(qn("w:fldCharType"))
                if kind == "begin":
                    if depth == 0:
                        fields += 1
                        field = fields
                    depth += 1
                elif kind == "end" and depth:
                    depth -= 1
            has_instr = run.find(_INSTR) is not None
            protected = inherited or ("field" if field is not None or has_instr else None)
            start = len(self.atoms)
            self.atoms.extend(_atoms(run, len(self.runs), protected))
            self.runs.append(RunInfo(run, start, len(self.atoms), protected, field))

    @property
    def text(self) -> str:
        return "".join(a.char for a in self.atoms)


_W_NS = {"w": W}


def open_fields_before(p: _E) -> int:
    """Number of complex fields opened (fldChar begin) before `p` and not yet closed."""
    begins = p.xpath("count(preceding::w:fldChar[@w:fldCharType='begin'])", namespaces=_W_NS)
    ends = p.xpath("count(preceding::w:fldChar[@w:fldCharType='end'])", namespaces=_W_NS)
    assert isinstance(begins, float)
    assert isinstance(ends, float)
    return max(0, int(begins - ends))


def _iter_runs(container: _E, inherited: str | None) -> Iterator[tuple[_E, str | None]]:
    for child in container:
        tag = child.tag
        if tag == _R:
            yield child, inherited
        elif tag in _TRANSPARENT:
            yield from _iter_runs(child, inherited)
        elif tag == _SDT:
            content = child.find(_SDT_CONTENT)
            if content is not None:
                yield from _iter_runs(content, inherited)
        elif tag in _PROTECTED_CONTAINERS:
            yield from _iter_runs(child, inherited or _PROTECTED_CONTAINERS[tag])
        # w:del / w:moveFrom (deleted text), w:oMath, markers, ...: not visible run text.


def _atoms(run: _E, index: int, protected: str | None) -> Iterator[Atom]:
    for child in run:
        tag = child.tag
        if not isinstance(tag, str) or tag in _ZERO_WIDTH:
            continue
        if tag == _T:
            for i, ch in enumerate(child.text or ""):
                yield Atom(ch, index, child, i, protected)
        elif tag == _TAB:
            yield Atom("\t", index, child, 0, protected)
        elif tag == _BR:
            kind = child.get(qn("w:type"))
            reason = None if kind in (None, "textWrapping") else f"{kind} break"
            yield Atom("\n", index, child, 0, protected or reason)
        elif tag == _CR:
            yield Atom("\n", index, child, 0, protected)
        elif tag == _NO_BREAK_HYPHEN:
            yield Atom(NO_BREAK_HYPHEN, index, child, 0, protected)
        elif tag == _SOFT_HYPHEN:
            yield Atom(SOFT_HYPHEN, index, child, 0, protected)
        else:
            local = etree.QName(tag).localname
            reason = _OBJECT_REASONS.get(local, f"special element <{local}>")
            yield Atom(OBJECT_CHAR, index, child, 0, protected or reason)


# ---------------------------------------------------------------------------- queries


def paragraph_text(p: _E) -> str:
    return ParagraphMap(p).text


def rpr_key(run: _E) -> bytes:
    """Canonical formatting of a run: C14N of its w:rPr (an empty rPr equals none)."""
    rpr = run.find(_RPR)
    if rpr is None or (len(rpr) == 0 and not rpr.attrib):
        return b""
    return etree.tostring(rpr, method="c14n")


def _context_key(run: _E, p: _E) -> bytes:
    """Inline containers (hyperlink, ...) around a run, with their attributes."""
    parts: list[str] = []
    el = run.getparent()
    while el is not None and el is not p:
        parts.append(f"{etree.QName(el.tag).localname}{sorted(el.attrib.items())!r}")
        el = el.getparent()
    return "/".join(reversed(parts)).encode()


def char_formats(p: _E) -> list[tuple[str, bytes]]:
    """(character, effective formatting key) for every visible character of a paragraph.

    Two paragraphs with equal lists look identical character by character, however their
    runs happen to be split. This is what "formatting outside the edited span is unchanged"
    is measured against.
    """
    pm = ParagraphMap(p)
    keys = [_context_key(r.el, p) + b"|" + rpr_key(r.el) for r in pm.runs]
    return [(a.char, keys[a.run]) for a in pm.atoms]


def check_new_text(text: str) -> str:
    """Validate text that will be written into the document; returns it with CR/CRLF -> LF."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    bad = _INVALID_XML_CHARS.search(text)
    if bad:
        raise EditError(
            "invalid_text",
            f"The new text contains an unsupported character (U+{ord(bad.group()):04X}). "
            "Use plain text; placeholders like U+FFFC stand for objects and cannot be typed.",
        )
    return text


# ---------------------------------------------------------------------------- editing


def replace_span(
    p: _E, start: int, end: int, new_text: str, *, attach: Attach = "auto"
) -> list[str]:
    """Replace characters [start, end) of paragraph `p` with `new_text`.

    start == end inserts; new_text == "" deletes. In new_text, "\\t" becomes a tab and "\\n"
    a line break. For inserts, `attach` says which neighbour the new text belongs to:
    "auto" keeps text at the edge of a hyperlink (or other inline container) outside it;
    "left"/"right" join the preceding/following run, even inside a container (used when a
    replace was trimmed to a pure insert, so "example.org" -> "example.org/x" stays a link).

    Returns the local names of inline containers that the edit emptied and therefore removed
    (e.g. ["hyperlink"]). Raises EditError (document unchanged) when the span touches
    protected content or crosses an inline-container boundary.
    """
    new_text = check_new_text(new_text)
    pm = ParagraphMap(p)
    if not 0 <= start <= end <= len(pm.atoms):
        raise EditError(
            "invalid_span",
            f"span [{start}, {end}) is outside the paragraph text (0..{len(pm.atoms)})",
        )
    if start == end:
        if new_text:
            _insert(p, start, new_text, attach)
        return []
    _check_span(pm, start, end)
    _split_at(p, end)
    _split_at(p, start)
    pm = ParagraphMap(p)
    first, last = pm.atoms[start].run, pm.atoms[end - 1].run
    span = [r.el for r in pm.runs[first : last + 1]]
    container = span[0].getparent()
    assert container is not None
    before = span[0].getprevious()
    if new_text:
        host = span[0]
        set_run_text(host, new_text)
        for el in span[1:]:
            container.remove(el)
        _merge_around(host)
        return []
    for el in span:
        container.remove(el)
    if before is not None and before.tag == _R:
        _merge_around(before)
    return _drop_if_empty(container, p)


def _check_span(pm: ParagraphMap, start: int, end: int) -> None:
    atoms = pm.atoms[start:end]
    runs = pm.runs[atoms[0].run : atoms[-1].run + 1]
    for reason in [a.protected for a in atoms] + [r.protected for r in runs]:
        if reason:
            raise EditError(
                "protected",
                f"The text to change includes protected content ({reason}). Edit only the "
                "plain text around it, in separate operations.",
                protected=reason,
            )
    container = runs[0].el.getparent()
    if any(r.el.getparent() is not container for r in runs):
        raise EditError(
            "crosses_boundary",
            "The text spans a hyperlink or content-control boundary. Edit the part inside "
            "and the part outside it in separate operations.",
        )
    el = runs[0].el
    while el is not runs[-1].el:
        nxt = el.getnext()
        assert nxt is not None
        el = nxt
        if isinstance(el.tag, str) and el.tag != _R and el.tag not in MARKERS:
            local = etree.QName(el.tag).localname
            raise EditError(
                "crosses_boundary",
                f"The text spans an embedded <{local}> element. Edit the text before and "
                "after it in separate operations.",
            )


def _split_at(p: _E, k: int) -> None:
    """Make offset k a run boundary (no-op if it already is)."""
    pm = ParagraphMap(p)
    if not 0 < k < len(pm.atoms):
        return
    left, right = pm.atoms[k - 1], pm.atoms[k]
    if left.run == right.run:
        _split_run(pm.runs[right.run].el, right.node, right.offset)


def _split_run(run: _E, node: _E, offset: int) -> _E:
    """Split `run` so that `node` (from character `offset` on, for w:t) starts a new run."""
    if offset:
        text = node.text or ""
        tail = _shallow_copy(node)
        node.text, tail.text = text[:offset], text[offset:]
        _preserve_space(node)
        _preserve_space(tail)
        node.addnext(tail)
        node = tail
    right = _shallow_copy(run)
    rpr = run.find(_RPR)
    if rpr is not None:
        right.append(copy.deepcopy(rpr))
    moving = [node, *node.itersiblings()]
    for el in moving:
        right.append(el)
    run.addnext(right)
    return right


def _shallow_copy(el: _E) -> _E:
    """Same tag and attributes (e.g. xml:space, rsids), no children or text."""
    out = etree.Element(el.tag)
    for key, value in el.items():
        out.set(key, value)
    return out


def _insert(p: _E, k: int, text: str, attach: Attach) -> None:
    pm = ParagraphMap(p)
    left = pm.atoms[k - 1] if k > 0 else None
    right = pm.atoms[k] if k < len(pm.atoms) else None
    if left and right and left.protected and right.protected:
        lr, rr = pm.runs[left.run], pm.runs[right.run]
        same_field = lr.field is not None and lr.field == rr.field
        same_container = _top(lr.el, p) is _top(rr.el, p) and _top(lr.el, p).tag != _R
        if left.run == right.run or same_field or same_container:
            raise EditError(
                "protected",
                f"Cannot insert inside protected content ({left.protected}). Choose an "
                "anchor in plain text before or after it.",
                protected=left.protected,
            )
    if left and right and left.run == right.run:
        _split_run(pm.runs[right.run].el, right.node, right.offset)
        pm = ParagraphMap(p)
        left, right = pm.atoms[k - 1], pm.atoms[k]

    lrun = pm.runs[left.run].el if left and not left.protected else None
    rrun = pm.runs[right.run].el if right and not right.protected else None
    if attach == "left" and lrun is not None:
        new = _new_run(lrun, text)
        lrun.addnext(new)
    elif attach == "right" and rrun is not None:
        new = _new_run(rrun, text)
        rrun.addprevious(new)
    elif lrun is not None and (
        lrun.getparent() is p or (rrun is not None and rrun.getparent() is lrun.getparent())
    ):
        new = _new_run(lrun, text)  # plain text, or strictly inside one container
        lrun.addnext(new)
    elif rrun is not None and rrun.getparent() is p:
        new = _new_run(rrun, text)
        rrun.addprevious(new)
    else:
        # At the edge of a container or next to protected content only: a new top-level run
        # outside the neighbouring unit (whole field, whole hyperlink), formatted like the
        # nearest plain top-level text, else like the paragraph mark.
        new = _new_run(_nearest_plain_run(pm, k), text, p.find(f"{_PPR}/{_RPR}"))
        if right is not None:
            _unit_start(pm, right.run, p).addprevious(new)
        elif left is not None:
            _unit_end(pm, left.run, p).addnext(new)
        else:
            p.append(new)
    _merge_around(new)


def _nearest_plain_run(pm: ParagraphMap, k: int) -> _E | None:
    plain = [r for r in pm.runs if r.end > r.start and not r.protected and r.el.getparent() is pm.p]
    before = [r for r in plain if r.end <= k]
    after = [r for r in plain if r.start >= k]
    if before:
        return before[-1].el
    return after[0].el if after else None


def _top(el: _E, p: _E) -> _E:
    """Ancestor-or-self of `el` that is a direct child of paragraph `p`."""
    parent = el.getparent()
    while parent is not None and parent is not p:
        el, parent = parent, parent.getparent()
    return el


def _unit_start(pm: ParagraphMap, run: int, p: _E) -> _E:
    field = pm.runs[run].field
    if field is not None:
        run = next(i for i, r in enumerate(pm.runs) if r.field == field)
    return _top(pm.runs[run].el, p)


def _unit_end(pm: ParagraphMap, run: int, p: _E) -> _E:
    field = pm.runs[run].field
    if field is not None:
        run = max(i for i, r in enumerate(pm.runs) if r.field == field)
    return _top(pm.runs[run].el, p)


_REVISION_TAGS = {qn(f"w:{t}") for t in ("rPrChange", "ins", "del", "moveFrom", "moveTo")}


def clean_rpr(rpr: _E | None) -> _E | None:
    """Deep copy of an rPr without revision marks (they belong to the original text only)."""
    if rpr is None:
        return None
    out = copy.deepcopy(rpr)
    for child in list(out):
        if child.tag in _REVISION_TAGS:
            out.remove(child)
    out.tag = _RPR
    return out


def _new_run(like: _E | None, text: str, rpr: _E | None = None) -> _E:
    """A fresh run holding `text`, formatted like run `like` (else with `rpr`)."""
    source = like.find(_RPR) if like is not None else rpr
    run = etree.Element(_R)
    formatted = clean_rpr(source)
    if formatted is not None:
        run.append(formatted)
    set_run_text(run, text)
    return run


def set_run_text(run: _E, text: str) -> None:
    """Replace the run's content (everything but w:rPr) with `text`."""
    for child in list(run):
        if child.tag != _RPR:
            run.remove(child)
    buf: list[str] = []

    def flush() -> None:
        if buf:
            t = etree.SubElement(run, _T)
            t.text = "".join(buf)
            _preserve_space(t)
            buf.clear()

    for ch in text:
        if ch in "\t\n":
            flush()
            etree.SubElement(run, _TAB if ch == "\t" else _BR)
        else:
            buf.append(ch)
    flush()


def _preserve_space(t: _E) -> None:
    text = t.text or ""
    if text != text.strip() or "  " in text:
        t.set(_XML_SPACE, "preserve")


def _plain(run: _E) -> bool:
    return run.tag == _R and all(c.tag in _PLAIN for c in run)


def _merge_around(run: _E) -> None:
    """Merge `run` with directly adjacent plain runs of identical formatting.

    Undoes the splits made for an edit, so changing one word inside a run leaves one run.
    """
    if not _plain(run):
        return
    prev = run.getprevious()
    while prev is not None and _plain(prev) and rpr_key(prev) == rpr_key(run):
        _absorb(prev, run)
        run = prev
        prev = run.getprevious()
    nxt = run.getnext()
    while nxt is not None and _plain(nxt) and rpr_key(nxt) == rpr_key(run):
        _absorb(run, nxt)
        nxt = run.getnext()


def _absorb(into: _E, other: _E) -> None:
    for child in list(other):
        if child.tag != _RPR:
            into.append(child)
    parent = other.getparent()
    assert parent is not None
    parent.remove(other)
    # Coalesce adjacent w:t so a re-merged run looks like it did before the split.
    prev: _E | None = None
    for child in list(into):
        if prev is not None and prev.tag == _T and child.tag == _T:
            prev.text = (prev.text or "") + (child.text or "")
            _preserve_space(prev)
            into.remove(child)
            continue
        prev = child


def _drop_if_empty(container: _E, p: _E) -> list[str]:
    """Remove inline containers (hyperlink, ...) that an edit left completely empty."""
    removed: list[str] = []
    while container is not p and container.tag in _TRANSPARENT and len(container) == 0:
        parent = container.getparent()
        assert parent is not None
        removed.append(etree.QName(container.tag).localname)
        parent.remove(container)
        container = parent
    return removed
