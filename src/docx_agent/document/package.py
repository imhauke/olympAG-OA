"""Minimal OPC (zip) package layer for .docx files.

Design goal: **the smallest possible blast radius**. Only XML parts we explicitly
open for editing are parsed and re-serialized; every other zip entry is written
back with its original (uncompressed) bytes, name, order, compression type,
timestamp and attributes. That lets the integrity checker prove that images,
styles, numbering, relationships, headers, etc. are untouched. (Compressed
bytes may differ because zlib re-deflates; part *content* is what is preserved.)
"""

from __future__ import annotations

import copy
import io
import posixpath
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from lxml import etree

from docx_agent.document.ns import OFFICE_DOCUMENT_REL, PKG_REL

# Defensive limits: a .docx is untrusted input (later served through an API).
MAX_PARTS = 2_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    remove_blank_text=False,
    huge_tree=False,
)


class PackageError(ValueError):
    """The input is not a usable .docx package."""


def parse_xml(data: bytes) -> etree._ElementTree:
    return etree.ElementTree(etree.fromstring(data, _PARSER))


def serialize_xml(tree: etree._ElementTree) -> bytes:
    """Serialize like Word does: UTF-8 declaration, standalone="yes" when the input had it."""
    if tree.docinfo.standalone:
        out = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
    else:
        out = etree.tostring(tree, xml_declaration=True, encoding="UTF-8")
    assert isinstance(out, bytes)
    return out


@dataclass(frozen=True)
class _Entry:
    info: zipfile.ZipInfo
    data: bytes


@dataclass
class DocxPackage:
    """An in-memory .docx package. Parts are addressed by zip name, e.g. 'word/document.xml'."""

    _entries: list[_Entry]
    _by_name: dict[str, _Entry] = field(init=False)
    _trees: dict[str, etree._ElementTree] = field(default_factory=dict)
    _main: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self._by_name = {}
        for e in self._entries:
            if e.info.filename in self._by_name:
                # Readers disagree on which duplicate wins; never guess.
                raise PackageError(f"duplicate part name {e.info.filename!r}")
            self._by_name[e.info.filename] = e

    # ------------------------------------------------------------------ load
    @classmethod
    def from_bytes(cls, blob: bytes) -> DocxPackage:
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise PackageError("not a zip archive (is this really a .docx?)") from exc
        with zf:
            infos = zf.infolist()
            if len(infos) > MAX_PARTS:
                raise PackageError(f"too many parts ({len(infos)})")
            if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
                raise PackageError("package too large when uncompressed")
            if any(i.flag_bits & 0x1 for i in infos):
                raise PackageError("encrypted packages are not supported")
            try:
                entries = [_Entry(info=i, data=zf.read(i)) for i in infos]
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
                raise PackageError(f"corrupt or unsupported zip member: {exc}") from exc
        pkg = cls(entries)
        if "[Content_Types].xml" not in pkg._by_name:
            raise PackageError("missing [Content_Types].xml")
        pkg._main = pkg._resolve_main_document()
        return pkg

    @classmethod
    def open(cls, path: str | Path) -> DocxPackage:
        return cls.from_bytes(Path(path).read_bytes())

    # ------------------------------------------------------------------ query
    def part_names(self) -> list[str]:
        return [e.info.filename for e in self._entries]

    def read_part(self, name: str) -> bytes:
        """Current bytes of a part (re-serialized if it was opened for editing)."""
        if name in self._trees:
            return serialize_xml(self._trees[name])
        return self._by_name[name].data

    def part_info(self, name: str) -> zipfile.ZipInfo:
        """Zip metadata (compression type, timestamp, ...) of a part, as loaded."""
        return copy.copy(self._by_name[name].info)

    def original_part(self, name: str) -> bytes:
        """Bytes of the part exactly as loaded."""
        return self._by_name[name].data

    def main_document_name(self) -> str:
        """Main document part, resolved via /_rels/.rels (usually 'word/document.xml')."""
        return self._main

    def _resolve_main_document(self) -> str:
        if "_rels/.rels" not in self._by_name:
            raise PackageError("missing _rels/.rels")
        try:
            rels = etree.fromstring(self.original_part("_rels/.rels"), _PARSER)
        except etree.XMLSyntaxError as exc:
            raise PackageError(f"malformed _rels/.rels: {exc}") from exc
        for rel in rels.iter(f"{{{PKG_REL}}}Relationship"):
            if rel.get("Type") == OFFICE_DOCUMENT_REL:
                name = str(PurePosixPath(rel.get("Target", "").lstrip("/")))
                if name in self._by_name:
                    return name
        raise PackageError("no main document part (officeDocument relationship)")

    def related_part(self, source: str, rel_type_suffix: str) -> str | None:
        """Part targeted by `source`'s first internal relationship whose Type ends with
        `rel_type_suffix` (e.g. "/styles"). Read-only: the .rels part is not opened for editing."""
        src = PurePosixPath(source)
        rels_name = str(src.parent / "_rels" / f"{src.name}.rels")
        if rels_name not in self._by_name:
            return None
        try:
            rels = etree.fromstring(self.original_part(rels_name), _PARSER)
        except etree.XMLSyntaxError as exc:
            raise PackageError(f"malformed {rels_name}: {exc}") from exc
        for rel in rels.iter(f"{{{PKG_REL}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            if rel.get("Type", "").endswith(rel_type_suffix):
                target = rel.get("Target", "")
                path = target.lstrip("/") if target.startswith("/") else str(src.parent / target)
                name = posixpath.normpath(path)
                return name if name in self._by_name else None
        return None

    # ------------------------------------------------------------------ edit
    def xml(self, name: str) -> etree._ElementTree:
        """Open an XML part for editing. Only parts opened here are re-serialized on save."""
        if name not in self._trees:
            try:
                self._trees[name] = parse_xml(self._by_name[name].data)
            except etree.XMLSyntaxError as exc:
                raise PackageError(f"malformed XML in {name}: {exc}") from exc
        return self._trees[name]

    def document(self) -> etree._ElementTree:
        return self.xml(self._main)

    @property
    def opened_parts(self) -> frozenset[str]:
        return frozenset(self._trees)

    # ------------------------------------------------------------------ save
    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for e in self._entries:
                # A copy of the original ZipInfo keeps name, order, timestamp, compression
                # type and attributes; copying avoids mutating our pristine metadata.
                out.writestr(copy.copy(e.info), self.read_part(e.info.filename))
        return buf.getvalue()

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())
