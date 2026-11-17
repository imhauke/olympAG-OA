from hypothesis import example, given
from hypothesis import strategies as st

from docx_agent.document import ops
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.runs import char_formats, paragraph_text
from tests.fixtures.builders import xml_body

word = st.text(alphabet="abcdef", min_size=1, max_size=15)


@given(left=word, right=word, new_left=word, new_right=word)
@example(left="a", right="aaaaaaaaaaaa", new_left="aaaaaaaaaaaa", new_right="a")
def test_disjoint_replacements_keep_the_unchanged_formatted_middle(
    left: str, right: str, new_left: str, new_right: str
) -> None:
    middle = " MIDPOINT "
    body = xml_body(
        f"<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{left}</w:t></w:r>"
        f'<w:r><w:t xml:space="preserve">{middle}</w:t></w:r>'
        f"<w:r><w:rPr><w:i/></w:rPr><w:t>{right}</w:t></w:r></w:p>"
    )
    index = BlockIndex(body)
    original = char_formats(index.paragraph("p0"))[len(left) : len(left) + len(middle)]
    ops.replace_text(index, left + middle + right, new_left + middle + new_right)
    assert paragraph_text(index.paragraph("p0")) == new_left + middle + new_right
    after = char_formats(index.paragraph("p0"))[len(new_left) : len(new_left) + len(middle)]
    assert after == original
