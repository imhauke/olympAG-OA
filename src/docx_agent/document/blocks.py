"""Stable block IDs for the main document body.

IDs are assigned once, at load, in document order:

* ``p0, p1, ...``   top-level paragraphs
* ``t0, t1, ...``   top-level tables
* ``t0.r1.c2.p0``   paragraph 0 in row 1, cell 2 of table t0 (nested tables continue the pattern)
* ``n0, n1, ...``   paragraphs inserted during the session

Block-level content controls (``w:sdt``) and ``w:customXml`` are walked transparently.
The ID -> element map lives in Python only; nothing is written into the XML. Deleted IDs
are tombstoned, so an ID never changes meaning during a session.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from lxml import etree

from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage, PackageError

_E = etree._Element

_BODY = qn("w:body")
_P = qn("w:p")
_TBL = qn("w:tbl")
_TR = qn("w:tr")
_TC = qn("w:tc")
_SDT = qn("w:sdt")
_SDT_CONTENT = qn("w:sdtContent")
_CUSTOM_XML = qn("w:customXml")

BlockKind = Literal["paragraph", "table"]


def children(container: _E, tags: tuple[str, ...]) -> Iterator[_E]:
    """Children of `container` with one of `tags`, looking through sdt/customXml wrappers."""
    for child in container:
        if child.tag in tags:
            yield child
        elif child.tag == _SDT:
            content = child.find(_SDT_CONTENT)
            if content is not None:
                yield from children(content, tags)
        elif child.tag == _CUSTOM_XML:
            yield from children(child, tags)


def logical_container(el: _E) -> _E:
    """The w:body or w:tc that holds a block, skipping sdt/customXml wrappers."""
    parent = el.getparent()
    while parent is not None and parent.tag not in (_BODY, _TC):
        parent = parent.getparent()
    if parent is None:
        raise ValueError("block is not inside w:body or w:tc")
    return parent


class BlockIndex:
    """Maps block IDs to live elements of one document body."""

    def __init__(self, body: _E) -> None:
        if body.tag != _BODY:
            raise ValueError("BlockIndex needs a w:body element")
        self.body = body
        self._el: dict[str, _E] = {}
        self._id: dict[_E, str] = {}
        self._deleted: set[str] = set()
        self._new = 0
        self._assign(body, "")

    @classmethod
    def from_package(cls, pkg: DocxPackage) -> BlockIndex:
        body = pkg.document().getroot().find(_BODY)
        if body is None:
            raise PackageError("main document has no w:body")
        return cls(body)

    def _add(self, bid: str, el: _E) -> None:
        self._el[bid] = el
        self._id[el] = bid

    def _assign(self, container: _E, prefix: str) -> None:
        n_p = n_t = 0
        for el in children(container, (_P, _TBL)):
            if el.tag == _P:
                self._add(f"{prefix}p{n_p}", el)
                n_p += 1
                continue
            tid = f"{prefix}t{n_t}"
            n_t += 1
            self._add(tid, el)
            for r, row in enumerate(children(el, (_TR,))):
                for c, cell in enumerate(children(row, (_TC,))):
                    self._assign(cell, f"{tid}.r{r}.c{c}.")

    # ------------------------------------------------------------------ lookup
    def get(self, bid: str) -> _E:
        if bid in self._deleted:
            raise EditError("deleted_block", f"Block {bid} was deleted earlier in this session.")
        el = self._el.get(bid)
        if el is None:
            raise EditError(
                "unknown_block",
                f"There is no block {bid!r}. Use IDs exactly as shown by view_document "
                "(e.g. p3, t0, t0.r1.c2.p0).",
            )
        return el

    def paragraph(self, bid: str) -> _E:
        el = self.get(bid)
        if el.tag != _P:
            raise EditError(
                "not_a_paragraph",
                f"{bid} is a table; address a cell paragraph such as {bid}.r0.c0.p0.",
            )
        return el

    def kind(self, bid: str) -> BlockKind:
        return "paragraph" if self.get(bid).tag == _P else "table"

    def id_of(self, el: _E) -> str | None:
        bid = self._id.get(el)
        return None if bid is None or bid in self._deleted else bid

    def is_deleted(self, bid: str) -> bool:
        return bid in self._deleted

    def __contains__(self, bid: object) -> bool:
        return isinstance(bid, str) and bid in self._el and bid not in self._deleted

    # ------------------------------------------------------------------ iteration
    def ids(self) -> list[str]:
        """Live block IDs (paragraphs, tables, cell paragraphs) in current document order."""
        return list(self._walk(self.body))

    def _walk(self, container: _E) -> Iterator[str]:
        for el in children(container, (_P, _TBL)):
            bid = self.id_of(el)
            if bid is not None:
                yield bid
            if el.tag == _TBL:
                for row in children(el, (_TR,)):
                    for cell in children(row, (_TC,)):
                        yield from self._walk(cell)

    def paragraph_ids(self, scope: str | None = None) -> list[str]:
        """Live paragraph IDs in document order; with `scope`, only that paragraph or the
        paragraphs inside that table."""
        if scope is None:
            return [b for b in self.ids() if self._el[b].tag == _P]
        el = self.get(scope)
        if el.tag == _P:
            return [scope]
        inside = [self.id_of(p) for p in el.iter(_P)]
        return [b for b in inside if b is not None]

    # ------------------------------------------------------------------ mutation
    def register(self, el: _E) -> str:
        """Give a newly inserted block a fresh, never-reused ID (n0, n1, ...)."""
        bid = f"n{self._new}"
        self._new += 1
        self._add(bid, el)
        return bid

    def remove(self, bid: str) -> None:
        """Remove a block from the tree and tombstone its ID and every ID nested inside it."""
        el = self.get(bid)
        for inner in el.iter(_P, _TBL):
            inner_id = self._id.get(inner)
            if inner_id is not None:
                self._deleted.add(inner_id)
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
