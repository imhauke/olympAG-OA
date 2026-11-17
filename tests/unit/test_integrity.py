"""Integrity checker: clean edit sessions pass; every injected corruption is detected."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

import pytest
from lxml import etree

from docx_agent.document import ops
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.integrity import check
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage, PackageError
from tests.fixtures.builders import build_xml_docx
from tests.unit.test_package import _rezip

Mutation = Callable[[BlockIndex], None]


def mutate(blob: bytes, fn: Mutation) -> bytes:
    """Apply a raw (non-ops) change to the main document, like a buggy editor would."""
    pkg = DocxPackage.from_bytes(blob)
    fn(BlockIndex.from_package(pkg))
    return pkg.to_bytes()


def _remove(el: etree._Element | None) -> None:
    assert el is not None
    parent = el.getparent()
    assert parent is not None
    parent.remove(el)


def _find(index: BlockIndex, bid: str, tag: str) -> etree._Element:
    el = index.get(bid).find(f".//{qn(tag)}")
    assert el is not None
    return el


def test_identical_documents_pass(rich_docx: bytes) -> None:
    report = check(rich_docx, rich_docx, touched=set())
    assert report.ok
    assert report.changes == []
    assert report.changed_parts == []


def test_opened_and_resaved_document_passes(rich_docx: bytes) -> None:
    out = mutate(rich_docx, lambda index: None)
    assert check(rich_docx, out, touched=set()).ok


def test_clean_edit_session_passes_and_reports_changes(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    index = BlockIndex.from_package(pkg)
    results = [
        ops.replace_text(index, "improved sleep", "much better sleep"),
        ops.insert_paragraph(index, "p5", "Discuss progress."),
        ops.delete_paragraph(index, "p10"),  # moves its bookmark into p11
        ops.replace_text(index, "8h", "7.5h"),
        ops.insert_text(index, " of 2", block_id="p8", position="end"),
        ops.delete_text(index, "example.org"),  # removes the hyperlink on purpose
    ]
    touched = {bid for r in results for bid in r.touched}
    removed = {kind for r in results for kind in r.removed}
    report = check(rich_docx, pkg.to_bytes(), touched=touched, allow_removed=removed)
    assert report.ok, report.violations
    assert report.changed_parts == ["word/document.xml"]
    kinds = {(c.kind, c.before_id) for c in report.changes}
    assert kinds == {
        ("modified", "p2"),
        ("inserted", None),
        ("deleted", "p10"),
        ("modified", "t0.r1.c1.p0"),
        ("modified", "p8"),
        ("modified", "p7"),
        ("modified", "p11"),
    }
    inserted = next(c for c in report.changes if c.kind == "inserted")
    assert (inserted.after_id, inserted.after_text) == ("p6", "Discuss progress.")
    # without declaring the hyperlink removal, the same output is flagged
    strict = check(rich_docx, pkg.to_bytes(), touched=touched)
    assert strict.violations == ["Protected content removed or altered: hyperlink."]


def test_changes_outside_touched_blocks_are_violations(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    index = BlockIndex.from_package(pkg)
    ops.replace_text(index, "Goal", "Aim")
    ops.insert_paragraph(index, "p0", "x")
    report = check(rich_docx, pkg.to_bytes(), touched={"p13"})
    assert "t0.r0.c0.p0 was modified but no edit touched it." in report.violations
    assert "1 paragraph(s) appeared but only 0 were inserted by edits." in report.violations


# ---------------------------------------------------------------------------- corruptions


def _drop_bold_p1(index: BlockIndex) -> None:
    _remove(_find(index, "p1", "w:b"))


def _drop_image(index: BlockIndex) -> None:
    _remove(_find(index, "p6", "w:drawing"))


def _change_style(index: BlockIndex) -> None:
    _find(index, "p2", "w:pStyle").set(qn("w:val"), "ListNumber")


def _unmerge_cell(index: BlockIndex) -> None:
    _remove(_find(index, "t0", "w:gridSpan"))


def _drop_bookmark(index: BlockIndex) -> None:
    _remove(_find(index, "p10", "w:bookmarkStart"))


def _change_field_code(index: BlockIndex) -> None:
    _find(index, "p8", "w:instrText").text = " NUMPAGES "


def _change_page_margins(index: BlockIndex) -> None:
    margin = index.body[-1].find(qn("w:pgMar"))  # final section (body-level sectPr)
    assert margin is not None
    margin.set(qn("w:top"), "720")


def _move_sectpr_out(index: BlockIndex) -> None:
    _remove(_find(index, "p12", "w:sectPr"))


def _strip_preserve(index: BlockIndex) -> None:
    t = _find(index, "p1", "w:t")  # "The client " (trailing space)
    t.text = "The client  "  # also a text change, so the paragraph counts as edited
    del t.attrib[qn("xml:space")]


def _bold_outside_edit(index: BlockIndex) -> None:
    ops.replace_text(index, "weekly", "monthly", block_id="p1")
    first = index.get("p1").find(qn("w:r"))
    assert first is not None
    rpr = first.find(qn("w:rPr"))
    assert rpr is not None
    etree.SubElement(rpr, qn("w:b"))


def _change_untouched_text(index: BlockIndex) -> None:
    _find(index, "p3", "w:t").text = "Practised yoga."


@pytest.mark.parametrize(
    ("corrupt", "expected"),
    [
        (_drop_bold_p1, "p1: formatting changed on unedited text"),
        (_bold_outside_edit, "p1: formatting changed on unedited text at 'The client"),
        (_drop_image, "Protected content removed or altered: image."),
        (_change_style, "p2: paragraph properties"),
        (_unmerge_cell, "Document structure outside paragraphs changed"),
        (_drop_bookmark, "Protected content removed or altered: bookmark."),
        (_change_field_code, "Protected content added or altered: field."),
        (_change_page_margins, "Document structure outside paragraphs changed"),
        (_move_sectpr_out, "Protected content removed or altered: section break."),
        (_strip_preserve, "lacks xml:space=preserve"),
    ],
)
def test_corruption_is_detected(rich_docx: bytes, corrupt: Mutation, expected: str) -> None:
    report = check(rich_docx, mutate(rich_docx, corrupt))
    assert not report.ok
    assert any(expected in v for v in report.violations), report.violations


def test_silent_text_change_is_caught_with_touched(rich_docx: bytes) -> None:
    out = mutate(rich_docx, _change_untouched_text)
    assert check(rich_docx, out).ok  # a clean text edit, as such...
    report = check(rich_docx, out, touched={"p1"})  # ...but nobody asked for it
    assert report.violations == ["p3 was modified but no edit touched it."]


def test_allowed_removal_must_be_named(rich_docx: bytes) -> None:
    out = mutate(rich_docx, _drop_image)
    assert check(rich_docx, out, allow_removed=["image"]).ok
    assert not check(rich_docx, out, allow_removed=["hyperlink"]).ok


# ---------------------------------------------------------------------------- package level


def test_other_part_changed_is_a_violation(rich_docx: bytes) -> None:
    out = _rezip(rich_docx, {"word/styles.xml": b"<w:styles/>"})
    report = check(rich_docx, out)
    assert report.changed_parts == ["word/styles.xml"]
    assert report.violations == ["Part word/styles.xml changed; only word/document.xml may change."]


def test_missing_part_and_reordering_are_violations(rich_docx: bytes) -> None:
    missing = _rezip(rich_docx, {"docProps/core.xml": None})
    assert any("missing ['docProps/core.xml']" in v for v in check(rich_docx, missing).violations)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(rich_docx)) as src, zipfile.ZipFile(buf, "w") as dst:
        for info in reversed(src.infolist()):
            dst.writestr(info, src.read(info))
    assert any("order changed" in v for v in check(rich_docx, buf.getvalue()).violations)


def test_not_a_docx_raises(rich_docx: bytes) -> None:
    with pytest.raises(PackageError):
        check(rich_docx, b"not a zip")


# ---------------------------------------------------------------------------- structure & alignment

TBL = "<w:tbl><w:tblPr/><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>"


def test_new_structural_hazards_are_reported() -> None:
    blob = build_xml_docx(f"{TBL}<w:p/>{TBL}<w:p/>")

    def drop_spacer(index: BlockIndex) -> None:
        index.remove("p0")

    report = check(blob, mutate(blob, drop_spacer))
    assert any("two tables touch" in v for v in report.violations)

    def empty_cell(index: BlockIndex) -> None:
        index.remove("t0.r0.c0.p0")

    report = check(blob, mutate(blob, empty_cell))
    assert any("table cell does not end with a paragraph" in v for v in report.violations)


def test_preexisting_hazards_are_not_blamed_on_the_edit() -> None:
    blob = build_xml_docx(f"<w:p><w:r><w:t>a</w:t></w:r></w:p>{TBL}{TBL}")

    def edit(index: BlockIndex) -> None:
        ops.replace_text(index, "a", "b")

    assert check(blob, mutate(blob, edit), touched={"p0"}).ok


def test_alignment_pairs_by_similarity() -> None:
    paras = "".join(
        f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"
        for t in ("alpha one", "beta two", "gamma three", "delta four")
    )
    blob = build_xml_docx(paras)

    def edit(index: BlockIndex) -> None:
        ops.delete_paragraph(index, "p1")
        ops.replace_text(index, "gamma three", "gamma 3")

    report = check(blob, mutate(blob, edit), touched={"p1", "p2"})
    assert report.ok, report.violations
    assert [(c.kind, c.before_id, c.after_id) for c in report.changes] == [
        ("deleted", "p1", None),
        ("modified", "p2", "p1"),
    ]


def test_cell_edits_never_pair_across_cells() -> None:
    row = "<w:tr><w:tc><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc>"
    row += "<w:tc><w:p><w:r><w:t>y</w:t></w:r></w:p></w:tc></w:tr>"
    blob = build_xml_docx(f"<w:tbl><w:tblPr/>{row}</w:tbl><w:p/>")

    def edit(index: BlockIndex) -> None:
        ops.insert_paragraph(index, "t0.r0.c0.p0", "new")
        ops.replace_text(index, "y", "z")

    report = check(blob, mutate(blob, edit), touched={"n0", "t0.r0.c1.p0"})
    assert report.ok, report.violations
    assert sorted((c.kind, c.before_id or c.after_id) for c in report.changes) == [
        ("inserted", "t0.r0.c0.p1"),
        ("modified", "t0.r0.c1.p0"),
    ]
    assert report.to_dict()["ok"] is True


def test_delete_plus_unrelated_insert_is_not_paired_as_a_modification(rich_docx: bytes) -> None:
    """Found by the session property test: an empty heading-style paragraph inserted at the
    top and a deleted body paragraph must not be paired (and blamed for a style change)."""
    pkg = DocxPackage.from_bytes(rich_docx)
    index = BlockIndex.from_package(pkg)
    ops.insert_paragraph(index, "p0", "")
    ops.delete_paragraph(index, "p1")
    report = check(rich_docx, pkg.to_bytes(), touched={"n0", "p1"})
    assert report.ok, report.violations
    assert sorted(c.kind for c in report.changes) == ["deleted", "inserted"]


def test_coincidental_common_characters_are_not_compared() -> None:
    """Found by the session property test: deleting p1 and inserting "y" next to it must not
    compare the formatting of the 'y' in "weekly" with the new paragraph's 'y'."""
    blob = build_xml_docx(
        "<w:p><w:r><w:t>a</w:t></w:r></w:p>"
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">weekly session</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>z</w:t></w:r></w:p>"
    )
    pkg = DocxPackage.from_bytes(blob)
    index = BlockIndex.from_package(pkg)
    ops.insert_paragraph(index, "p0", "y")
    ops.delete_paragraph(index, "p1")
    report = check(blob, pkg.to_bytes(), touched={"n0", "p1"})
    assert report.ok, report.violations


def test_format_corruption_in_heavily_edited_paragraph_with_long_anchor() -> None:
    blob = build_xml_docx(
        '<w:p><w:r><w:t xml:space="preserve">keep this part intact, edit </w:t></w:r>'
        "<w:r><w:t>here</w:t></w:r></w:p>"
    )

    def edit(index: BlockIndex) -> None:
        ops.replace_text(index, "here", "there and more")
        rpr = etree.SubElement(index.get("p0")[0], qn("w:rPr"))
        index.get("p0")[0].insert(0, rpr)
        etree.SubElement(rpr, qn("w:i"))

    report = check(blob, mutate(blob, edit), touched={"p0"})
    assert any("formatting changed" in v for v in report.violations)


# ------------------------------------------------------------------ step-4 review regressions

NBSP = chr(0xA0)


def test_preexisting_whitespace_issues_are_not_blamed_on_the_edit() -> None:
    blob = build_xml_docx(
        f"<w:p><w:r><w:t>foo </w:t></w:r><w:r><w:t>z.{NBSP}</w:t></w:r>"
        "<w:r><w:rPr><w:b/></w:rPr><w:t>edit me</w:t></w:r></w:p>"
    )

    def edit(index: BlockIndex) -> None:
        ops.replace_text(index, "edit me", "edited")

    report = check(blob, mutate(blob, edit), touched={"p0"})
    assert report.ok, report.violations


def test_nbsp_edge_needs_no_preserve() -> None:
    blob = build_xml_docx("<w:p/><w:p/>")

    def add(index: BlockIndex) -> None:
        p = index.get("p0")
        etree.SubElement(etree.SubElement(p, qn("w:r")), qn("w:t")).text = f"z.{NBSP}"

    assert check(blob, mutate(blob, add)).ok


def test_compression_type_change_is_a_violation(rich_docx: bytes) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(rich_docx)) as src, zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "word/styles.xml":
                info.compress_type = zipfile.ZIP_STORED
            dst.writestr(info, data)
    report = check(rich_docx, buf.getvalue())
    assert report.violations == ["Part word/styles.xml: zip metadata changed (compression/date)."]


def test_allowed_removal_is_scoped_to_edited_blocks() -> None:
    link = '<w:hyperlink r:id="rId99"><w:r><w:t>{}</w:t></w:r></w:hyperlink>'
    blob = build_xml_docx(f"<w:p>{link.format('one')}</w:p><w:p>{link.format('two')}</w:p><w:p/>")

    def edit(index: BlockIndex) -> None:
        ops.delete_paragraph(index, "p0")  # intended: removes link "one"
        p1 = index.get("p1")
        hyperlink = p1[0]
        p1.replace(hyperlink, hyperlink[0])  # silent bug: unwraps link "two", text kept

    out = mutate(blob, edit)
    report = check(blob, out, touched={"p0"}, allow_removed={"hyperlink"})
    assert "Protected content removed or altered: hyperlink." in report.violations
    # the deletion alone, with the same allowance, is fine
    clean = mutate(blob, lambda index: ops.delete_paragraph(index, "p0") and None)
    assert check(blob, clean, touched={"p0"}, allow_removed={"hyperlink"}).ok


def test_large_changed_region_is_checked_quickly() -> None:
    paras = "".join(f"<w:p><w:r><w:t>line {i}</w:t></w:r></w:p>" for i in range(800))
    blob = build_xml_docx(paras)

    def touch_all(index: BlockIndex) -> None:
        for bid in index.paragraph_ids():
            index.get(bid).set(qn("w:rsidR"), "00AB12CD")  # every paragraph's XML differs

    report = check(blob, mutate(blob, touch_all))
    assert report.ok
    assert len(report.changes) == 800
    assert all(c.kind == "modified" and c.before_id == c.after_id for c in report.changes)


# ------------------------------------------------------------------ pre-submission deep check


def test_insert_starting_with_space_after_bold_text_is_not_a_false_positive() -> None:
    """Found on a macOS-written docx: inserting " (Dr.)" after a bold name and changing a
    date in the same paragraph made the char-only diff pair the bold inserted space with the
    plain original space and report a formatting change that did not happen."""
    blob = build_xml_docx(
        '<w:p><w:r><w:t xml:space="preserve">Die Klientin </w:t></w:r>'
        "<w:r><w:rPr><w:b/></w:rPr><w:t>Erika Musterfrau</w:t></w:r>"
        '<w:r><w:t xml:space="preserve"> nahm am </w:t></w:r>'
        "<w:r><w:rPr><w:i/></w:rPr><w:t>12. März 2026</w:t></w:r>"
        '<w:r><w:t xml:space="preserve"> teil.</w:t></w:r></w:p>'
    )

    def edit(index: BlockIndex) -> None:
        ops.replace_text(index, "12. März 2026", "19. März 2026")
        ops.insert_text(index, " (Dr.)", anchor="Erika Musterfrau")

    report = check(blob, mutate(blob, edit), touched={"p0"})
    assert report.ok, report.violations


def test_format_change_next_to_an_edit_is_still_detected() -> None:
    blob = build_xml_docx(
        '<w:p><w:r><w:t xml:space="preserve">plain words before </w:t></w:r>'
        "<w:r><w:rPr><w:b/></w:rPr><w:t>bold</w:t></w:r></w:p>"
    )

    def edit(index: BlockIndex) -> None:
        ops.replace_text(index, "bold", "BOLD")
        first = index.get("p0")[0]
        etree.SubElement(etree.SubElement(first, qn("w:rPr")), qn("w:i"))
        first.insert(0, first[-1])  # rPr must come first

    report = check(blob, mutate(blob, edit), touched={"p0"})
    assert any("formatting changed" in v for v in report.violations), report.violations
