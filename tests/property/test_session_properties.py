"""End-to-end property: any sequence of edit operations on the rich fixture either is refused
(document unchanged) or yields a package that passes the integrity checker with exactly the
blocks the operations reported as touched, and still opens in python-docx."""

from __future__ import annotations

import io

import docx
from hypothesis import given, settings
from hypothesis import strategies as st

from docx_agent.document import ops
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.integrity import check
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import OBJECT_CHAR, paragraph_text
from tests.fixtures.builders import build_rich_docx

RICH = build_rich_docx()
NEW_TEXT = st.text(alphabet="Xy \t\n.", max_size=6)


@st.composite
def an_op(draw: st.DrawFn, index: BlockIndex) -> ops.EditResult:
    paragraphs = index.paragraph_ids()
    bid = draw(st.sampled_from(paragraphs))
    text = paragraph_text(index.paragraph(bid))
    start = draw(st.integers(0, max(0, len(text) - 1)))
    end = draw(st.integers(min(start + 1, len(text)), min(len(text), start + 12)))
    quote = text[start:end]
    kind = draw(st.sampled_from(["replace", "delete", "insert", "insert_p", "delete_p"]))
    if kind == "replace":
        return ops.replace_text(index, quote, draw(NEW_TEXT), block_id=bid, occurrence=1)
    if kind == "delete":
        return ops.delete_text(index, quote, block_id=bid, occurrence=1)
    if kind == "insert":
        position = draw(st.sampled_from(["start", "end"]))
        return ops.insert_text(index, draw(NEW_TEXT) or "x", block_id=bid, position=position)
    if kind == "insert_p":
        side = draw(st.sampled_from(["before", "after"]))
        return ops.insert_paragraph(index, bid, draw(NEW_TEXT), side=side)
    return ops.delete_paragraph(index, bid, allow_media=draw(st.booleans()))


@settings(max_examples=60)
@given(st.data(), st.integers(1, 5))
def test_random_edit_sessions_keep_integrity(data: st.DataObject, n_ops: int) -> None:
    pkg = DocxPackage.from_bytes(RICH)
    index = BlockIndex.from_package(pkg)
    touched: set[str] = set()
    removed: set[str] = set()
    for _ in range(n_ops):
        try:
            result = data.draw(an_op(index))
        except EditError:
            continue
        touched.update(result.touched)
        removed.update(result.removed)

    out = pkg.to_bytes()
    report = check(RICH, out, touched=touched, allow_removed=removed)
    assert report.ok, report.violations

    reopened = docx.Document(io.BytesIO(out))
    top = [b for b in index.paragraph_ids() if index.get(b).getparent() is index.body]
    body = [paragraph_text(index.paragraph(b)) for b in top]
    # python-docx's .text leaves out images; ours shows them as OBJECT_CHAR
    assert [p.text for p in reopened.paragraphs] == [t.replace(OBJECT_CHAR, "") for t in body]
