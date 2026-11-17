"""Thin, typed LLM-facing wrappers; all mutations go through document.ops."""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from docx_agent.agent.session import DocumentSession
from docx_agent.document import ops
from docx_agent.document.errors import EditError
from docx_agent.document.view import render


def make_tools(session: DocumentSession) -> list[BaseTool]:
    """Bind tools to the session supplied by LangGraph runtime context."""

    def edit(operation: Callable[[], ops.EditResult]) -> str:
        try:
            return json.dumps(session.apply(operation).to_dict(), ensure_ascii=False)
        except EditError as exc:
            return json.dumps(exc.to_dict(), ensure_ascii=False)

    @tool
    def view_document(
        from_id: str | None = None, to_id: str | None = None, max_chars: int = 20_000
    ) -> str:
        """Read ID-tagged paragraphs and tables. Page through shortened views using from_id."""
        return render(
            session.index, session.styles, from_id=from_id, to_id=to_id, max_chars=max_chars
        )

    @tool
    def find_text(query: str, scope: str | None = None) -> str:
        """Find a quote in the document or block scope, with IDs and surrounding context."""
        return json.dumps(
            [asdict(m) for m in ops.find_text(session.index, query, scope)], ensure_ascii=False
        )

    @tool
    def replace_text(
        old: str, new: str, block_id: str | None = None, occurrence: int | None = None
    ) -> str:
        """Replace one quote, preserving formatting outside the changed text.

        Ambiguous matches need a block_id and 1-based occurrence within that block.
        """
        return edit(
            lambda: ops.replace_text(
                session.index, old, new, block_id=block_id, occurrence=occurrence
            )
        )

    @tool
    def insert_text(
        text: str,
        *,
        block_id: str | None = None,
        anchor: str | None = None,
        side: Literal["before", "after"] = "after",
        position: Literal["start", "end"] | None = None,
        occurrence: int | None = None,
    ) -> str:
        """Insert text beside an anchor quote OR at a paragraph's start/end position."""
        return edit(
            lambda: ops.insert_text(
                session.index,
                text,
                block_id=block_id,
                anchor=anchor,
                side=side,
                position=position,
                occurrence=occurrence,
            )
        )

    @tool
    def delete_text(text: str, block_id: str | None = None, occurrence: int | None = None) -> str:
        """Delete one quote. Include a neighbouring space when needed."""
        return edit(
            lambda: ops.delete_text(session.index, text, block_id=block_id, occurrence=occurrence)
        )

    @tool
    def insert_paragraph(
        anchor_id: str,
        text: str,
        side: Literal["before", "after"] = "after",
        style: str | None = None,
    ) -> str:
        """Insert a paragraph beside a block. Inherit its style or select an existing style."""
        return edit(
            lambda: ops.insert_paragraph(
                session.index, anchor_id, text, side=side, style=style, styles=session.styles
            )
        )

    @tool
    def delete_paragraph(block_id: str, allow_media: bool = False) -> str:
        """Delete a paragraph. allow_media requires explicit user intent to remove its objects."""
        return edit(lambda: ops.delete_paragraph(session.index, block_id, allow_media=allow_media))

    @tool
    def set_paragraph_style(block_id: str, style: str) -> str:
        """Apply an existing Word paragraph style (Heading 1, Heading 2, etc.) to a paragraph.

        Preserve text, runs, numbering, indentation and protected objects. Use one heading
        level per hierarchy depth, including headings currently formatted as normal text.
        """
        return edit(lambda: ops.set_paragraph_style(session.index, block_id, style, session.styles))

    @tool
    def ask_clarification(question: str) -> str:
        """Ask the user a specific question when their intent cannot resolve an ambiguity.

        Call this alone, without other tools in the same turn.
        """
        if not question.strip():
            raise EditError("invalid_args", "A clarification question must not be empty.")
        return json.dumps({"question": question})

    @tool
    def finish(summary: str = "") -> str:
        """Submit edits for validation and review.

        Call it last, in the same turn as edits you are confident about, or alone. If any
        earlier call in that turn fails, finish is cancelled so you can fix the edit first.
        """
        return json.dumps({"summary": summary})

    return [
        view_document,
        find_text,
        replace_text,
        insert_text,
        delete_text,
        insert_paragraph,
        delete_paragraph,
        set_paragraph_style,
        ask_clarification,
        finish,
    ]


def invoke_tool(tools: dict[str, BaseTool], name: str, args: dict[str, Any]) -> str:
    """Turn model argument errors into recoverable tool feedback."""
    try:
        if name not in tools:
            raise EditError("unknown_tool", f"Unknown document tool {name!r}.")
        return str(tools[name].invoke(args))
    except EditError as exc:
        return json.dumps(exc.to_dict(), ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        return json.dumps({"error": "invalid_args", "message": str(exc)}, ensure_ascii=False)
