from dataclasses import replace

from docx_agent.agent.session import DocumentSession
from docx_agent.document import ops
from docx_agent.document.ns import qn
from tests.fixtures.builders import build_xml_docx


def test_session_multi_edits_and_reset(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    session.apply(lambda: ops.replace_text(session.index, "8h", "7h"))
    session.apply(lambda: ops.replace_text(session.index, "7h", "6h"))
    session.apply(lambda: ops.insert_paragraph(session.index, "p0", "SYNTHETIC new note"))
    session.apply(lambda: ops.insert_text(session.index, "!", block_id="n0", position="end"))
    assert session.validate().ok
    session.reset()
    assert not session.edits
    assert not session.validate().changes


def test_op_effect_must_match_claim(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)

    def buggy_insert() -> ops.EditResult:
        result = ops.replace_text(session.index, "8h", "7h")
        return replace(result, op="insert_paragraph", before="")

    session.apply(buggy_insert)
    report = session.validate()
    assert not report.ok
    assert any("effect" in v.lower() for v in report.violations)


def test_disguised_insert_delete_pair_is_rejected() -> None:
    session = DocumentSession(
        build_xml_docx(
            "<w:p><w:r><w:t>First note.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Second note.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Last note.</w:t></w:r></w:p>"
        )
    )

    def buggy_insert() -> ops.EditResult:
        ops.delete_paragraph(session.index, "p1")
        inserted = ops.insert_paragraph(session.index, "p0", "Second revised note.")
        return replace(inserted, touched=("p1", inserted.block_id))

    session.apply(buggy_insert)
    report = session.validate()
    assert any("effect does not match insert_paragraph" in v for v in report.violations)


def test_unjournaled_mutations_cannot_pass(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    session.index.paragraph("p2").find(f".//{qn('w:t')}").text = "Unjournaled edit"
    session.apply(lambda: ops.replace_text(session.index, "8h", "7h"))
    session.index.paragraph("p3").find(f".//{qn('w:t')}").text = "Another unjournaled edit"
    report = session.validate()
    assert any("before operation 1" in v for v in report.violations)
    assert any("after the last operation" in v for v in report.violations)


def test_noop_and_cancelled_edit_are_valid(rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    session.apply(lambda: ops.replace_text(session.index, "8h", "8h"))
    session.apply(lambda: ops.replace_text(session.index, "8h", "7h"))
    session.apply(lambda: ops.replace_text(session.index, "7h", "8h"))
    assert session.validate().ok
