"""Style catalog and the read-only relationship lookup it relies on."""

from __future__ import annotations

import pytest

from docx_agent.document.package import DocxPackage, PackageError
from docx_agent.document.styles import StyleCatalog
from tests.fixtures.builders import W_NS, build_paragraphs_docx
from tests.unit.test_package import _rezip

DOC_RELS = "word/_rels/document.xml.rels"
PKG_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
STYLES_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"


def _rels(target: str, mode: str = "") -> bytes:
    extra = f' TargetMode="{mode}"' if mode else ""
    return (
        f'<Relationships xmlns="{PKG_RELS_NS}">'
        f'<Relationship Id="rId1" Type="{STYLES_TYPE}" Target="{target}"{extra}/>'
        "</Relationships>"
    ).encode()


@pytest.fixture(scope="module")
def blob() -> bytes:
    return build_paragraphs_docx([[("x", {})]])


def test_catalog_resolves_names_and_ids_case_insensitively(blob: bytes) -> None:
    pkg = DocxPackage.from_bytes(blob)
    cat = StyleCatalog.from_package(pkg)
    assert cat.resolve("Heading 1") == "Heading1"
    assert cat.resolve("heading1") == "Heading1"
    assert cat.resolve(" list bullet ") == "ListBullet"
    assert cat.resolve("Nope") is None
    assert cat.default_id == "Normal"
    assert cat.name("Heading1") == "heading 1"
    assert cat.name("Unknown") == "Unknown"
    assert cat.name(None) is None
    assert "word/styles.xml" not in pkg.opened_parts  # read-only


def test_missing_or_external_styles_relationship_gives_empty_catalog(blob: bytes) -> None:
    no_rel = _rezip(blob, {DOC_RELS: f'<Relationships xmlns="{PKG_RELS_NS}"/>'.encode()})
    assert StyleCatalog.from_package(DocxPackage.from_bytes(no_rel)).names == {}
    external = _rezip(blob, {DOC_RELS: _rels("https://example.org/s.xml", "External")})
    assert DocxPackage.from_bytes(external).related_part("word/document.xml", "/styles") is None
    missing_target = _rezip(blob, {DOC_RELS: _rels("nothere.xml")})
    pkg = DocxPackage.from_bytes(missing_target)
    assert pkg.related_part("word/document.xml", "/styles") is None


def test_absolute_relationship_target(blob: bytes) -> None:
    pkg = DocxPackage.from_bytes(_rezip(blob, {DOC_RELS: _rels("/word/styles.xml")}))
    assert pkg.related_part("word/document.xml", "/styles") == "word/styles.xml"


def test_malformed_parts_raise_package_error(blob: bytes) -> None:
    bad_rels = DocxPackage.from_bytes(_rezip(blob, {DOC_RELS: b"<oops"}))
    with pytest.raises(PackageError, match="malformed"):
        bad_rels.related_part("word/document.xml", "/styles")
    bad_styles = DocxPackage.from_bytes(_rezip(blob, {"word/styles.xml": b"<oops"}))
    with pytest.raises(PackageError, match="styles"):
        StyleCatalog.from_package(bad_styles)


def test_style_without_name_or_id(blob: bytes) -> None:
    styles = (
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:style w:type="paragraph" w:styleId="Bare"/>'
        '<w:style w:type="paragraph"><w:name w:val="No id"/></w:style>'
        '<w:style w:type="character" w:styleId="Strong"><w:name w:val="Strong"/></w:style>'
        "</w:styles>"
    ).encode()
    cat = StyleCatalog.from_package(
        DocxPackage.from_bytes(_rezip(blob, {"word/styles.xml": styles}))
    )
    assert cat.names == {"Bare": "Bare"}
    assert cat.default_id is None
    assert cat.display_names() == ["Bare"]
