"""Document-level operations: matching, each op, each refusal path, and the integrity of
everything an op did not touch."""

from __future__ import annotations

import io

import docx
import pytest
from lxml import etree

from docx_agent.document import ops
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import char_formats, paragraph_text
from docx_agent.document.styles import StyleCatalog
from tests.fixtures.builders import build_xml_docx, xml_body

NBSP, SHY, RSQUO, NDASH = chr(0xA0), chr(0xAD), chr(0x2019), chr(0x2013)
LAQUO, RAQUO = chr(0xAB), chr(0xBB)


def _c14n(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n")


class Doc:
    """A loaded rich document plus a pristine copy to compare against."""

    def __init__(self, blob: bytes) -> None:
        self.pkg = DocxPackage.from_bytes(blob)
        self.index = BlockIndex.from_package(self.pkg)
        self.styles = StyleCatalog.from_package(self.pkg)
        self.original = BlockIndex.from_package(DocxPackage.from_bytes(blob))

    def text(self, bid: str) -> str:
        return paragraph_text(self.index.paragraph(bid))

    def assert_only_touched_changed(self, touched: set[str]) -> None:
        for bid in self.original.ids():
            container_of_touched = any(t.startswith(f"{bid}.") for t in touched)
            if bid in touched or container_of_touched or self.index.is_deleted(bid):
                continue
            assert _c14n(self.index.get(bid)) == _c14n(self.original.get(bid)), bid


@pytest.fixture
def doc(rich_docx: bytes) -> Doc:
    return Doc(rich_docx)


# ---------------------------------------------------------------------------- matching


def test_find_text_exact_and_scoped(doc: Doc) -> None:
    matches = ops.find_text(doc.index, "weekly")
    assert [(m.block_id, m.start, m.end) for m in matches] == [("p1", 39, 45), ("p4", 9, 15)]
    assert matches[0].context.endswith(LAQUO + "weekly" + RAQUO + " session on 12 March 2026.")
    assert [m.block_id for m in ops.find_text(doc.index, "In progress", "t0")] == ["t0.r1.c2.p0"]
    assert ops.find_text(doc.index, "nowhere") == []


def test_find_text_forgiving_fallback(doc: Doc) -> None:
    # double space, NBSP and a curly quote in the query; tab in the document
    m = ops.find_text(doc.index, "Name:  Max" + NBSP + "Mustermann")
    assert [(x.block_id, x.start, x.end) for x in m] == [("p9", 0, 20)]
    body = xml_body(f"<w:p><w:r><w:t>it{RSQUO}s a {NDASH} test</w:t></w:r></w:p>")
    idx = BlockIndex(body)
    (hit,) = ops.find_text(idx, "it's a - test")
    assert (hit.start, hit.end) == (0, 13)
    soft = BlockIndex(xml_body(f"<w:p><w:r><w:t>infor{SHY}mation</w:t></w:r></w:p>"))
    assert [(x.start, x.end) for x in ops.find_text(soft, "information")] == [(0, 12)]
    assert ops.find_text(soft, "   ") == []


def test_exact_match_wins_over_fallback() -> None:
    idx = BlockIndex(
        xml_body("<w:p><w:r><w:t>a  b</w:t></w:r></w:p><w:p><w:r><w:t>a b</w:t></w:r></w:p>")
    )
    assert [m.block_id for m in ops.find_text(idx, "a b")] == ["p1"]


def test_empty_query_is_invalid(doc: Doc) -> None:
    with pytest.raises(EditError) as exc:
        ops.find_text(doc.index, "")
    assert exc.value.code == "invalid_args"


def test_ambiguous_lists_candidates_and_occurrence_resolves(doc: Doc) -> None:
    with pytest.raises(EditError) as exc:
        ops.replace_text(doc.index, "weekly", "monthly")
    err = exc.value.to_dict()
    assert err["error"] == "ambiguous"
    assert err["total"] == 2
    assert [c["block_id"] for c in err["candidates"]] == ["p1", "p4"]
    result = ops.replace_text(doc.index, "weekly", "monthly", occurrence=2)
    assert result.block_id == "p4"
    assert doc.text("p4") == "Continue monthly sessions."


def test_not_found_and_bad_occurrence(doc: Doc) -> None:
    with pytest.raises(EditError) as exc:
        ops.replace_text(doc.index, "sleep. Practised", "x")  # spans two paragraphs
    assert exc.value.code == "not_found"
    with pytest.raises(EditError) as exc:
        ops.replace_text(doc.index, "weekly", "x", block_id="p1", occurrence=2)
    assert exc.value.code == "invalid_occurrence"


# ---------------------------------------------------------------------------- text ops


def test_replace_text_keeps_formatting_and_everything_else(doc: Doc) -> None:
    before = char_formats(doc.index.paragraph("p1"))
    result = ops.replace_text(doc.index, "12 March 2026", "19 March 2026", block_id="p1")
    assert result.touched == ("p1",)
    assert result.after.endswith("session on 19 March 2026.")
    after = char_formats(doc.index.paragraph("p1"))
    assert [f for _, f in after] == [f for _, f in before]  # same per-char formatting
    doc.assert_only_touched_changed({"p1"})


def test_replace_trims_quoted_context(doc: Doc) -> None:
    """Quoting unchanged neighbours must not spread the first run's format over them."""
    p = doc.index.paragraph("p1")
    before = char_formats(p)
    ops.replace_text(doc.index, "Max Mustermann attended", "Erika Musterfrau attended")
    after = char_formats(p)
    assert doc.text("p1").startswith("The client Erika Musterfrau attended the weekly")
    bold = before[11][1]
    assert [f for _, f in after[11:27]] == [bold] * 16  # new name is bold like the old
    assert after[27:] == before[25:]  # " attended the ..." untouched, still not bold


def test_minimal_change() -> None:
    assert ops.minimal_change("abcdef", 1, 5, "bXYe") == (2, 4, "XY")
    assert ops.minimal_change("Page 1", 0, 6, "Page 1 of 3") == (6, 6, " of 3")
    assert ops.minimal_change("aaa", 0, 3, "aa") == (2, 3, "")
    assert ops.minimal_change("same", 0, 4, "same") == (4, 4, "")


def test_replace_quoting_a_field_but_changing_only_text_succeeds(doc: Doc) -> None:
    result = ops.replace_text(doc.index, "Page 1", "Page 1 of 3", block_id="p8")
    assert result.after == "Page 1 of 3"
    with pytest.raises(EditError) as exc:
        ops.replace_text(doc.index, "Page 1", "Page 2", block_id="p8")
    assert exc.value.code == "protected"


def test_noop_replace_changes_nothing(doc: Doc) -> None:
    result = ops.replace_text(doc.index, "Goal", "Goal")
    assert result.before == result.after
    doc.assert_only_touched_changed(set())


def test_delete_text(doc: Doc) -> None:
    result = ops.delete_text(doc.index, " (fictitious)")
    assert result.block_id == "p13"
    assert result.after == "Signed: Dr. Erika Beispiel"
    doc.assert_only_touched_changed({"p13"})


def test_delete_whole_hyperlink_text_reports_removed_link(doc: Doc) -> None:
    result = ops.delete_text(doc.index, "example.org")
    assert result.removed == ("hyperlink",)
    assert doc.index.paragraph("p7").find(qn("w:hyperlink")) is None


def test_insert_text_anchor_and_positions(doc: Doc) -> None:
    ops.insert_text(doc.index, "Dr. ", anchor="Erika", side="before")
    assert doc.text("p13") == "Signed: Dr. Dr. Erika Beispiel (fictitious)"
    ops.insert_text(doc.index, "Note: ", block_id="p2", position="start")
    ops.insert_text(doc.index, " Good.", block_id="p2", position="end")
    assert doc.text("p2") == "Note: Reported improved sleep. Good."
    ops.insert_text(doc.index, "!", anchor="Goal")
    assert doc.text("t0.r0.c0.p0") == "Goal!"
    doc.assert_only_touched_changed({"p13", "p2", "t0.r0.c0.p0"})


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"text": ""}, "invalid_args"),
        ({"text": "x"}, "invalid_args"),  # neither anchor nor position
        ({"text": "x", "anchor": "Goal", "position": "end"}, "invalid_args"),
        ({"text": "x", "position": "end"}, "invalid_args"),  # position without block
        ({"text": "x", "block_id": "p0", "position": "middle"}, "invalid_args"),
        ({"text": "x", "anchor": "Goal", "side": "inside"}, "invalid_args"),
        ({"text": "x", "block_id": "t0", "position": "end"}, "not_a_paragraph"),
        ({"text": "x", "block_id": "p99", "anchor": "Goal"}, "unknown_block"),
    ],
)
def test_insert_text_argument_errors(doc: Doc, kwargs: dict[str, object], code: str) -> None:
    with pytest.raises(EditError) as exc:
        ops.insert_text(doc.index, **kwargs)  # type: ignore[arg-type]
    assert exc.value.code == code


# ---------------------------------------------------------------------------- insert paragraph


def test_insert_paragraph_copies_list_style(doc: Doc) -> None:
    result = ops.insert_paragraph(doc.index, "p3", "Walked daily.")
    assert result.block_id == "n0"
    new = doc.index.get("n0")
    assert new.getprevious() is doc.index.get("p3")
    style = new.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
    assert style is not None
    assert style.get(qn("w:val")) == "ListBullet"
    doc.assert_only_touched_changed({"n0"})
    assert doc.index.ids()[4] == "n0"


def test_insert_paragraph_never_copies_section_break(doc: Doc) -> None:
    ops.insert_paragraph(doc.index, "p12", "After the break", side="before")
    new = doc.index.get("n0")
    assert new.find(f".//{qn('w:sectPr')}") is None
    assert new.getnext() is doc.index.get("p12")


def test_insert_paragraph_copies_first_run_format_and_strips_revisions() -> None:
    body = xml_body(
        '<w:p><w:pPr><w:jc w:val="center"/><w:pPrChange w:id="1"/>'
        '<w:rPr><w:b/><w:ins w:id="2"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:i/><w:rPrChange w:id="3"/></w:rPr><w:t>x</w:t></w:r></w:p>'
    )
    idx = BlockIndex(body)
    ops.insert_paragraph(idx, "p0", "new\tline")
    new = idx.get("n0")
    xml = etree.tostring(new).decode()
    assert "jc" in xml
    assert "<w:i/>" in xml
    assert "Change" not in xml
    assert "w:ins" not in xml
    assert paragraph_text(new) == "new\tline"


def test_insert_paragraph_with_style(doc: Doc) -> None:
    ops.insert_paragraph(doc.index, "p0", "Plain text", style="Normal", styles=doc.styles)
    assert doc.index.get("n0").find(qn("w:pPr")) is None  # default style -> no pPr
    ops.insert_paragraph(doc.index, "p0", "Sub", style="heading 2", styles=doc.styles)
    style = doc.index.get("n1").find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
    assert style is not None
    assert style.get(qn("w:val")) == "Heading2"
    with pytest.raises(EditError) as exc:
        ops.insert_paragraph(doc.index, "p0", "x", style="Fancy", styles=doc.styles)
    assert exc.value.code == "unknown_style"
    assert "Normal" in exc.value.details["available"]


def test_insert_empty_paragraph_after_table(doc: Doc) -> None:
    ops.insert_paragraph(doc.index, "t0", "")
    new = doc.index.get("n0")
    assert len(new) == 0
    assert new.getprevious() is doc.index.get("t0")


def test_insert_paragraph_bad_side(doc: Doc) -> None:
    with pytest.raises(EditError) as exc:
        ops.insert_paragraph(doc.index, "p0", "x", side="below")  # type: ignore[arg-type]
    assert exc.value.code == "invalid_args"


# ---------------------------------------------------------------------------- delete paragraph


def test_delete_paragraph(doc: Doc) -> None:
    result = ops.delete_paragraph(doc.index, "p3")
    assert result.before == "Practised breathing exercises."
    assert "p3" not in doc.index.ids()
    doc.assert_only_touched_changed({"p3"})


@pytest.mark.parametrize(
    ("bid", "needle"),
    [
        ("p12", "section break"),
        ("p6", "image"),
        ("p8", "field"),
        ("t0.r0.c1.p0", "table cell must end with a paragraph"),
    ],
)
def test_delete_paragraph_refusals(doc: Doc, bid: str, needle: str) -> None:
    with pytest.raises(EditError, match=needle) as exc:
        ops.delete_paragraph(doc.index, bid)
    assert exc.value.code == "protected"
    doc.assert_only_touched_changed(set())


def test_delete_paragraph_with_media_when_allowed(doc: Doc) -> None:
    result = ops.delete_paragraph(doc.index, "p6", allow_media=True)
    assert result.removed == ("image",)


def test_delete_paragraph_with_hidden_revision_or_equation() -> None:
    body = xml_body(
        '<w:p><w:del w:id="1" w:author="x"><w:r><w:delText>x</w:delText></w:r></w:del></w:p>'
        "<w:p><m:oMath/></w:p><w:p/>"
    )
    idx = BlockIndex(body)
    for bid, reason in (("p0", "tracked change"), ("p1", "equation")):
        with pytest.raises(EditError, match=reason):
            ops.delete_paragraph(idx, bid)


def test_cannot_delete_last_paragraph_of_document_or_before_trailing_table() -> None:
    idx = BlockIndex(xml_body("<w:p/><w:sectPr/>"))
    with pytest.raises(EditError, match="document must end"):
        ops.delete_paragraph(idx, "p0")
    idx = BlockIndex(xml_body("<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl><w:p/>"))
    with pytest.raises(EditError, match="document must end"):
        ops.delete_paragraph(idx, "p0")


def test_cannot_empty_a_content_control() -> None:
    idx = BlockIndex(xml_body("<w:sdt><w:sdtPr/><w:sdtContent><w:p/></w:sdtContent></w:sdt><w:p/>"))
    with pytest.raises(EditError, match="content control"):
        ops.delete_paragraph(idx, "p0")


def test_delete_paragraph_moves_bookmarks_forward(doc: Doc) -> None:
    result = ops.delete_paragraph(doc.index, "p10")
    assert result.touched == ("p10", "p11")
    p11 = doc.index.get("p11")
    assert [etree.QName(c).localname for c in p11][:2] == ["bookmarkStart", "bookmarkEnd"]
    assert doc.text("p11") == "End of first section."
    assert char_formats(p11) == char_formats(doc.original.get("p11"))


def test_delete_paragraph_moves_bookmarks_backward_or_refuses() -> None:
    mark = '<w:bookmarkStart w:id="0" w:name="b"/><w:bookmarkEnd w:id="0"/>'
    idx = BlockIndex(
        xml_body(f"<w:p/><w:p>{mark}</w:p><w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl><w:p/>")
    )
    assert ops.delete_paragraph(idx, "p1").touched == ("p1", "p0")
    assert idx.get("p0")[0].tag == qn("w:bookmarkStart")
    tbl = "<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>"
    idx = BlockIndex(xml_body(f"<w:p>{mark}</w:p>{tbl}<w:p/>"))  # first block, then a table
    with pytest.raises(EditError, match="neighbouring paragraph"):
        ops.delete_paragraph(idx, "p0")


def test_deleted_block_cannot_be_edited_again(doc: Doc) -> None:
    ops.delete_paragraph(doc.index, "p2")
    with pytest.raises(EditError) as exc:
        ops.replace_text(doc.index, "Reported", "x", block_id="p2")
    assert exc.value.code == "deleted_block"


# ---------------------------------------------------------------------------- whole package


def test_edit_session_saves_valid_docx_changing_only_document_xml(rich_docx: bytes) -> None:
    doc = Doc(rich_docx)
    ops.replace_text(doc.index, "improved sleep", "much better sleep")
    ops.insert_paragraph(doc.index, "p5", "Discuss progress at next session.")
    ops.delete_paragraph(doc.index, "p3")
    ops.replace_text(doc.index, "8h", "7.5h")
    ops.insert_text(doc.index, " of 2", block_id="p8", position="end")
    out = doc.pkg.to_bytes()

    reloaded = DocxPackage.from_bytes(out)
    changed = [
        n for n in doc.pkg.part_names() if reloaded.original_part(n) != doc.pkg.original_part(n)
    ]
    assert changed == ["word/document.xml"]

    d = docx.Document(io.BytesIO(out))
    texts = [p.text for p in d.paragraphs]
    assert "Reported much better sleep." in texts
    assert "Practised breathing exercises." not in texts
    assert texts[texts.index("Review goals in four weeks.") + 1] == (
        "Discuss progress at next session."
    )
    assert d.tables[0].cell(1, 1).text == "7.5h"
    assert len(d.inline_shapes) == 1
    assert b"ns0:" not in reloaded.original_part("word/document.xml")


def test_raw_xml_docx_builder_roundtrips() -> None:
    blob = build_xml_docx("<w:p><w:r><w:t>raw</w:t></w:r></w:p>")
    d = docx.Document(io.BytesIO(blob))
    assert [p.text for p in d.paragraphs] == ["raw"]


def test_edit_result_serializes(doc: Doc) -> None:
    result = ops.delete_text(doc.index, "Goal")
    assert result.to_dict() == {
        "op": "delete_text",
        "block_id": "t0.r0.c0.p0",
        "before": "Goal",
        "after": "",
        "touched": ("t0.r0.c0.p0",),
        "removed": (),
    }


# ---------------------------------------------------------------------------- review regressions


def test_crlf_in_new_text_is_normalized_before_trimming() -> None:
    idx = BlockIndex(xml_body("<w:p><w:r><w:t>a</w:t><w:br/><w:t>b</w:t></w:r></w:p>"))
    result = ops.replace_text(idx, "a\nb", "a\r\nb")
    assert result.after == "a\nb"


def test_ambiguous_candidates_use_block_relative_occurrences() -> None:
    idx = BlockIndex(
        xml_body("<w:p><w:r><w:t>Max</w:t></w:r></w:p><w:p><w:r><w:t>Max and Max</w:t></w:r></w:p>")
    )
    with pytest.raises(EditError) as exc:
        ops.replace_text(idx, "Max", "Eva")
    cands = exc.value.details["candidates"]
    assert [(c["block_id"], c["occurrence"]) for c in cands] == [("p0", 1), ("p1", 1), ("p1", 2)]
    ops.replace_text(idx, "Max", "Eva", block_id="p1", occurrence=2)  # retry exactly as listed
    assert paragraph_text(idx.paragraph("p1")) == "Max and Eva"


def test_replace_extending_link_text_stays_in_link(doc: Doc) -> None:
    ops.replace_text(doc.index, "example.org", "example.org/synthetic")
    link = doc.index.paragraph("p7").find(qn("w:hyperlink"))
    assert link is not None
    assert "".join(t.text or "" for t in link.iter(qn("w:t"))) == "example.org/synthetic"


def test_insert_after_trailing_hyperlink_goes_outside(doc: Doc) -> None:
    ops.insert_text(doc.index, " (synthetic)", block_id="p7", position="end")
    link = doc.index.paragraph("p7").find(qn("w:hyperlink"))
    assert link is not None
    assert "".join(t.text or "" for t in link.iter(qn("w:t"))) == "example.org"
    assert doc.text("p7") == "More information: example.org (synthetic)"


def test_cannot_delete_only_paragraph_between_two_tables() -> None:
    tbl = "<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>"
    idx = BlockIndex(xml_body(f"{tbl}<w:p/>{tbl}<w:p/>"))
    with pytest.raises(EditError, match="tables"):
        ops.delete_paragraph(idx, "p0")


def test_delete_paragraph_closing_a_multi_paragraph_field_is_refused() -> None:
    idx = BlockIndex(
        xml_body(
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>x</w:t></w:r></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p><w:p/>'
        )
    )
    for bid in ("p0", "p1"):
        with pytest.raises(EditError, match="field"):
            ops.delete_paragraph(idx, bid)


def test_delete_paragraph_reports_removed_hyperlink(doc: Doc) -> None:
    result = ops.delete_paragraph(doc.index, "p7")
    assert result.removed == ("hyperlink",)


def test_delete_paragraph_with_tracked_paragraph_mark_needs_allow_media() -> None:
    body = xml_body(
        '<w:p><w:pPr><w:rPr><w:ins w:id="1" w:author="x"/></w:rPr></w:pPr>'
        "<w:r><w:t>typed with track changes</w:t></w:r></w:p><w:p/>"
    )
    idx = BlockIndex(body)
    with pytest.raises(EditError, match="tracked change"):
        ops.delete_paragraph(idx, "p0")
    assert ops.delete_paragraph(idx, "p0", allow_media=True).removed == ("tracked change",)
