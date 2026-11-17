"""German documents and instructions: umlauts, ß and German typography through the real engine."""

from io import BytesIO

from docx import Document

from docx_agent.agent.session import DocumentSession
from docx_agent.document import ops
from docx_agent.document.integrity import check
from docx_agent.web.diff import segments
from tests.fixtures.builders import build_paragraphs_docx

DASH = chr(0x2013)  # en dash, as Word types it
GERMAN = build_paragraphs_docx(
    [
        [
            ("Der Klient ", {}),
            ("Jürgen Groß", {"bold": True}),
            (f" schläft „ungefähr“ 8 Stunden {DASH} laut Tagebuch.", {"italic": True}),
        ],
        [("Übungen: Atemübung, Spaziergang", {})],
    ]
)


def _runs(data: bytes, index: int) -> list[tuple[str, bool, bool]]:
    paragraph = Document(BytesIO(data)).paragraphs[index]
    return [(r.text, bool(r.bold), bool(r.italic)) for r in paragraph.runs]


def test_german_text_edit_keeps_formatting_and_integrity() -> None:
    session = DocumentSession(GERMAN)
    # The model typed straight quotes and a hyphen; the document uses „…“ and an en dash.
    session.apply(
        lambda: ops.replace_text(
            session.index,
            'schläft "ungefähr" 8 Stunden - laut',
            'schläft "ungefähr" 7 Stunden - laut',
        )
    )
    session.apply(lambda: ops.replace_text(session.index, "Groß", "Größer"))
    assert session.validate().ok
    output = session.package.to_bytes()
    assert check(GERMAN, output).ok
    runs = _runs(output, 0)
    assert "".join(t for t, *_ in runs) == (
        f"Der Klient Jürgen Größer schläft „ungefähr“ 7 Stunden {DASH} laut Tagebuch."
    )
    assert ("Jürgen Größer", True, False) in runs  # bold name stays bold
    assert all(italic for t, _, italic in runs if "Stunden" in t)  # German typography kept


def test_german_paragraph_insert_and_word_diff() -> None:
    session = DocumentSession(GERMAN)
    session.apply(lambda: ops.replace_text(session.index, "Atemübung", "Atemübungen"))
    assert session.validate().ok
    diff = segments("Übungen: Atemübung, Spaziergang", "Übungen: Atemübungen, Spaziergang")
    assert {"kind": "insert", "text": "Atemübungen"} in diff  # umlaut words are whole tokens
