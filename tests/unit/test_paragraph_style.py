import copy

import pytest
from lxml import etree

from docx_agent.agent.session import DocumentSession
from docx_agent.document import ops
from docx_agent.document.errors import EditError
from docx_agent.document.integrity import check
from docx_agent.document.ns import qn
from docx_agent.document.runs import char_formats
from tests.fixtures.builders import build_xml_docx


def test_style_changes_only_style_and_keeps_protected_content(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    original = copy.deepcopy(session.index.paragraph("p6"))
    formats = char_formats(original)
    result = session.apply(
        lambda: ops.set_paragraph_style(session.index, "p6", "Heading 2", session.styles)
    )
    assert result.style == "Heading2"
    assert char_formats(session.index.paragraph("p6")) == formats
    assert session.validate().ok
    assert not check(rich_docx, session.package.to_bytes()).ok
    changed = copy.deepcopy(session.index.paragraph("p6"))
    changed.remove(changed.find(qn("w:pPr")))
    assert etree.tostring(original, method="c14n") == etree.tostring(changed, method="c14n")


def test_style_and_rename_pair_as_one_modified_paragraph() -> None:
    blob = build_xml_docx("<w:p><w:r><w:t>1. Overview and objectives</w:t></w:r></w:p>")
    session = DocumentSession(blob)
    session.apply(lambda: ops.replace_text(session.index, "1. Overview and objectives", "Overview"))
    session.apply(lambda: ops.set_paragraph_style(session.index, "p0", "Heading1", session.styles))
    report = session.validate()
    assert report.ok, report.violations
    assert [c.kind for c in report.changes] == ["modified"]


def test_style_permission_does_not_allow_other_properties_or_wrong_style(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    session.apply(lambda: ops.set_paragraph_style(session.index, "p2", "Heading1", session.styles))
    ppr = session.index.paragraph("p2").find(qn("w:pPr"))
    etree.SubElement(ppr, qn("w:jc")).set(qn("w:val"), "center")
    assert not check(rich_docx, session.package.to_bytes(), style_changes={"p2": "Heading1"}).ok
    assert not check(rich_docx, session.package.to_bytes(), style_changes={"p2": "Heading2"}).ok
    assert not check(rich_docx, session.package.to_bytes(), style_changes={"p3": "Heading1"}).ok


def test_style_unknown_refused_and_noop_valid(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    before = session.package.to_bytes()
    with pytest.raises(EditError, match="paragraph style"):
        ops.set_paragraph_style(session.index, "p0", "not-a-style", session.styles)
    assert session.package.to_bytes() == before
    session.apply(lambda: ops.set_paragraph_style(session.index, "p0", "Heading1", session.styles))
    assert session.validate().ok


def test_style_permission_requires_requested_effect(rich_docx: bytes) -> None:
    report = check(rich_docx, rich_docx, style_changes={"p2": "Heading1"})
    assert not report.ok
