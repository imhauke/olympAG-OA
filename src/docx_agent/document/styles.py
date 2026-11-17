"""Read-only paragraph-style catalog (style name <-> styleId), from the package's styles part.

styles.xml is parsed from its original bytes and never opened for editing, so it is
always saved byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage, PackageError, parse_xml


@dataclass(frozen=True)
class StyleCatalog:
    names: dict[str, str]  # paragraph styleId -> display name
    default_id: str | None = None

    @classmethod
    def from_package(cls, pkg: DocxPackage) -> StyleCatalog:
        part = pkg.related_part(pkg.main_document_name(), "/styles")
        if part is None:
            return cls({})
        try:
            root = parse_xml(pkg.original_part(part)).getroot()
        except etree.XMLSyntaxError as exc:
            raise PackageError(f"malformed styles part {part}: {exc}") from exc
        names: dict[str, str] = {}
        default: str | None = None
        for style in root.iterchildren(qn("w:style")):
            if style.get(qn("w:type")) != "paragraph":
                continue
            sid = style.get(qn("w:styleId"))
            if not sid:
                continue
            name_el = style.find(qn("w:name"))
            names[sid] = (name_el.get(qn("w:val")) if name_el is not None else None) or sid
            if style.get(qn("w:default")) in ("1", "true", "on"):
                default = sid
        return cls(names, default)

    def resolve(self, name_or_id: str) -> str | None:
        """styleId for a paragraph style given by display name or id (case-insensitive)."""
        wanted = name_or_id.strip().casefold()
        for sid, name in self.names.items():
            if wanted in (sid.casefold(), name.casefold()):
                return sid
        return None

    def name(self, style_id: str | None) -> str | None:
        if style_id is None:
            return None
        return self.names.get(style_id, style_id)

    def display_names(self) -> list[str]:
        return sorted(self.names.values(), key=str.casefold)
