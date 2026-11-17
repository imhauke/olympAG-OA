import json

from docx_agent.agent.session import DocumentSession
from docx_agent.agent.tools import invoke_tool, make_tools
from tests.fixtures.builders import build_xml_docx


def test_all_edit_wrappers() -> None:
    session = DocumentSession(
        build_xml_docx(
            "<w:p><w:r><w:t>First note.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Second note.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Last note.</w:t></w:r></w:p>"
        )
    )
    tools = {t.name: t for t in make_tools(session)}
    result = json.loads(invoke_tool(tools, "insert_text", {"text": " extra", "anchor": "First"}))
    assert result["after"] == "First extra note."
    result = json.loads(invoke_tool(tools, "delete_text", {"text": " extra", "block_id": "p0"}))
    assert result["after"] == "First note."
    result = json.loads(
        invoke_tool(
            tools,
            "insert_paragraph",
            {
                "anchor_id": "p0",
                "text": "New note.",
                "style": "Normal",
            },
        )
    )
    assert result["block_id"] == "n0"
    result = json.loads(invoke_tool(tools, "delete_paragraph", {"block_id": "p1"}))
    assert result["before"] == "Second note."
    assert session.validate().ok
    refused = json.loads(invoke_tool(tools, "replace_text", {"old": "absent", "new": "new"}))
    assert refused["error"] == "not_found"
    assert len(session.edits) == 4


def test_tool_schema_exposes_only_document_actions(rich_docx: bytes) -> None:
    tools = {t.name: t for t in make_tools(DocumentSession(rich_docx))}
    assert set(tools) == {
        "view_document",
        "find_text",
        "replace_text",
        "insert_text",
        "delete_text",
        "insert_paragraph",
        "delete_paragraph",
        "ask_clarification",
        "finish",
    }
    schema = tools["replace_text"].tool_call_schema.model_json_schema()
    assert schema["properties"]["old"]["type"] == "string"
    assert set(schema["required"]) == {"old", "new"}
    assert "session" not in schema["properties"]
