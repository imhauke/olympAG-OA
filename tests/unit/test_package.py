"""Package layer: a load/save with no edits must not change anything observable."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import docx
import pytest
from lxml import etree

from docx_agent.document import package as package_mod
from docx_agent.document.package import DocxPackage, PackageError
from tests.fixtures.builders import build_rich_docx


def _parts(blob: bytes) -> list[tuple[str, int, bytes]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return [(i.filename, i.compress_type, zf.read(i)) for i in zf.infolist()]


def _c14n(data: bytes) -> bytes:
    return etree.tostring(etree.fromstring(data), method="c14n")


def _rezip(blob: bytes, transform: dict[str, bytes | None]) -> bytes:
    """Copy a zip, replacing (bytes) or dropping (None) selected parts."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            if info.filename in transform:
                new = transform[info.filename]
                if new is not None:
                    dst.writestr(info, new)
            else:
                dst.writestr(info, src.read(info))
    return out.getvalue()


def test_noop_roundtrip_is_part_identical(rich_docx: bytes) -> None:
    out = DocxPackage.from_bytes(rich_docx).to_bytes()
    assert _parts(out) == _parts(rich_docx)  # names, order, compression type, bytes


def test_opened_but_unchanged_document_is_c14n_identical(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    name = pkg.main_document_name()
    pkg.document()  # parse -> will be re-serialized on save
    out = DocxPackage.from_bytes(pkg.to_bytes())
    assert _c14n(out.original_part(name)) == _c14n(pkg.original_part(name))
    for part in pkg.part_names():
        if part != name:
            assert out.original_part(part) == pkg.original_part(part)


def test_editing_document_changes_only_that_part(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    body = pkg.document().getroot()
    first_t = next(body.iter("{*}t"))
    first_t.text = "EDITED"
    out = DocxPackage.from_bytes(pkg.to_bytes())
    changed = [n for n in pkg.part_names() if out.original_part(n) != pkg.original_part(n)]
    assert changed == [pkg.main_document_name()]
    assert pkg.opened_parts == {"word/document.xml"}


def test_output_opens_in_python_docx(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    pkg.document()
    d = docx.Document(io.BytesIO(pkg.to_bytes()))
    assert d.paragraphs[0].text == "Session Note - Max Mustermann"
    assert len(d.tables) == 1
    assert len(d.inline_shapes) == 1


def test_standalone_declaration_is_preserved(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    pkg.document()
    assert pkg.read_part("word/document.xml").startswith(
        b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
    )


def test_save_and_open_from_path(rich_docx: bytes, tmp_path: Path) -> None:
    path = tmp_path / "out.docx"
    DocxPackage.from_bytes(rich_docx).save(path)
    assert _parts(DocxPackage.open(path).to_bytes()) == _parts(rich_docx)


def test_main_document_resolved_through_relationships(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(rich_docx)
    rels = pkg.original_part("_rels/.rels").replace(b"word/document.xml", b"word/main.xml")
    ctypes = pkg.original_part("[Content_Types].xml").replace(
        b"/word/document.xml", b"/word/main.xml"
    )
    blob = _rezip(rich_docx, {"_rels/.rels": rels, "[Content_Types].xml": ctypes})
    # rename the part itself
    blob = _rezip(blob, {"word/document.xml": None})
    buf = io.BytesIO(blob)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("word/main.xml", pkg.original_part("word/document.xml"))
    assert DocxPackage.from_bytes(buf.getvalue()).main_document_name() == "word/main.xml"


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        ({"[Content_Types].xml": None}, "Content_Types"),
        ({"_rels/.rels": None}, "_rels/.rels"),
        ({"word/document.xml": None}, "main document"),
    ],
)
def test_rejects_broken_packages(
    rich_docx: bytes, transform: dict[str, bytes | None], message: str
) -> None:
    with pytest.raises(PackageError, match=message):
        DocxPackage.from_bytes(_rezip(rich_docx, transform))


def test_rejects_non_zip() -> None:
    with pytest.raises(PackageError, match="not a zip"):
        DocxPackage.from_bytes(b"%PDF-1.7 definitely not a docx")


def test_rejects_too_many_parts(rich_docx: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_mod, "MAX_PARTS", 3)
    with pytest.raises(PackageError, match="too many parts"):
        DocxPackage.from_bytes(rich_docx)


def test_rejects_oversized_package(rich_docx: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_mod, "MAX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(PackageError, match="too large"):
        DocxPackage.from_bytes(rich_docx)


def test_xml_parser_does_not_expand_entities() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'
    root = package_mod.parse_xml(evil).getroot()
    assert "boom" not in etree.tostring(root).decode()


def test_fixture_builder_is_deterministic() -> None:
    """Same synthetic input every run (CI checks committed samples against regeneration)."""
    assert _parts(build_rich_docx()) == _parts(build_rich_docx())


def test_serialize_without_standalone_keeps_it_absent() -> None:
    tree = package_mod.parse_xml(b'<?xml version="1.0" encoding="UTF-8"?><x/>')
    assert b"standalone" not in package_mod.serialize_xml(tree)


def test_rejects_encrypted_entries(rich_docx: bytes) -> None:
    # Set the "encrypted" general-purpose flag bit in the first central-directory record.
    blob = bytearray(rich_docx)
    cd = blob.index(b"PK\x01\x02")
    blob[cd + 8] |= 0x1
    with pytest.raises(PackageError, match="encrypted"):
        DocxPackage.from_bytes(bytes(blob))


# --- regressions from /checkpoint review (step 2) ---------------------------------


def test_rejects_duplicate_part_names(rich_docx: bytes) -> None:
    buf = io.BytesIO(rich_docx)
    with zipfile.ZipFile(buf, "a") as zf, pytest.warns(UserWarning, match="Duplicate"):
        zf.writestr("word/document.xml", b"<x/>")
    with pytest.raises(PackageError, match="duplicate part"):
        DocxPackage.from_bytes(buf.getvalue())


def test_malformed_rels_is_a_package_error(rich_docx: bytes) -> None:
    with pytest.raises(PackageError, match=r"malformed _rels/\.rels"):
        DocxPackage.from_bytes(_rezip(rich_docx, {"_rels/.rels": b"<bad"}))


def test_corrupt_member_is_a_package_error(rich_docx: bytes) -> None:
    blob = bytearray(rich_docx)
    with zipfile.ZipFile(io.BytesIO(rich_docx)) as zf:
        info = zf.infolist()[-1]
        # local header (30 bytes) + name + extra, then compressed data
        start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    blob[start + 5] ^= 0xFF
    with pytest.raises(PackageError, match="corrupt"):
        DocxPackage.from_bytes(bytes(blob))


def test_roundtrip_preserves_zip_metadata(rich_docx: bytes) -> None:
    def meta(blob: bytes) -> list[tuple[object, ...]]:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            return [
                (i.filename, i.date_time, i.external_attr, i.compress_type, i.comment)
                for i in zf.infolist()
            ]

    pkg = DocxPackage.from_bytes(rich_docx)
    first = pkg.to_bytes()
    assert meta(first) == meta(rich_docx)
    assert pkg.to_bytes() == first  # saving twice is stable (no shared-state mutation)


def test_malformed_part_is_a_package_error_when_opened(rich_docx: bytes) -> None:
    pkg = DocxPackage.from_bytes(_rezip(rich_docx, {"word/document.xml": b"<w:document"}))
    with pytest.raises(PackageError, match=r"malformed XML in word/document\.xml"):
        pkg.document()
