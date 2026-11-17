"""Run-level primitives: character map, span replacement, formatting inheritance, refusals."""

from __future__ import annotations

import pytest
from lxml import etree

from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.runs import (
    NO_BREAK_HYPHEN,
    OBJECT_CHAR,
    SOFT_HYPHEN,
    ParagraphMap,
    char_formats,
    check_new_text,
    paragraph_text,
    replace_span,
)
from tests.fixtures.builders import W_NS, xml_paragraph

B = "<w:rPr><w:b/></w:rPr>"
I = "<w:rPr><w:i/></w:rPr>"  # noqa: E741


def r(text: str, rpr: str = "") -> str:
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f"<w:r>{rpr}<w:t{space}>{text}</w:t></w:r>"


def runs(p: etree._Element) -> list[tuple[str, str]]:
    """(text, formatting) per run, formatting as 'b', 'i', '' for compact asserts."""
    out = []
    for run in p.iter(qn("w:r")):
        rpr = run.find(qn("w:rPr"))
        fmt = "".join(etree.QName(c).localname for c in rpr) if rpr is not None else ""
        out.append((paragraph_text(_wrap(run)), fmt))
    return out


def _wrap(run: etree._Element) -> etree._Element:
    p = etree.Element(qn("w:p"))
    p.append(etree.fromstring(etree.tostring(run)))
    return p


MIXED = r("The client ") + r("Max", B) + r(" attended the ") + r("weekly", I) + r(" session.")


# ---------------------------------------------------------------------------- char map


def test_text_atoms_cover_tabs_breaks_and_hyphens() -> None:
    p = xml_paragraph(
        "<w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t><w:cr/>"
        "<w:noBreakHyphen/><w:softHyphen/><w:lastRenderedPageBreak/></w:r>"
    )
    assert paragraph_text(p) == "a\tb\nc\n" + NO_BREAK_HYPHEN + SOFT_HYPHEN
    assert not any(a.protected for a in ParagraphMap(p).atoms)


def test_hyperlink_smarttag_and_inline_sdt_text_is_editable() -> None:
    p = xml_paragraph(
        r("A ")
        + f'<w:hyperlink r:id="rId1">{r("link")}</w:hyperlink>'
        + f'<w:smartTag w:uri="x" w:element="y">{r(" tag")}</w:smartTag>'
        + f"<w:sdt><w:sdtPr/><w:sdtContent>{r(' cc')}</w:sdtContent></w:sdt>"
    )
    pm = ParagraphMap(p)
    assert pm.text == "A link tag cc"
    assert not any(a.protected for a in pm.atoms)


def test_deleted_revision_text_is_invisible_and_insertions_are_protected() -> None:
    p = xml_paragraph(
        r("keep ")
        + '<w:del w:id="1" w:author="x"><w:r><w:delText>gone </w:delText></w:r></w:del>'
        + f'<w:ins w:id="2" w:author="x">{r("new")}</w:ins>'
    )
    pm = ParagraphMap(p)
    assert pm.text == "keep new"
    assert [a.protected for a in pm.atoms[-3:]] == ["tracked change"] * 3


def test_objects_are_protected_placeholders() -> None:
    p = xml_paragraph(
        r("x")
        + "<w:r><w:drawing/></w:r>"
        + '<w:r><w:footnoteReference w:id="1"/></w:r>'
        + "<w:r><w:unknownThing/></w:r>"
        + '<w:r><w:br w:type="page"/></w:r>'
    )
    pm = ParagraphMap(p)
    assert pm.text == "x" + OBJECT_CHAR * 3 + "\n"
    assert [a.protected for a in pm.atoms[1:]] == [
        "image",
        "footnote reference",
        "special element <unknownThing>",
        "page break",
    ]


def test_complex_field_runs_are_protected_including_cached_result() -> None:
    p = xml_paragraph(
        r("Page ")
        + '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + "<w:r><w:instrText> PAGE </w:instrText></w:r>"
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + r("12")
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        + r(" end")
    )
    pm = ParagraphMap(p)
    assert pm.text == "Page 12 end"
    assert [r.field for r in pm.runs] == [None, 0, 0, 0, 0, 0, None]
    assert [a.protected for a in pm.atoms[5:7]] == ["field", "field"]
    assert pm.atoms[7].protected is None


def test_fld_simple_is_protected() -> None:
    p = xml_paragraph(f'<w:fldSimple w:instr=" DATE ">{r("1 Jan")}</w:fldSimple>')
    assert {a.protected for a in ParagraphMap(p).atoms} == {"field"}


def test_char_formats_ignore_how_runs_are_split() -> None:
    one = xml_paragraph(r("Hello", B))
    two = xml_paragraph(r("He", B) + r("llo", B))
    empty_rpr = xml_paragraph("<w:r><w:rPr/><w:t>x</w:t></w:r>")
    no_rpr = xml_paragraph(r("x"))
    assert char_formats(one) == char_formats(two)
    assert char_formats(empty_rpr) == char_formats(no_rpr)
    assert char_formats(one) != char_formats(xml_paragraph(r("Hello", I)))


def test_char_formats_include_hyperlink_context() -> None:
    plain = xml_paragraph(r("x"))
    linked = xml_paragraph(f'<w:hyperlink r:id="rId1">{r("x")}</w:hyperlink>')
    assert char_formats(plain) != char_formats(linked)


# ---------------------------------------------------------------------------- replace


def test_replace_inside_one_run_keeps_a_single_run() -> None:
    p = xml_paragraph(r("The weekly session", I))
    replace_span(p, 4, 10, "biweekly")
    assert runs(p) == [("The biweekly session", "i")]


def test_replace_across_runs_takes_first_run_format() -> None:
    p = xml_paragraph(MIXED)
    replace_span(p, 11, 17, "Erika came")  # "Max at" -> bold "Erika came"
    assert paragraph_text(p) == "The client Erika cametended the weekly session."
    assert runs(p) == [
        ("The client ", ""),
        ("Erika came", "b"),
        ("tended the ", ""),
        ("weekly", "i"),
        (" session.", ""),
    ]


def test_replace_spanning_n_runs_removes_the_inner_runs() -> None:
    p = xml_paragraph(MIXED)
    replace_span(p, 4, 32, "person")  # "client Max attended the week" -> "person"
    assert paragraph_text(p) == "The personly session."
    assert runs(p) == [("The person", ""), ("ly", "i"), (" session.", "")]


def test_formatting_outside_span_is_unchanged() -> None:
    p = xml_paragraph(MIXED)
    before = char_formats(p)
    replace_span(p, 15, 23, "joined")
    after = char_formats(p)
    assert after[:15] == before[:15]
    assert after[15 + 6 :] == before[23:]


def test_delete_span_and_merge_neighbours() -> None:
    p = xml_paragraph(r("one two three"))
    replace_span(p, 3, 7, "")
    assert runs(p) == [("one three", "")]
    assert len(p.findall(qn("w:r"))) == 1


def test_new_text_tabs_and_newlines_become_elements() -> None:
    p = xml_paragraph(r("a-b"))
    replace_span(p, 1, 2, "\t|\r\n")
    run = p.find(qn("w:r"))
    assert run is not None
    assert [etree.QName(c).localname for c in run] == ["t", "tab", "t", "br", "t"]
    assert paragraph_text(p) == "a\t|\nb"


def test_xml_space_preserve_set_on_split_text() -> None:
    p = xml_paragraph(r("ab cd", B) + r("x"))
    replace_span(p, 5, 6, "y")  # new run "y" is plain; "ab cd" untouched
    replace_span(p, 2, 3, "")  # "abcd"
    replace_span(p, 2, 2, " ")
    texts = list(p.iter(qn("w:t")))
    assert "".join(t.text or "" for t in texts) == "ab cdy"
    for t in texts:
        if (t.text or "") != (t.text or "").strip():
            assert t.get(qn("xml:space")) == "preserve"


def test_invalid_span_and_text_are_refused() -> None:
    p = xml_paragraph(r("abc"))
    with pytest.raises(EditError) as exc:
        replace_span(p, 2, 9, "x")
    assert exc.value.code == "invalid_span"
    for bad in ("\x00", "a\x0bb", OBJECT_CHAR):
        with pytest.raises(EditError) as exc:
            replace_span(p, 0, 1, bad)
        assert exc.value.code == "invalid_text"
    assert check_new_text("a\r\nb\rc") == "a\nb\nc"
    assert paragraph_text(p) == "abc"


@pytest.mark.parametrize(
    ("inner", "start", "end", "code"),
    [
        (r("a") + "<w:r><w:drawing/></w:r>" + r("b"), 0, 3, "protected"),
        (
            r("Page ")
            + '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            + r("1")
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
            4,
            6,
            "protected",
        ),
        (
            r("see ") + f'<w:hyperlink r:id="rId1">{r("link")}</w:hyperlink>',
            2,
            6,
            "crosses_boundary",
        ),
        (r("a") + "<m:oMath/>" + r("b"), 0, 2, "crosses_boundary"),
        (f'<w:ins w:id="1" w:author="x">{r("abc")}</w:ins>', 0, 1, "protected"),
        # a zero-width field (e.g. an XE entry) between two runs
        (
            r("a")
            + '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            + "<w:r><w:instrText> XE x </w:instrText></w:r>"
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            + r("b"),
            0,
            2,
            "protected",
        ),
    ],
)
def test_refusals_leave_paragraph_unchanged(inner: str, start: int, end: int, code: str) -> None:
    p = xml_paragraph(inner)
    before = etree.tostring(p, method="c14n")
    with pytest.raises(EditError) as exc:
        replace_span(p, start, end, "X")
    assert exc.value.code == code
    assert etree.tostring(p, method="c14n") == before


def test_bookmarks_and_proofing_marks_stay_in_place() -> None:
    p = xml_paragraph(
        '<w:bookmarkStart w:id="0" w:name="b"/>'
        + r("See ")
        + '<w:proofErr w:type="spellStart"/>'
        + r("Plan")
        + '<w:proofErr w:type="spellEnd"/><w:bookmarkEnd w:id="0"/>'
    )
    replace_span(p, 2, 8, "x")  # "e Plan" spans both runs and the proofErr between them
    assert paragraph_text(p) == "Sex"
    names = [etree.QName(c).localname for c in p]
    assert names.count("proofErr") == 2
    assert names[0] == "bookmarkStart"
    assert names[-1] == "bookmarkEnd"


def test_emptied_hyperlink_is_removed_and_reported() -> None:
    p = xml_paragraph(r("see ") + f'<w:hyperlink r:id="rId1">{r("link")}</w:hyperlink>')
    assert replace_span(p, 4, 8, "") == ["hyperlink"]
    assert p.find(qn("w:hyperlink")) is None
    assert paragraph_text(p) == "see "


def test_replace_inside_hyperlink_stays_inside() -> None:
    p = xml_paragraph(r("see ") + f'<w:hyperlink r:id="rId1">{r("example.org")}</w:hyperlink>')
    replace_span(p, 12, 15, "com")
    link = p.find(qn("w:hyperlink"))
    assert link is not None
    assert paragraph_text(_wrap_children(link)) == "example.com"


def _wrap_children(container: etree._Element) -> etree._Element:
    p = etree.Element(qn("w:p"))
    for child in container:
        p.append(etree.fromstring(etree.tostring(child)))
    return p


def test_split_run_with_drawing_keeps_the_drawing_element() -> None:
    p = xml_paragraph('<w:r><w:t>ab</w:t><w:drawing><x id="1"/></w:drawing><w:t>cd</w:t></w:r>')
    replace_span(p, 0, 1, "A")
    replace_span(p, 4, 5, "D")
    assert paragraph_text(p) == f"Ab{OBJECT_CHAR}cD"
    assert len(p.findall(f".//{qn('w:drawing')}")) == 1


# ---------------------------------------------------------------------------- insert


def test_insert_inherits_preceding_format() -> None:
    p = xml_paragraph(MIXED)
    replace_span(p, 14, 14, " Jr.")  # right after bold "Max"
    assert ("Max Jr.", "b") in runs(p)


def test_insert_at_start_inherits_following_format() -> None:
    p = xml_paragraph(r("Max", B) + r(" came"))
    replace_span(p, 0, 0, "Dr. ")
    assert runs(p)[0] == ("Dr. Max", "b")


def test_insert_in_middle_of_run_keeps_one_run() -> None:
    p = xml_paragraph(r("weely", I))
    replace_span(p, 3, 3, "k")
    assert runs(p) == [("weekly", "i")]


def test_insert_at_hyperlink_boundary_prefers_plain_text() -> None:
    link = f'<w:hyperlink r:id="rId1">{r("link", I)}</w:hyperlink>'
    p = xml_paragraph(link + r(" after"))
    replace_span(p, 4, 4, "!")  # between link and plain text -> plain run
    assert paragraph_text(p) == "link! after"
    assert p.find(qn("w:hyperlink")) is not None
    assert runs(p)[-1] == ("! after", "")
    p = xml_paragraph(r("before ") + link)
    replace_span(p, 7, 7, "the ")  # before the link -> plain run
    assert runs(p)[0] == ("before the ", "")


def test_insert_after_field_lands_after_field_end() -> None:
    p = xml_paragraph(
        r("Page ", B)
        + '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + "<w:r><w:instrText> PAGE </w:instrText></w:r>"
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + r("1", B)
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    replace_span(p, 6, 6, " of 3")
    last = p[-1]
    assert paragraph_text(_wrap(last)) == " of 3"
    assert ParagraphMap(p).runs[-1].field is None
    assert runs(p)[-1] == (" of 3", "b")


def test_insert_before_field_lands_before_field_begin() -> None:
    p = xml_paragraph(
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + "<w:r><w:instrText> PAGE </w:instrText></w:r>"
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + r("1")
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    replace_span(p, 0, 0, "Page ")
    assert paragraph_text(p) == "Page 1"
    assert ParagraphMap(p).runs[0].field is None


def test_insert_between_two_protected_objects() -> None:
    p = xml_paragraph("<w:r><w:drawing/></w:r><w:r><w:drawing/></w:r>")
    replace_span(p, 1, 1, " and ")
    assert paragraph_text(p) == f"{OBJECT_CHAR} and {OBJECT_CHAR}"


def test_insert_inside_field_result_is_refused() -> None:
    p = xml_paragraph(
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + r("1")
        + r("2")
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    with pytest.raises(EditError) as exc:
        replace_span(p, 1, 1, "x")
    assert exc.value.code == "protected"


def test_insert_inside_tracked_insertion_is_refused() -> None:
    p = xml_paragraph(f'<w:ins w:id="1" w:author="x">{r("ab")}</w:ins>')
    with pytest.raises(EditError):
        replace_span(p, 1, 1, "x")


def test_insert_into_empty_paragraph_uses_paragraph_mark_format() -> None:
    p = xml_paragraph('<w:pPr><w:rPr><w:b/><w:rPrChange w:id="1"/></w:rPr></w:pPr>')
    replace_span(p, 0, 0, "Hello")
    assert runs(p) == [("Hello", "b")]


def test_insert_next_to_page_break_in_same_run() -> None:
    p = xml_paragraph('<w:r><w:t>end</w:t><w:br w:type="page"/></w:r>')
    replace_span(p, 3, 3, ".")
    assert paragraph_text(p) == "end.\n"
    assert p.find(f".//{qn('w:br')}") is not None


def test_empty_insert_is_a_noop() -> None:
    p = xml_paragraph(r("abc"))
    before = etree.tostring(p)
    assert replace_span(p, 1, 1, "") == []
    assert etree.tostring(p) == before


def test_delete_after_image_leaves_image_run_alone() -> None:
    p = xml_paragraph("<w:r><w:drawing/></w:r>" + r(" caption"))
    replace_span(p, 1, 9, "")
    assert paragraph_text(p) == OBJECT_CHAR
    assert len(p.findall(qn("w:r"))) == 1


# ---------------------------------------------------------------------------- review regressions


def test_field_spanning_paragraphs_is_protected_in_later_paragraphs() -> None:
    """A TOC-like field opens in one paragraph and closes in a later one."""
    body = xml_paragraph("").getparent()
    assert body is not None
    body.remove(body[0])
    for inner in (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        "<w:r><w:instrText> TOC </w:instrText></w:r>"
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + r("Intro 1"),
        r("Methods 2"),
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
        r("Body text"),
    ):
        body.append(etree.fromstring(f'<w:p xmlns:w="{W_NS}">{inner}</w:p>'))
    middle, closing, after = body[1], body[2], body[3]
    assert {a.protected for a in ParagraphMap(middle).atoms} == {"field"}
    assert ParagraphMap(closing).runs[0].protected == "field"
    assert {a.protected for a in ParagraphMap(after).atoms} == {None}
    with pytest.raises(EditError):
        replace_span(middle, 0, 7, "Foo")


def test_insert_at_paragraph_end_after_hyperlink_stays_outside_link() -> None:
    p = xml_paragraph(r("See ") + f'<w:hyperlink r:id="rId1">{r("site", I)}</w:hyperlink>')
    replace_span(p, 8, 8, " Thanks.")
    link = p.find(qn("w:hyperlink"))
    assert link is not None
    assert paragraph_text(_wrap_children(link)) == "site"
    assert runs(p)[-1] == (" Thanks.", "")  # formatted like plain "See ", not like the link
    p = xml_paragraph(f'<w:hyperlink r:id="rId1">{r("site", I)}</w:hyperlink>' + r(" here"))
    replace_span(p, 0, 0, "The ")
    assert paragraph_text(_wrap_children(p.find(qn("w:hyperlink")))) == "site"  # type: ignore[arg-type]


def test_attach_left_keeps_insert_inside_hyperlink() -> None:
    p = xml_paragraph(r("See ") + f'<w:hyperlink r:id="rId1">{r("example.org")}</w:hyperlink>')
    replace_span(p, 15, 15, "/x", attach="left")
    link = p.find(qn("w:hyperlink"))
    assert link is not None
    assert paragraph_text(_wrap_children(link)) == "example.org/x"


def test_attach_right_keeps_insert_inside_hyperlink_start() -> None:
    p = xml_paragraph(r("See ") + f'<w:hyperlink r:id="rId1">{r("site")}</w:hyperlink>')
    replace_span(p, 4, 4, "web", attach="right")
    link = p.find(qn("w:hyperlink"))
    assert link is not None
    assert paragraph_text(_wrap_children(link)) == "website"
