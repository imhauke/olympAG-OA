"""Synthetic .docx builders for tests. All content is fictitious.

`build_rich_docx()` packs the Word features that most often break naive editors
into one small document: runs with mixed formatting, lists, a table with merged
cells, an inline image, a hyperlink, a complex field, a bookmark, proofing marks,
tabs/line breaks, header/footer and a section break.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import datetime

from docx import Document
from docx.enum.section import WD_SECTION
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree
from PIL import Image

FIXED_DATE = datetime(2026, 1, 1, 9, 0, 0)

# (text, {"bold": bool, "italic": bool, "underline": bool, "size": int, "color": "RRGGBB"})
RunSpec = tuple[str, dict[str, object]]


def make_png(
    color: tuple[int, int, int] = (22, 128, 112), size: tuple[int, int] = (64, 32)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _fix_core_properties(doc: Document) -> None:  # type: ignore[valid-type]
    cp = doc.core_properties
    cp.author = "docx-agent fixtures"
    cp.last_modified_by = "docx-agent fixtures"
    cp.created = FIXED_DATE
    cp.modified = FIXED_DATE
    cp.revision = 1
    cp.title = "SYNTHETIC test document"


def _to_bytes(doc: Document) -> bytes:  # type: ignore[valid-type]
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _apply(run: object, fmt: dict[str, object]) -> None:
    font = run.font  # type: ignore[attr-defined]
    font.bold = fmt.get("bold")
    font.italic = fmt.get("italic")
    font.underline = fmt.get("underline")
    if "size" in fmt:
        font.size = Pt(int(fmt["size"]))  # type: ignore[call-overload]
    if "color" in fmt:
        font.color.rgb = RGBColor.from_string(str(fmt["color"]))


def add_runs(paragraph: object, runs: Sequence[RunSpec]) -> None:
    for text, fmt in runs:
        _apply(paragraph.add_run(text), fmt)  # type: ignore[attr-defined]


def add_hyperlink(paragraph: object, url: str, text: str) -> None:
    part = paragraph.part  # type: ignore[attr-defined]
    r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.extend([color, underline])
    t = OxmlElement("w:t")
    t.text = text
    run.extend([rpr, t])
    link.append(run)
    paragraph._p.append(link)  # type: ignore[attr-defined]


def add_complex_field(paragraph: object, instruction: str, cached: str) -> None:
    """begin / instrText / separate / cached result / end — five runs, like Word writes it."""

    def fld(kind: str) -> None:
        r = paragraph.add_run()._r  # type: ignore[attr-defined]
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), kind)
        r.append(el)

    fld("begin")
    r = paragraph.add_run()._r  # type: ignore[attr-defined]
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    r.append(instr)
    fld("separate")
    paragraph.add_run(cached)  # type: ignore[attr-defined]
    fld("end")


def add_bookmark_and_proofing(paragraph: object, name: str) -> None:
    p = paragraph._p  # type: ignore[attr-defined]
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "0")
    start.set(qn("w:name"), name)
    p.insert(1 if p.pPr is not None else 0, start)
    spell = OxmlElement("w:proofErr")
    spell.set(qn("w:type"), "spellStart")
    p.append(spell)
    paragraph.add_run("Behandlungsplan")  # type: ignore[attr-defined]
    spell_end = OxmlElement("w:proofErr")
    spell_end.set(qn("w:type"), "spellEnd")
    p.append(spell_end)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "0")
    p.append(end)


def build_rich_docx() -> bytes:
    """Body order (top-level blocks, IDs as assigned by BlockIndex):
    p0 Heading 1 | p1 mixed runs | p2,p3 bullets | p4,p5 numbered
    t0 table 3x3, row 0 = merged cell (c0 holds p0 "Goal" + empty p1) and c1 "Status"
    p6 image | p7 hyperlink | p8 PAGE field | p9 tab+break | p10 bookmark+proofErr
    p11 "End of first section." | p12 empty paragraph carrying the section break (sectPr)
    p13 last paragraph (second section)
    """
    doc = Document()
    _fix_core_properties(doc)
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "Praxis Beispiel - SYNTHETIC DATA"
    sec.footer.paragraphs[0].text = "Confidential (fictitious example)"

    doc.add_heading("Session Note - Max Mustermann", level=1)
    p = doc.add_paragraph()
    add_runs(
        p,
        [
            ("The client ", {}),
            ("Max Mustermann", {"bold": True}),
            (" attended the ", {}),
            ("weekly", {"italic": True}),
            (" session on ", {}),
            ("12 March 2026", {"bold": True, "color": "C00000"}),
            (".", {}),
        ],
    )
    doc.add_paragraph("Reported improved sleep.", style="List Bullet")
    doc.add_paragraph("Practised breathing exercises.", style="List Bullet")
    doc.add_paragraph("Continue weekly sessions.", style="List Number")
    doc.add_paragraph("Review goals in four weeks.", style="List Number")

    table = doc.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    rows = [("Goal", "", "Status"), ("Sleep", "8h", "In progress"), ("Stress", "Low", "Reached")]
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            table.cell(r, c).text = value
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.paragraphs[0].runs[0].bold = True

    doc.add_picture(io.BytesIO(make_png()), width=Inches(1))

    p = doc.add_paragraph("More information: ")
    add_hyperlink(p, "https://example.org/synthetic", "example.org")

    p = doc.add_paragraph("Page ")
    add_complex_field(p, "PAGE", "1")

    p = doc.add_paragraph()
    run = p.add_run("Name:\tMax Mustermann")
    run.add_break()
    p.add_run("Date:\t12 March 2026")

    p = doc.add_paragraph("See ")
    add_bookmark_and_proofing(p, "plan")

    doc.add_paragraph("End of first section.")
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_paragraph("Signed: Dr. Erika Beispiel (fictitious)")
    return _to_bytes(doc)


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_DECL = (
    f'xmlns:w="{W_NS}" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)


def xml_body(inner: str) -> etree._Element:
    """A w:body from raw WordprocessingML (w:, r:, m: prefixes are declared for you)."""
    return etree.fromstring(f"<w:document {_NS_DECL}><w:body>{inner}</w:body></w:document>")[0]


def xml_paragraph(inner: str) -> etree._Element:
    """A w:p (inside a w:body) from raw run-level WordprocessingML."""
    return xml_body(f"<w:p>{inner}</w:p>")[0]


def build_xml_docx(body_inner: str) -> bytes:
    """A real .docx whose body is replaced by raw XML (styles, rels etc. from python-docx)."""
    doc = Document()
    _fix_core_properties(doc)
    body = doc.element.body
    sect = body.sectPr
    for child in list(body):
        body.remove(child)
    for child in xml_body(body_inner):
        body.append(child)
    if sect is not None:
        body.append(sect)
    return _to_bytes(doc)


def build_paragraphs_docx(paragraphs: Sequence[Sequence[RunSpec]]) -> bytes:
    """One paragraph per entry, each built from explicit runs. For run-level tests."""
    doc = Document()
    _fix_core_properties(doc)
    for runs in paragraphs:
        add_runs(doc.add_paragraph(), runs)
    return _to_bytes(doc)
