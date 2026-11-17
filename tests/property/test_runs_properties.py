"""Property-based tests for span edits (hypothesis).

For random paragraphs (random run splits and formatting, optionally mixed with hyperlinks,
images and fields) and random edits, an edit must either be refused with the paragraph left
byte-for-byte unchanged, or produce exactly the expected text while every character outside
the edited span keeps its formatting and no protected content disappears.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from hypothesis import given
from hypothesis import strategies as st
from lxml import etree

from docx_agent.document.errors import EditError
from docx_agent.document.ns import qn
from docx_agent.document.ops import _fold_char, minimal_change, restore_typography
from docx_agent.document.runs import ParagraphMap, char_formats, paragraph_text, replace_span
from tests.fixtures.builders import xml_paragraph

FORMATS = [
    "",
    "<w:b/>",
    "<w:i/>",
    "<w:b/><w:i/>",
    '<w:color w:val="C00000"/>',
    '<w:sz w:val="28"/>',
]
TEXT = st.text(alphabet="ab \t\n", min_size=1, max_size=6)
NEW_TEXT = st.text(alphabet="XY \t\n", max_size=5)


def run_xml(text: str, fmt: str) -> str:
    parts: list[str] = []
    buf = ""
    for ch in text + "\0":
        if ch in "\t\n\0":
            if buf:
                space = ' xml:space="preserve"' if buf != buf.strip() else ""
                parts.append(f"<w:t{space}>{escape(buf)}</w:t>")
                buf = ""
            if ch == "\t":
                parts.append("<w:tab/>")
            elif ch == "\n":
                parts.append("<w:br/>")
        else:
            buf += ch
    rpr = f"<w:rPr>{fmt}</w:rPr>" if fmt else ""
    return f"<w:r>{rpr}{''.join(parts)}</w:r>"


plain_runs = st.lists(st.tuples(TEXT, st.sampled_from(FORMATS)), min_size=1, max_size=6)

FIELD = (
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
    "<w:r><w:instrText> PAGE </w:instrText></w:r>"
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    "<w:r><w:t>7</w:t></w:r>"
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
)
DRAWING = '<w:r><w:drawing><img id="1"/></w:drawing></w:r>'


@st.composite
def mixed_items(draw: st.DrawFn) -> str:
    kind = draw(st.sampled_from(["run", "run", "run", "link", "drawing", "field", "bookmark"]))
    if kind == "drawing":
        return DRAWING
    if kind == "field":
        return FIELD
    if kind == "bookmark":
        return '<w:bookmarkStart w:id="0" w:name="b"/>'
    item = run_xml(draw(TEXT), draw(st.sampled_from(FORMATS)))
    return f'<w:hyperlink r:id="rId9">{item}</w:hyperlink>' if kind == "link" else item


@st.composite
def span(draw: st.DrawFn, length: int) -> tuple[int, int]:
    start = draw(st.integers(0, length))
    return start, draw(st.integers(start, length))


def _counts(p: etree._Element) -> tuple[int, ...]:
    return tuple(
        len(p.findall(f".//{qn(tag)}"))
        for tag in ("w:drawing", "w:fldChar", "w:instrText", "w:bookmarkStart")
    )


def _assert_well_formed(p: etree._Element) -> None:
    for t in p.iter(qn("w:t")):
        text = t.text or ""
        assert text, "empty w:t left behind"
        if text != text.strip():
            assert t.get(qn("xml:space")) == "preserve"
    for r in p.iter(qn("w:r")):
        assert any(c.tag != qn("w:rPr") for c in r), "empty run left behind"
    reparsed = etree.fromstring(etree.tostring(p))
    assert paragraph_text(reparsed) == paragraph_text(p)


@given(plain_runs, st.data(), NEW_TEXT)
def test_plain_runs_edit_is_exact_and_formatting_preserving(
    runs: list[tuple[str, str]], data: st.DataObject, new: str
) -> None:
    p = xml_paragraph("".join(run_xml(t, f) for t, f in runs))
    old = paragraph_text(p)
    before = char_formats(p)
    start, end = data.draw(span(len(old)))

    replace_span(p, start, end, new)

    assert paragraph_text(p) == old[:start] + new + old[end:]
    after = char_formats(p)
    assert after[:start] == before[:start]
    assert after[start + len(new) :] == before[end:]
    if new:
        # inherits the replaced text's format; for an insert, the preceding (else following) one
        source = start if start < end or start == 0 else start - 1
        expected = before[source][1] if source < len(before) else b""
        assert {f for _, f in after[start : start + len(new)]} == {expected}
    _assert_well_formed(p)


@given(st.lists(mixed_items(), min_size=1, max_size=7), st.data(), NEW_TEXT)
def test_mixed_content_is_never_corrupted(items: list[str], data: st.DataObject, new: str) -> None:
    p = xml_paragraph("".join(items))
    old = paragraph_text(p)
    before_xml = etree.tostring(p, method="c14n")
    before = char_formats(p)
    counts = _counts(p)
    start, end = data.draw(span(len(old)))

    try:
        replace_span(p, start, end, new)
    except EditError:
        assert etree.tostring(p, method="c14n") == before_xml
        return

    assert paragraph_text(p) == old[:start] + new + old[end:]
    after = char_formats(p)
    assert after[:start] == before[:start]
    assert after[start + len(new) :] == before[end:]
    assert _counts(p) == counts  # images, fields and bookmarks all survive
    pm = ParagraphMap(p)
    assert all(a.protected is None for a in pm.atoms[start : start + len(new)])
    _assert_well_formed(p)


@given(st.text(alphabet="abc", max_size=8), st.data(), st.text(alphabet="abc", max_size=8))
def test_minimal_change_is_equivalent(text: str, data: st.DataObject, new: str) -> None:
    start, end = data.draw(span(len(text)))
    s, e, n = minimal_change(text, start, end, new)
    assert start <= s <= e <= end
    assert text[:s] + n + text[e:] == text[:start] + new + text[end:]
    assert len(n) <= len(new)


TYPO = "ab -'\t " + "".join(map(chr, (0x2013, 0x2019, 0x00A0)))


@given(st.text(alphabet=TYPO, max_size=10), st.text(alphabet=TYPO, max_size=10))
def test_restore_typography_only_swaps_equivalent_characters(old: str, new: str) -> None:
    restored = restore_typography(old, new)
    assert len(restored) == len(new)
    assert [_fold_char(c) for c in restored] == [_fold_char(c) for c in new]
    assert restore_typography(old, old) == old
