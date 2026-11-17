"""Independent, case-defined assertions; model review cannot decide the eval score."""

import io
from collections import Counter
from typing import Any

from docx import Document
from lxml import etree

from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.integrity import check
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import char_formats, paragraph_text
from docx_agent.document.styles import StyleCatalog
from evals.case import Case


def verify_output(case: Case, before: bytes, after: bytes) -> tuple[list[str], dict[str, Any]]:
    touched = set(case.changed_before) | {f"n{i}" for i in range(case.inserted)}
    report = check(before, after, touched=touched, style_changes=case.style_changes)
    failures = list(report.violations)
    actual = {c.before_id for c in report.changes if c.before_id is not None}
    if actual != set(case.changed_before):
        failures.append(
            f"Changed original blocks: expected {sorted(case.changed_before)}, "
            f"got {sorted(actual)}."
        )
    counts = Counter(change.kind for change in report.changes)
    for kind, expected in (("inserted", case.inserted), ("deleted", case.deleted)):
        if counts[kind] != expected:
            failures.append(f"Expected {expected} {kind} paragraphs, got {counts[kind]}.")
    before_index = BlockIndex.from_package(DocxPackage.from_bytes(before))
    package = DocxPackage.from_bytes(after)
    after_index = BlockIndex.from_package(package)
    styles = StyleCatalog.from_package(package)
    all_text = "\n".join(
        paragraph_text(after_index.paragraph(b)) for b in after_index.paragraph_ids()
    )
    for text in case.absent_text:
        if text in all_text:
            failures.append(f"Forbidden output text remains: {text!r}.")
    for expected in case.blocks:
        try:
            paragraph = after_index.paragraph(expected.block_id)
            text = paragraph_text(paragraph)
            if expected.equals is not None and text != expected.equals:
                failures.append(f"{expected.block_id}: expected {expected.equals!r}, got {text!r}.")
            for needle in expected.contains:
                if needle not in text:
                    failures.append(f"{expected.block_id}: missing {needle!r}.")
            for needle in expected.not_contains:
                if needle in text:
                    failures.append(f"{expected.block_id}: unexpected {needle!r}.")
            if expected.style is not None:
                style = paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
                sid = style.get(qn("w:val")) if style is not None else styles.default_id
                if sid != expected.style:
                    failures.append(
                        f"{expected.block_id}: expected style {expected.style}, got {sid}."
                    )
            if expected.unchanged or expected.formats_unchanged:
                original = before_index.paragraph(expected.before_id or expected.block_id)
                if expected.unchanged and etree.tostring(original, method="c14n") != etree.tostring(
                    paragraph, method="c14n"
                ):
                    failures.append(f"{expected.block_id}: untouched paragraph XML changed.")
                if expected.formats_unchanged and (
                    [fmt for _, fmt in char_formats(original)]
                    != [fmt for _, fmt in char_formats(paragraph)]
                ):
                    failures.append(f"{expected.block_id}: character-format sequence changed.")
        except EditError as exc:
            failures.append(f"{expected.block_id}: {exc.message}")
    try:
        Document(io.BytesIO(after))
    except Exception as exc:
        failures.append(f"python-docx could not reopen output: {type(exc).__name__}.")
    return failures, report.to_dict()
