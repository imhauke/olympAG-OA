"""Reproducible python-docx fixtures, never used to save agent outputs."""

from __future__ import annotations

import argparse
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.section import WD_SECTION
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.shared import Inches, RGBColor

ROOT = Path(__file__).resolve().parent
SYNTHETIC = "SYNTHETIC - fictitious practice, people and notes. Not a real clinical record."
FIXED_DATE = datetime(2026, 1, 1, 9)
ZIP_DATE = (2026, 1, 1, 9, 0, 0)


def _page_field(paragraph: object) -> None:
    field = OxmlElement("w:fldSimple")
    field.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}instr", "PAGE")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)  # type: ignore[attr-defined]


def _document(title: str) -> DocumentType:
    doc = Document()
    props = doc.core_properties
    props.author = "docx-agent synthetic samples"
    props.last_modified_by = props.author
    props.title = "SYNTHETIC: " + title
    props.created = props.modified = FIXED_DATE
    props.revision = 1
    doc.sections[0].header.paragraphs[0].text = "Praxis Beispiel | SYNTHETIC DATA"
    footer = doc.sections[0].footer.paragraphs[0]
    footer.text = "Fictitious example | Page "
    _page_field(footer)
    return doc


def _logo() -> io.BytesIO:
    # Preserve the original synthetic image; font rasterization varies by platform.
    return io.BytesIO((ROOT / "assets" / "logo.png").read_bytes())


def _link(doc: DocumentType) -> None:
    paragraph = doc.add_paragraph("Resources: ")
    link = OxmlElement("w:hyperlink")
    link.set(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
        paragraph.part.relate_to("https://example.org/synthetic", RT.HYPERLINK, is_external=True),
    )
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    underline = OxmlElement("w:u")
    underline.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "single")
    props.append(underline)
    text = OxmlElement("w:t")
    text.text = "example.org"
    run.extend([props, text])
    link.append(run)
    paragraph._p.append(link)


def _bytes(doc: DocumentType) -> bytes:
    raw, stable = io.BytesIO(), io.BytesIO()
    doc.save(raw)
    # python-docx uses wall-clock ZIP dates. Fix them for reproducible *fixture* bytes.
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(stable, "w") as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            entry.date_time = ZIP_DATE
            target.writestr(entry, data)
    return stable.getvalue()


def therapy_session_note() -> bytes:
    doc = _document("Session note")
    doc.add_heading("Session Note - Max Mustermann", 1)
    doc.add_paragraph(SYNTHETIC)
    p = doc.add_paragraph("The client ")
    p.add_run("Max Mustermann").bold = True
    p.add_run(" attended the ")
    p.add_run("weekly").italic = True
    p.add_run(" session on ")
    date = p.add_run("12 March 2026")
    date.bold = True
    date.font.color.rgb = RGBColor.from_string("C00000")
    p.add_run(".")
    doc.add_paragraph("Reported improved sleep.", "List Bullet")
    doc.add_paragraph("Practised breathing exercises.", "List Bullet")
    doc.add_paragraph("Review goals in four weeks.", "List Number")
    table = doc.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    for row, values in zip(
        table.rows,
        [
            ("Goal", "Target", "Status"),
            ("Sleep", "8h", "In progress"),
            ("Stress", "Low", "Reached"),
        ],
        strict=True,
    ):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    doc.add_paragraph("End of synthetic session note.")
    return _bytes(doc)


def treatment_plan() -> bytes:
    doc = _document("Treatment plan")
    doc.add_heading("Treatment Plan - Max Mustermann", 1)
    doc.add_paragraph(SYNTHETIC)
    doc.add_picture(_logo(), width=Inches(2.5))
    doc.add_paragraph("Practise breathing twice daily.", "List Number")
    doc.add_paragraph("Review after four weeks.", "List Number")
    doc.add_paragraph("Internal draft placeholder - remove before sharing.")
    _link(doc)
    table = doc.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Goal and target"
    table.cell(0, 2).text = "Status"
    for r, values in enumerate(
        [("Breathing", "Twice daily", "Planned"), ("Activity", "Three walks weekly", "Active")], 1
    ):
        for c, value in enumerate(values):
            table.cell(r, c).text = value
    doc.add_paragraph("End of synthetic treatment plan.")
    return _bytes(doc)


def practice_letter() -> bytes:
    doc = _document("Practice letter")
    doc.add_picture(_logo(), width=Inches(2.5))
    doc.add_heading("Praxis Beispiel - Appointment Letter", 1)
    doc.add_paragraph(SYNTHETIC)
    doc.add_paragraph("Dear Max Mustermann,")
    p = doc.add_paragraph("Your appointment is on ")
    p.add_run("20 March 2026").bold = True
    p.add_run(" at ")
    p.add_run("09:00").italic = True
    p.add_run(".")
    p = doc.add_paragraph("This draft sentence is obsolete. Please bring the completed form.")
    p.add_run("1").font.superscript = True
    doc.add_paragraph("Signed: Dr. Erika Beispiel (fictitious)")
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_heading("Notes", 2)
    doc.add_paragraph("1 The form and appointment are invented for this synthetic example.")
    return _bytes(doc)


@dataclass(frozen=True)
class Sample:
    build: Callable[[], bytes]
    instruction: str


SAMPLES = {
    "therapy_session_note": Sample(
        therapy_session_note,
        "Change the session date in the narrative from 12 March 2026 to 19 March 2026. "
        "In the goals table, change the sleep target from 8h to 7.5h. "
        'Add a bullet immediately after "Practised breathing exercises." reading '
        '"Agreed to keep a sleep diary." Preserve all other content and formatting.',
    ),
    "treatment_plan": Sample(
        treatment_plan,
        'Change "Review after four weeks." to "Review after six weeks." '
        "In the Breathing row, change the status from Planned to In progress. "
        'Delete the entire paragraph "Internal draft placeholder - remove before sharing." '
        "Preserve everything else, including the logo, hyperlink and merged table cells.",
    ),
    "practice_letter": Sample(
        practice_letter,
        "Move the appointment from 20 March 2026 at 09:00 to 27 March 2026 at 10:30. "
        'Remove the sentence "This draft sentence is obsolete." and its following space. '
        "Add a paragraph immediately after the paragraph about bringing the completed form: "
        '"Please contact the practice if you need to reschedule." '
        "Keep the logo, signature, superscript reference, sections and all other content intact.",
    ),
}


def generate(root: Path = ROOT, *, check: bool = False) -> None:
    for name, sample in SAMPLES.items():
        directory = root / name
        files = {
            "input.docx": sample.build(),
            "instruction.txt": (sample.instruction + "\n").encode(),
        }
        for filename, data in files.items():
            path = directory / filename
            if check:
                if not path.is_file() or path.read_bytes() != data:
                    raise ValueError(f"Regenerated sample differs: {path}")
            else:
                directory.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Compare committed inputs without writing."
    )
    args = parser.parse_args()
    generate(check=args.check)
    print(
        "Synthetic sample inputs match."
        if args.check
        else "Generated three synthetic sample inputs."
    )


if __name__ == "__main__":
    main()
