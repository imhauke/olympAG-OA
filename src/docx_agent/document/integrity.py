"""Integrity checker: proves that an edited .docx changed only what it should.

It compares the *original bytes* with the *output bytes* and does not trust the editor's own
bookkeeping, so the same check serves the `verify` CLI on any two files and the agent's
validate step (which additionally passes the block IDs its edits touched).

Checks
1. Package: same part names in the same order; every part except the main document is
   byte-identical.
2. Skeleton: the document with all paragraphs removed (tables, rows, cells and their
   properties, sections, content-control wrappers, background) is C14N-identical.
3. Paragraphs are aligned (diff, never across table cells). Unchanged ones must be
   C14N-identical; changed ones keep their paragraph properties, and every character whose
   text did not change keeps its exact formatting (see `runs.char_formats`).
4. Protected content (images, objects, fields, footnote/comment references, bookmarks,
   hyperlinks, section breaks, equations, tracked changes) is neither added, altered nor
   removed, unless its category is in `allow_removed`.
5. Sanity of the result: no new structural hazards (two tables touching, a body/cell not
   ending in a paragraph) and no whitespace that Word would drop (missing xml:space).
6. With `touched`: nothing outside those blocks changed, and inserted paragraphs are
   accounted for by inserted (n*) IDs.
"""

from __future__ import annotations

import copy
import difflib
import itertools
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from lxml import etree

from docx_agent.document.blocks import BlockIndex, children
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import PROTECTED, ParagraphMap, char_formats

_E = etree._Element
_P = qn("w:p")
_TBL = qn("w:tbl")
_TC = qn("w:tc")
_BODY = qn("w:body")
_PPR = qn("w:pPr")
_T = qn("w:t")

ChangeKind = Literal["modified", "inserted", "deleted"]

SAME_TEXT_RATIO = 0.8  # pair paragraphs with different pPr only if their text is this similar
SAME_PARAGRAPH_RATIO = 0.5  # below this, a changed paragraph counts as rewritten
MIN_ANCHOR = 3  # unchanged stretches shorter than this are not format-compared
MAX_DP_CELLS = 40_000  # larger changed regions are paired by position (no O(m*n) DP)
EDGE_SPACE = " \t\r\n"  # whitespace Word drops at w:t edges without xml:space (not NBSP)


@dataclass(frozen=True)
class BlockChange:
    kind: ChangeKind
    before_id: str | None
    after_id: str | None
    before_text: str
    after_text: str


@dataclass
class IntegrityReport:
    violations: list[str] = field(default_factory=list)
    changes: list[BlockChange] = field(default_factory=list)
    changed_parts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, **asdict(self)}


def _c14n(el: _E) -> bytes:
    return etree.tostring(el, method="c14n")


@dataclass(frozen=True)
class _Para:
    bid: str
    el: _E
    container: str  # "" for the body, "t0.r1.c2" for a cell
    key: bytes
    text: str
    ppr: bytes  # C14N of w:pPr (b"" if none)


def _paragraphs(index: BlockIndex) -> list[_Para]:
    out = []
    for bid in index.paragraph_ids():
        el = index.paragraph(bid)
        container = bid.rsplit(".", 1)[0] if "." in bid else ""
        ppr = el.find(_PPR)
        ppr_key = _c14n(ppr) if ppr is not None else b""
        out.append(_Para(bid, el, container, _c14n(el), ParagraphMap(el).text, ppr_key))
    return out


# ---------------------------------------------------------------------------- entry point


def check(
    before: bytes,
    after: bytes,
    *,
    touched: Iterable[str] | None = None,
    allow_removed: Iterable[str] = (),
) -> IntegrityReport:
    """Compare two .docx packages. Raises PackageError if either is not a usable .docx."""
    report = IntegrityReport()
    a_pkg, b_pkg = DocxPackage.from_bytes(before), DocxPackage.from_bytes(after)
    _check_parts(a_pkg, b_pkg, report)
    a_index, b_index = BlockIndex.from_package(a_pkg), BlockIndex.from_package(b_pkg)
    a_root, b_root = a_pkg.document().getroot(), b_pkg.document().getroot()

    if _c14n(_skeleton(a_root)) != _c14n(_skeleton(b_root)):
        report.violations.append(
            "Document structure outside paragraphs changed (tables, cells, sections or "
            "content-control wrappers)."
        )
    a_paras, b_paras = _paragraphs(a_index), _paragraphs(b_index)
    for i, j in _align(a_paras, b_paras):
        a = a_paras[i] if i is not None else None
        b = b_paras[j] if j is not None else None
        if a is not None and b is not None:
            if a.key != b.key:
                report.changes.append(BlockChange("modified", a.bid, b.bid, a.text, b.text))
                _check_modified(a, b, report)
        elif a is not None:
            report.changes.append(BlockChange("deleted", a.bid, None, a.text, ""))
        elif b is not None:
            report.changes.append(BlockChange("inserted", None, b.bid, "", b.text))
            _check_whitespace(None, b, report)

    touched_set = set(touched) if touched is not None else None
    removable = [
        a_index.get(c.before_id)
        for c in report.changes
        if c.before_id is not None and (touched_set is None or c.before_id in touched_set)
    ]
    _check_protected(a_root, b_root, removable, set(allow_removed), report)
    for issue in _hazards(b_root) - _hazards(a_root):
        report.violations.append(issue)
    if touched_set is not None:
        _check_expected(report, touched_set)
    return report


# ---------------------------------------------------------------------------- package & skeleton


def _check_parts(a: DocxPackage, b: DocxPackage, report: IntegrityReport) -> None:
    a_names, b_names = a.part_names(), b.part_names()
    if a_names != b_names:
        missing = sorted(set(a_names) - set(b_names))
        added = sorted(set(b_names) - set(a_names))
        detail = f"missing {missing}, added {added}" if missing or added else "order changed"
        report.violations.append(f"Package parts differ: {detail}.")
    main = a.main_document_name()
    for name in a_names:
        if name not in b_names:
            continue
        ai, bi = a.part_info(name), b.part_info(name)
        if (ai.compress_type, ai.date_time) != (bi.compress_type, bi.date_time):
            report.violations.append(f"Part {name}: zip metadata changed (compression/date).")
        if a.original_part(name) != b.original_part(name):
            report.changed_parts.append(name)
            if name != main:
                report.violations.append(f"Part {name} changed; only {main} may change.")


def _skeleton(root: _E) -> _E:
    """The document without paragraphs: what no text operation may ever change."""
    skel = copy.deepcopy(root)
    for p in list(skel.iter(_P)):
        parent = p.getparent()
        if parent is not None:
            parent.remove(p)
    return skel


def _fingerprint(el: _E, how: str) -> bytes:
    if how == "xml":
        return _c14n(el)
    if how == "text":
        return (el.text or "").encode()
    return repr(sorted(el.attrib.items())).encode()


def _protected(root: _E) -> Counter[tuple[str, bytes]]:
    found: Counter[tuple[str, bytes]] = Counter()
    for el in root.iter(*PROTECTED):
        category, how = PROTECTED[el.tag]
        found[
            (category, etree.QName(el.tag).localname.encode() + b":" + _fingerprint(el, how))
        ] += 1
    return found


def _check_protected(
    a: _E, b: _E, removable: list[_E], allowed: set[str], report: IntegrityReport
) -> None:
    """Protected content must survive. An allowed category may only be lost from the
    `removable` (changed and, if known, touched) paragraphs -- never from anywhere else."""
    before, after = _protected(a), _protected(b)
    pool: Counter[tuple[str, bytes]] = Counter()
    for p in removable:
        pool.update({k: n for k, n in _protected(p).items() if k[0] in allowed})
    lost = sorted({cat for (cat, _), n in ((before - after) - pool).items() if n})
    gained = sorted({cat for (cat, _), n in (after - before).items() if n})
    for category in lost:
        report.violations.append(f"Protected content removed or altered: {category}.")
    for category in gained:
        report.violations.append(f"Protected content added or altered: {category}.")


# ---------------------------------------------------------------------------- paragraphs


def _align(a: list[_Para], b: list[_Para]) -> list[tuple[int | None, int | None]]:
    """Pair paragraphs of two documents: identical ones by diff, changed ones by similarity."""
    matcher = difflib.SequenceMatcher(
        None, [(p.container, p.key) for p in a], [(p.container, p.key) for p in b], autojunk=False
    )
    pairs: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(range(i1, i2), range(j1, j2), strict=True))
        else:
            pairs.extend(_pair_changed(a, b, range(i1, i2), range(j1, j2)))
    return pairs


def _pair_changed(
    a: list[_Para], b: list[_Para], ai: range, bj: range
) -> list[tuple[int | None, int | None]]:
    """Order-preserving best pairing (max total text similarity) of a changed region."""
    m, n = len(ai), len(bj)
    if m * n > MAX_DP_CELLS:
        k = min(m, n)
        return [
            *zip(ai[:k], bj[:k], strict=True),
            *((x, None) for x in ai[k:]),
            *((None, y) for y in bj[k:]),
        ]
    memo: dict[tuple[int, int], float] = {}

    def score(i: int, j: int) -> float:
        if (i, j) not in memo:
            memo[i, j] = _score(a[ai[i]], b[bj[j]])
        return memo[i, j]

    best = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            s = score(i, j)
            pair = s + best[i + 1][j + 1] if s > 0 else float("-inf")
            best[i][j] = max(pair, best[i + 1][j], best[i][j + 1])
    out: list[tuple[int | None, int | None]] = []
    i = j = 0
    while i < m and j < n:
        s = score(i, j)
        if s > 0 and best[i][j] == s + best[i + 1][j + 1]:
            out.append((ai[i], bj[j]))
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            out.append((ai[i], None))
            i += 1
        else:
            out.append((None, bj[j]))
            j += 1
    out.extend((ai[k], None) for k in range(i, m))
    out.extend((None, bj[k]) for k in range(j, n))
    return out


def _score(pa: _Para, pb: _Para) -> float:
    """How plausibly `pb` is an edited version of `pa` (<= 0: not at all)."""
    if pa.container != pb.container:
        return -1.0
    ratio = difflib.SequenceMatcher(None, pa.text, pb.text, autojunk=False).ratio()
    if pa.ppr == pb.ppr:
        return 0.1 + ratio
    # Text edits never change paragraph properties, so different pPr means "a different
    # paragraph" -- unless the text is (nearly) the same: then the properties were altered.
    return ratio if pa.text and ratio >= SAME_TEXT_RATIO else -1.0


def _check_modified(a: _Para, b: _Para, report: IntegrityReport) -> None:
    if a.ppr != b.ppr:
        report.violations.append(f"{a.bid}: paragraph properties (style, numbering...) changed.")
    a_fmt, b_fmt = char_formats(a.el), char_formats(b.el)
    matcher = difflib.SequenceMatcher(None, a.text, b.text, autojunk=False)
    identical_text = a.text == b.text
    rewritten = not identical_text and matcher.ratio() < SAME_PARAGRAPH_RATIO
    for tag, i1, i2, j1, _ in [] if rewritten else matcher.get_opcodes():
        if tag != "equal" or (not identical_text and i2 - i1 < MIN_ANCHOR):
            continue  # short coincidental matches between rewritten texts are noise
        for k in range(i2 - i1):
            if a_fmt[i1 + k][1] != b_fmt[j1 + k][1]:
                snippet = a.text[i1 + k : i1 + k + 20]
                report.violations.append(
                    f"{a.bid}: formatting changed on unedited text at {snippet!r}."
                )
                break
    _check_whitespace(a, b, report)


def _droppable_whitespace(p: _E) -> Counter[str]:
    """Texts of w:t with edge whitespace but no xml:space=preserve (Word would drop it)."""
    found: Counter[str] = Counter()
    for t in p.iter(_T):
        text = t.text or ""
        if text != text.strip(EDGE_SPACE) and t.get(qn("xml:space")) != "preserve":
            found[text] += 1
    return found


def _check_whitespace(a: _Para | None, b: _Para, report: IntegrityReport) -> None:
    """Report only problems the edit introduced, not ones already in the input."""
    new = _droppable_whitespace(b.el) - (_droppable_whitespace(a.el) if a else Counter())
    for text in sorted(new):
        report.violations.append(
            f"{b.bid}: text {text!r} lacks xml:space=preserve; Word would drop its spaces."
        )


# ---------------------------------------------------------------------------- structure


def _hazards(root: _E) -> set[str]:
    """Structural problems that make Word repair or silently restructure a document."""
    issues: set[str] = set()
    body = root.find(_BODY)
    containers = ([body] if body is not None else []) + list(root.iter(_TC))
    for container in containers:
        blocks = list(children(container, (_P, _TBL)))
        where = "a table cell" if container.tag == _TC else "the document body"
        if not blocks or blocks[-1].tag != _P:
            issues.add(f"Structure: {where} does not end with a paragraph.")
        if any(x.tag == _TBL and y.tag == _TBL for x, y in itertools.pairwise(blocks)):
            issues.add(f"Structure: two tables touch in {where}; Word would merge them.")
    return issues


def _check_expected(report: IntegrityReport, touched: set[str]) -> None:
    for change in report.changes:
        if change.before_id is not None and change.before_id not in touched:
            report.violations.append(
                f"{change.before_id} was {change.kind} but no edit touched it."
            )
    inserted = sum(1 for c in report.changes if c.kind == "inserted")
    allowed = sum(1 for bid in touched if bid.startswith("n"))
    if inserted > allowed:
        report.violations.append(
            f"{inserted} paragraph(s) appeared but only {allowed} were inserted by edits."
        )
