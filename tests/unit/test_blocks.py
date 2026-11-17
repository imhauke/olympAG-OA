"""Block index: ID scheme, transparency of wrappers, tombstones, registration."""

from __future__ import annotations

import pytest
from lxml import etree

from docx_agent.document.blocks import BlockIndex, logical_container
from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage, PackageError
from docx_agent.document.runs import paragraph_text
from tests.fixtures.builders import W_NS, build_paragraphs_docx, xml_body
from tests.unit.test_package import _rezip


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _tbl(*rows: list[str]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f"<w:tc>{c}</w:tc>" for c in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl><w:tblPr/>{cells}</w:tbl>"


def test_rich_document_ids(rich_docx: bytes) -> None:
    index = BlockIndex.from_package(DocxPackage.from_bytes(rich_docx))
    ids = index.ids()
    assert ids[:7] == ["p0", "p1", "p2", "p3", "p4", "p5", "t0"]
    assert ids[7:10] == ["t0.r0.c0.p0", "t0.r0.c0.p1", "t0.r0.c1.p0"]
    assert ids[-3:] == ["p11", "p12", "p13"]
    assert paragraph_text(index.paragraph("t0.r1.c2.p0")) == "In progress"
    assert paragraph_text(index.paragraph("p13")).startswith("Signed")
    assert index.kind("t0") == "table"
    assert index.kind("p0") == "paragraph"


def test_nested_tables_and_wrappers_are_walked() -> None:
    inner = _tbl([_p("deep")])
    body = xml_body(
        _p("a")
        + f"<w:sdt><w:sdtPr/><w:sdtContent>{_p('in control')}</w:sdtContent></w:sdt>"
        + f'<w:customXml w:element="x">{_p("in custom")}</w:customXml>'
        + _tbl([_p("c00") + inner + _p("after inner")])
        + "<w:sectPr/>"
    )
    index = BlockIndex(body)
    assert index.ids() == [
        "p0",
        "p1",
        "p2",
        "t0",
        "t0.r0.c0.p0",
        "t0.r0.c0.t0",
        "t0.r0.c0.t0.r0.c0.p0",
        "t0.r0.c0.p1",
    ]
    assert paragraph_text(index.paragraph("p1")) == "in control"
    assert paragraph_text(index.paragraph("t0.r0.c0.t0.r0.c0.p0")) == "deep"
    assert index.paragraph_ids("t0") == [
        "t0.r0.c0.p0",
        "t0.r0.c0.t0.r0.c0.p0",
        "t0.r0.c0.p1",
    ]
    assert index.paragraph_ids("p2") == ["p2"]
    assert logical_container(index.get("p1")) is body
    assert logical_container(index.get("t0.r0.c0.p0")).tag == qn("w:tc")


def test_lookup_errors() -> None:
    index = BlockIndex(xml_body(_p("a") + _tbl([_p("b")])))
    with pytest.raises(EditError) as exc:
        index.get("p9")
    assert exc.value.code == "unknown_block"
    with pytest.raises(EditError) as exc:
        index.paragraph("t0")
    assert exc.value.code == "not_a_paragraph"
    with pytest.raises(ValueError, match="w:body"):
        BlockIndex(etree.Element(qn("w:p")))


def test_register_and_remove_keep_ids_stable() -> None:
    body = xml_body(_p("a") + _p("b") + _p("c"))
    index = BlockIndex(body)
    p1 = index.get("p1")
    new = etree.Element(qn("w:p"))
    p1.addnext(new)
    assert index.register(new) == "n0"
    index.remove("p1")
    assert index.ids() == ["p0", "n0", "p2"]
    assert index.is_deleted("p1")
    assert "p1" not in index
    assert "n0" in index
    assert index.id_of(p1) is None
    with pytest.raises(EditError) as exc:
        index.get("p1")
    assert exc.value.code == "deleted_block"
    other = etree.Element(qn("w:p"))
    index.get("p2").addnext(other)
    assert index.register(other) == "n1"  # never reused


def test_package_without_body_is_rejected() -> None:
    blob = build_paragraphs_docx([[("x", {})]])
    broken = _rezip(blob, {"word/document.xml": f'<w:document xmlns:w="{W_NS}"/>'.encode()})
    with pytest.raises(PackageError, match="w:body"):
        BlockIndex.from_package(DocxPackage.from_bytes(broken))


def test_related_part_resolves_styles(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    assert pkg.related_part("word/document.xml", "/styles") == "word/styles.xml"
    assert pkg.related_part("word/document.xml", "/nonexistent") is None
    assert pkg.related_part("word/styles.xml", "/styles") is None  # no .rels for it
    assert "word/_rels/document.xml.rels" not in pkg.opened_parts


def test_logical_container_requires_body_or_cell() -> None:
    with pytest.raises(ValueError, match="w:body or w:tc"):
        logical_container(etree.Element(qn("w:p")))


def test_removing_a_table_tombstones_its_cell_paragraphs() -> None:
    index = BlockIndex(xml_body(_tbl([_p("a")]) + _p("b")))
    index.remove("t0")
    with pytest.raises(EditError) as exc:
        index.get("t0.r0.c0.p0")
    assert exc.value.code == "deleted_block"
    assert index.ids() == ["p0"]
