import json
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.types import Command

from docx_agent.agent.graph import AgentContext, AgentError, ReviewDecision, build_graph
from docx_agent.agent.session import DocumentSession
from docx_agent.document import ops
from docx_agent.document.integrity import check
from docx_agent.document.package import PackageError
from tests.fixtures.builders import build_xml_docx
from tests.graph.fake import ScriptedModel, calls


def test_happy_path(tmp_path: Path, rich_docx: bytes) -> None:
    session = DocumentSession(rich_docx)
    model = ScriptedModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7.5h"})),
            calls(("finish", {"summary": "Updated sleep."})),
        ]
    )
    graph = build_graph(model, RunnableLambda(lambda _: ReviewDecision(approved=True, reason="OK")))
    output = tmp_path / "out.docx"
    state = graph.invoke(
        {"instruction": "Change sleep from 8h to 7.5h"},
        {"configurable": {"thread_id": "happy"}},
        context=AgentContext(session=session, output=output),
    )
    assert state["status"] == "saved"
    assert check(rich_docx, output.read_bytes()).ok
    assert output.with_suffix(".report.json").exists()
    assert len(session.edits) == 1
    assert any(isinstance(m, ToolMessage) for m in model.seen[-1])
    assert model.seen[-1][: len(model.seen[0])] == model.seen[0]


def test_clarification_resume_preserves_edits(tmp_path: Path, rich_docx: bytes) -> None:
    model = ScriptedModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7h"})),
            calls(("replace_text", {"old": "12 March 2026", "new": "13 March 2026"})),
            calls(("ask_clarification", {"question": "Which date?"})),
            calls(
                ("replace_text", {"old": "12 March 2026", "new": "13 March 2026", "block_id": "p1"})
            ),
            calls(("finish", {})),
        ]
    )
    graph = build_graph(model, RunnableLambda(lambda _: ReviewDecision(approved=True, reason="OK")))
    ctx = AgentContext(session=DocumentSession(rich_docx), output=tmp_path / "out.docx")
    config = {"configurable": {"thread_id": "clarify"}}
    result = graph.invoke({"instruction": "Change the date and 8h to 7h"}, config, context=ctx)
    assert result["__interrupt__"][0].value["question"] == "Which date?"
    assert not ctx.output.exists()
    assert len(ctx.session.edits) == 1
    result = graph.invoke(Command(resume="The session date p1"), config, context=ctx)
    assert result["status"] == "saved"
    assert len(ctx.session.edits) == 2
    assert any(isinstance(m, HumanMessage) and "p1" in str(m.content) for m in result["messages"])


def test_review_rejection_resets_document(tmp_path: Path, rich_docx: bytes) -> None:
    decisions = iter(
        [
            ReviewDecision(approved=False, reason="Use 7.5h, not 7h"),
            ReviewDecision(approved=True, reason="Correct"),
        ]
    )
    model = ScriptedModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7h"})),
            calls(("finish", {})),
            calls(("replace_text", {"old": "8h", "new": "7.5h"})),
            calls(("finish", {})),
        ]
    )
    ctx = AgentContext(session=DocumentSession(rich_docx), output=tmp_path / "out.docx")
    graph = build_graph(model, RunnableLambda(lambda _: next(decisions)))
    state = graph.invoke(
        {"instruction": "8h to 7.5h"}, {"configurable": {"thread_id": "r"}}, context=ctx
    )
    assert state["status"] == "saved"
    assert state["retries"] == 1
    assert ctx.session.edits[0].after == "7.5h"


def test_validation_failure_resets_and_retries(
    tmp_path: Path, rich_docx: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = ops.replace_text
    count = 0

    def buggy_once(*args, **kwargs):
        nonlocal count
        count += 1
        result = real_replace(*args, **kwargs)
        return replace(result, after="false claim") if count == 1 else result

    monkeypatch.setattr(ops, "replace_text", buggy_once)
    model = ScriptedModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7h"})),
            calls(("finish", {})),
            calls(("replace_text", {"old": "8h", "new": "7h"})),
            calls(("finish", {})),
        ]
    )
    graph = build_graph(model, RunnableLambda(lambda _: ReviewDecision(approved=True, reason="OK")))
    ctx = AgentContext(session=DocumentSession(rich_docx), output=tmp_path / "out.docx")
    result = graph.invoke(
        {"instruction": "8h to 7h"}, {"configurable": {"thread_id": "validation"}}, context=ctx
    )
    assert result["status"] == "saved"
    assert result["retries"] == 1
    assert result["attempts"][0]["stage"] == "validate"
    assert len(ctx.session.edits) == 1
    assert "false claim" in str(model.seen[2])


@pytest.mark.parametrize("force", [False, True])
def test_review_exhaustion(tmp_path: Path, rich_docx: bytes, force: bool) -> None:
    model = ScriptedModel(responses=[calls(("finish", {})) for _ in range(3)])
    graph = build_graph(
        model, RunnableLambda(lambda _: ReviewDecision(approved=False, reason="Missing"))
    )
    ctx = AgentContext(
        session=DocumentSession(rich_docx), output=tmp_path / "out.docx", force=force
    )
    result = graph.invoke(
        {"instruction": "Change sleep"}, {"configurable": {"thread_id": "exhaust"}}, context=ctx
    )
    assert result["status"] == ("forced" if force else "failed")
    assert result["retries"] == 2
    assert ctx.output.exists() is force
    assert ctx.report_path.exists() is force
    if force:
        report = json.loads(ctx.report_path.read_text())
        assert report["status"] == "forced"
        assert report["review"]["approved"] is False


def test_dry_run_writes_nothing(tmp_path: Path, rich_docx: bytes) -> None:
    model = ScriptedModel(
        responses=[calls(("replace_text", {"old": "8h", "new": "7h"})), calls(("finish", {}))]
    )
    graph = build_graph(model, RunnableLambda(lambda _: ReviewDecision(approved=True, reason="OK")))
    ctx = AgentContext(
        session=DocumentSession(rich_docx), output=tmp_path / "out.docx", dry_run=True
    )
    result = graph.invoke(
        {"instruction": "8h to 7h"}, {"configurable": {"thread_id": "dry"}}, context=ctx
    )
    assert result["status"] == "dry_run"
    assert result["integrity"]["ok"]
    assert not ctx.output.exists()
    assert not ctx.report_path.exists()


def test_tool_feedback_and_sequential_batches(tmp_path: Path, rich_docx: bytes) -> None:
    model = ScriptedModel(
        responses=[
            calls(("view_document", {}), ("find_text", {"query": "8h"})),
            calls(("unknown", {}), ("replace_text", {}), ("view_document", {"from_id": "p999"})),
            calls(("finish", {}), ("replace_text", {"old": "8h", "new": "bad"})),
            calls(("ask_clarification", {"question": " "})),
            calls(
                ("replace_text", {"old": "8h", "new": "7h"}),
                ("replace_text", {"old": "7h", "new": "6h"}),
            ),
            calls(("finish", {})),
        ]
    )
    graph = build_graph(model, RunnableLambda(lambda _: ReviewDecision(approved=True, reason="OK")))
    ctx = AgentContext(session=DocumentSession(rich_docx), output=tmp_path / "out.docx")
    result = graph.invoke(
        {"instruction": "8h to 6h"}, {"configurable": {"thread_id": "tools"}}, context=ctx
    )
    assert result["status"] == "saved"
    assert len(ctx.session.edits) == 2
    assert ctx.session.edits[-1].after == "6h"
    content = " ".join(str(m.content) for m in result["messages"] if isinstance(m, ToolMessage))
    for expected in ("unknown_tool", "invalid_args", "unknown_block", "alone", "must not be empty"):
        assert expected in content


def test_no_tool_turns_are_bounded(tmp_path: Path, rich_docx: bytes) -> None:
    model = ScriptedModel(responses=[AIMessage(content="I changed it.")])
    graph = build_graph(model, RunnableLambda(lambda _: pytest.fail("Should not review")))
    ctx = AgentContext(
        session=DocumentSession(rich_docx), output=tmp_path / "out.docx", max_turns=1
    )
    result = graph.invoke(
        {"instruction": "Change sleep"}, {"configurable": {"thread_id": "limit"}}, context=ctx
    )
    assert result["status"] == "failed"
    assert "limit" in result["reason"]
    assert not ctx.output.exists()


def test_failed_report_write_does_not_publish_output(tmp_path: Path, rich_docx: bytes) -> None:
    model = ScriptedModel(responses=[calls(("finish", {}))])
    graph = build_graph(model, RunnableLambda(lambda _: ReviewDecision(approved=True, reason="OK")))
    ctx = AgentContext(
        session=DocumentSession(rich_docx),
        output=tmp_path / "out.docx",
        report=tmp_path / "missing" / "report.json",
    )
    with pytest.raises(FileNotFoundError):
        graph.invoke(
            {"instruction": "No change"},
            {"configurable": {"thread_id": "write-failure"}},
            context=ctx,
        )
    assert not ctx.output.exists()


def test_unreadable_output_is_validation_failure(
    tmp_path: Path, rich_docx: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = DocumentSession(rich_docx)
    monkeypatch.setattr(session, "validate", lambda: (_ for _ in ()).throw(PackageError("No body")))
    model = ScriptedModel(responses=[calls(("finish", {}))])
    graph = build_graph(model, RunnableLambda(lambda _: pytest.fail("Invalid output reviewed")))
    ctx = AgentContext(session=session, output=tmp_path / "out.docx", max_retries=0)
    result = graph.invoke(
        {"instruction": "Change sleep"}, {"configurable": {"thread_id": "unreadable"}}, context=ctx
    )
    assert result["status"] == "failed"
    assert "No body" in result["reason"]
    assert not ctx.output.exists()


@pytest.mark.parametrize(
    "metadata",
    [
        {"stop_reason": "refusal"},
        {"finish_reason": "SAFETY"},
    ],
)
def test_provider_refusal_stops_without_spending_more_calls(
    tmp_path: Path, rich_docx: bytes, metadata: dict[str, str]
) -> None:
    model = ScriptedModel(responses=[AIMessage(content="", response_metadata=metadata)])
    graph = build_graph(
        model, RunnableLambda(lambda _: pytest.fail("Refusal must not be reviewed"))
    )
    ctx = AgentContext(session=DocumentSession(rich_docx), output=tmp_path / "out.docx")
    with pytest.raises(AgentError, match="refused"):
        graph.invoke(
            {"instruction": "Change sleep"}, {"configurable": {"thread_id": "refusal"}}, context=ctx
        )
    assert len(model.seen) == 1
    assert not ctx.output.exists()


@pytest.mark.parametrize("options", [{"max_retries": 3}, {"max_turns": 0}, {"max_turns": 101}])
def test_invalid_context_limits(tmp_path: Path, rich_docx: bytes, options: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="retries"):
        AgentContext(session=DocumentSession(rich_docx), output=tmp_path / "out.docx", **options)


def test_reviewer_sees_actual_inserted_bullet_style(tmp_path: Path) -> None:
    original = build_xml_docx(
        '<w:p><w:pPr><w:pStyle w:val="ListBullet"/></w:pPr>'
        "<w:r><w:t>Existing bullet.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Last paragraph.</w:t></w:r></w:p>"
    )
    model = ScriptedModel(
        responses=[
            calls(("insert_paragraph", {"anchor_id": "p0", "text": "New bullet."})),
            calls(("finish", {})),
        ]
    )

    def judge(messages):
        payload = json.loads(messages[-1].content)
        assert '[n0] (List Bullet) "New bullet."' in payload["edited_blocks"]["n0"]
        return ReviewDecision(approved=True, reason="Verified inherited List Bullet style")

    graph = build_graph(model, RunnableLambda(judge))
    ctx = AgentContext(
        session=DocumentSession(original), output=tmp_path / "out.docx", max_retries=0
    )
    result = graph.invoke(
        {"instruction": "Add New bullet. after Existing bullet. as a bullet"},
        {"configurable": {"thread_id": "bullet-style"}},
        context=ctx,
    )
    assert result["status"] == "saved"
