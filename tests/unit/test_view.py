"""The ID-tagged view: what the agent sees must be quotable back into the ops."""

from __future__ import annotations

import json

import pytest

from docx_agent.document import ops
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.package import DocxPackage
from docx_agent.document.styles import StyleCatalog
from docx_agent.document.view import HEADER, LONG_NOTE, render
from tests.fixtures.builders import xml_body


@pytest.fixture
def view_parts(rich_docx: bytes) -> tuple[BlockIndex, StyleCatalog]:
    pkg = DocxPackage.from_bytes(rich_docx)
    return BlockIndex.from_package(pkg), StyleCatalog.from_package(pkg)


def test_rich_view(view_parts: tuple[BlockIndex, StyleCatalog]) -> None:
    lines = render(*view_parts).splitlines()
    assert lines[0] == HEADER
    assert lines[1] == '[p0] (heading 1) "Session Note - Max Mustermann"'
    assert '[p2] (List Bullet) "Reported improved sleep."' in lines
    assert "[t0] TABLE 3 rows x 3 cols" in lines
    assert '  [t0.r1.c2.p0] "In progress"' in lines
    assert '[p6] "[IMAGE]"' in lines
    assert '[p8] "Page 1" [read-only: field]' in lines
    assert '[p12] "" [read-only: section break]' in lines


def test_quoted_view_text_round_trips_into_ops(view_parts: tuple[BlockIndex, StyleCatalog]) -> None:
    index, styles = view_parts
    line = next(x for x in render(index, styles).splitlines() if x.startswith("[p9]"))
    quoted = json.loads(line.split(" ", 1)[1])  # what an LLM would copy from the view
    result = ops.replace_text(index, quoted, quoted.replace("Max", "Erika"), block_id="p9")
    assert result.after == "Name:\tErika Mustermann\nDate:\t12 March 2026"


def test_range_and_budget(view_parts: tuple[BlockIndex, StyleCatalog]) -> None:
    index, styles = view_parts
    ranged = render(index, styles, from_id="p6", to_id="p8").splitlines()
    assert [x.split(" ")[0] for x in ranged[1:]] == ["[p6]", "[p7]", "[p8]"]
    small = render(index, styles, max_chars=400)
    assert "Long document" in small
    assert "more blocks from" in small
    assert len(small) <= 400 + len(HEADER) + 1
    with pytest.raises(EditError) as exc:
        render(index, styles, from_id="p99")
    assert exc.value.code == "unknown_block"
    with pytest.raises(EditError):
        render(index, styles, from_id="p8", to_id="p6")


def test_long_paragraphs_are_shortened_when_over_budget() -> None:
    index = BlockIndex(xml_body(f"<w:p><w:r><w:t>{'word ' * 100}</w:t></w:r></w:p>"))
    view = render(index, max_chars=300)
    assert '..."' in view


def test_list_items_and_unknown_styles_without_catalog() -> None:
    index = BlockIndex(
        xml_body(
            '<w:p><w:pPr><w:pStyle w:val="Custom"/><w:numPr><w:ilvl w:val="0"/></w:numPr>'
            "</w:pPr><w:r><w:t>item</w:t></w:r></w:p>"
        )
    )
    assert render(index).splitlines()[1] == '[p0] (Custom) (list item) "item"'


def test_budget_cut_just_before_the_last_block() -> None:
    """Everything but the last block fits: the cut must name it, not index past the end."""
    short = "".join(f"<w:p><w:r><w:t>p{i}</w:t></w:r></w:p>" for i in range(3))
    index = BlockIndex(xml_body(short + f"<w:p><w:r><w:t>{'x' * 500}</w:t></w:r></w:p>"))
    head = "\n".join([LONG_NOTE, '[p0] "p0"', '[p1] "p1"', '[p2] "p2"'])
    budget = len(head) + 60  # room for the "more blocks" note, not for the shortened p3
    view = render(index, max_chars=budget)
    assert view.endswith("(... 1 more blocks from p3; use from_id=p3.)")
    assert len(view.split("\n", 1)[1]) <= budget
