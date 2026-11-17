"""WordprocessingML namespaces and a tiny qualified-name helper."""

from __future__ import annotations

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"

OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)

NSMAP = {"w": W, "r": R}


def qn(tag: str) -> str:
    """'w:p' -> '{http://...main}p'."""
    prefix, local = tag.split(":")
    uri = {"w": W, "r": R, "xml": XML}[prefix]
    return f"{{{uri}}}{local}"
