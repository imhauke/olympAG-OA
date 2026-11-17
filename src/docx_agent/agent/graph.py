"""Explicit, bounded LangGraph edit → validate → review → save workflow."""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from docx_agent.agent import prompts
from docx_agent.agent.artifacts import write_artifacts
from docx_agent.agent.session import DocumentSession
from docx_agent.agent.tools import invoke_tool, make_tools
from docx_agent.document.integrity import IntegrityReport
from docx_agent.document.package import PackageError
from docx_agent.document.view import render


class AgentError(RuntimeError):
    """Model or workflow failure; no document has been saved."""


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approved: bool
    reason: str = Field(min_length=1)


@dataclass(frozen=True)
class AgentContext:
    session: DocumentSession
    output: Path
    report: Path | None = None
    dry_run: bool = False
    force: bool = False
    max_retries: int = 2
    max_turns: int = 30

    def __post_init__(self) -> None:
        if not 0 <= self.max_retries <= 2 or not 1 <= self.max_turns <= 100:
            raise ValueError("Use 0..2 retries and 1..100 agent turns.")
        if self.output.resolve() == self.report_path.resolve() or (
            self.output.exists()
            and self.report_path.exists()
            and self.output.samefile(self.report_path)
        ):
            raise ValueError("Document and report paths must differ.")

    @property
    def report_path(self) -> Path:
        return self.report or self.output.with_suffix(".report.json")


class AgentState(TypedDict, total=False):
    instruction: str
    messages: Annotated[list[BaseMessage], operator.add]
    retries: int
    turns: int
    next: str
    status: str
    reason: str
    question: str
    summary: str
    integrity: dict[str, Any]
    review: dict[str, Any]
    attempts: Annotated[list[dict[str, Any]], operator.add]


def _view(session: DocumentSession) -> str:
    return render(session.index, session.styles)


def _retry_or_stop(state: AgentState, ctx: AgentContext, reason: str) -> AgentState:
    retry = state.get("retries", 0) < ctx.max_retries
    return {
        "next": "retry" if retry else "save" if ctx.force else END,
        "status": "retrying" if retry else "failed",
        "reason": reason,
    }


class _Nodes:
    def __init__(self, model: BaseChatModel, reviewer: Runnable[Any, Any] | None) -> None:
        self.model = model
        self.judge = (
            reviewer
            if reviewer is not None
            else model.with_structured_output(ReviewDecision, method="json_schema")
        )

    def load(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        session = runtime.context.session
        session.reset()
        return {
            "messages": [
                SystemMessage(prompts.SYSTEM),
                HumanMessage(state["instruction"]),
                HumanMessage("Original document:\n" + _view(session)),
            ],
            "retries": 0,
            "turns": 0,
            "status": "editing",
            "attempts": [],
        }

    def agent(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        if state["turns"] >= runtime.context.max_turns:
            return {"next": END, "status": "failed", "reason": "Agent turn limit reached."}
        try:
            response = self.model.bind_tools(make_tools(runtime.context.session)).invoke(
                state["messages"]
            )
        except Exception as exc:
            raise AgentError("Editor model call failed; no output saved.") from exc
        if not isinstance(response, AIMessage):
            raise AgentError("Editor returned a non-assistant message.")
        if response.response_metadata.get("stop_reason") == "refusal" or (
            response.response_metadata.get("finish_reason") in {"SAFETY", "PROHIBITED_CONTENT"}
        ):
            raise AgentError("Editor model refused the request; no output saved.")
        messages: list[BaseMessage] = [response]
        if not response.tool_calls:
            messages.append(HumanMessage("Use the document tools, ask_clarification, or finish."))
        return {
            "messages": messages,
            "turns": state["turns"] + 1,
            "next": "tools" if response.tool_calls else "agent",
        }

    def tools(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        response = state["messages"][-1]
        assert isinstance(response, AIMessage)
        bound = {t.name: t for t in make_tools(runtime.context.session)}
        messages: list[BaseMessage] = []
        updates: AgentState = {"next": "agent"}
        control = any(c["name"] in {"finish", "ask_clarification"} for c in response.tool_calls)
        for call in response.tool_calls:
            if control and len(response.tool_calls) != 1:
                content = json.dumps(
                    {"error": "invalid_args", "message": "Call finish or ask_clarification alone."}
                )
            else:
                content = invoke_tool(bound, call["name"], call["args"])
                if call["name"] in {"finish", "ask_clarification"}:
                    data = json.loads(content)
                    if "error" not in data:
                        if call["name"] == "finish":
                            updates.update({"next": "validate", "summary": data["summary"]})
                        else:
                            updates.update({"next": "clarify", "question": data["question"]})
            messages.append(
                ToolMessage(content=content, tool_call_id=call["id"], name=call["name"])
            )
        updates["messages"] = messages
        return updates

    def clarify(self, state: AgentState) -> AgentState:
        answer = interrupt({"question": state["question"]})
        return {"messages": [HumanMessage("Clarification answer: " + str(answer))]}

    def validate(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        try:
            report = runtime.context.session.validate().to_dict()
        except PackageError as exc:
            report = IntegrityReport(violations=[f"Output is unreadable: {exc}"]).to_dict()
        if report["ok"]:
            return {"integrity": report, "next": "review"}
        reason = "Validation failed: " + json.dumps(report, ensure_ascii=False)
        return {
            **_retry_or_stop(state, runtime.context, reason),
            "integrity": report,
            "attempts": [{"stage": "validate", "report": report}],
        }

    def review(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        session = runtime.context.session
        # Include all user answers, but not earlier rejected attempts or model summaries.
        answers = [
            str(m.content)
            for m in state["messages"]
            if isinstance(m, HumanMessage) and str(m.content).startswith("Clarification answer:")
        ]
        original = DocumentSession(session.original)
        payload = {
            "instruction": state["instruction"],
            "clarifications": answers,
            "original": _view(original),
            "changes": state["integrity"]["changes"],
            "operations": [e.to_dict() for e in session.edits],
        }
        try:
            raw = self.judge.invoke(
                [
                    SystemMessage(prompts.REVIEW),
                    HumanMessage(json.dumps(payload, ensure_ascii=False)),
                ]
            )
            decision = ReviewDecision.model_validate(raw)
        except Exception as exc:
            raise AgentError(
                "Review model call or structured response failed; no output saved."
            ) from exc
        result: AgentState = {
            "review": decision.model_dump(),
            "attempts": [{"stage": "review", **decision.model_dump()}],
        }
        if decision.approved:
            result.update({"next": "save", "status": "approved", "reason": decision.reason})
        else:
            result.update(
                _retry_or_stop(state, runtime.context, "Review rejected: " + decision.reason)
            )
        return result

    def retry(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        runtime.context.session.reset()
        return {
            "retries": state["retries"] + 1,
            "status": "editing",
            "review": {},
            "messages": [
                HumanMessage(
                    state["reason"] + "\nDocument reset to original. "
                    "Reapply every requested edit. Fresh view:\n" + _view(runtime.context.session)
                )
            ],
        }

    def save(self, state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
        ctx = runtime.context
        forced = state["status"] == "failed"
        status = "forced" if forced else "dry_run" if ctx.dry_run else "saved"
        report = {
            "status": status,
            "instruction": state["instruction"],
            "summary": state.get("summary", ""),
            "retries": state["retries"],
            "reason": state.get("reason", ""),
            "integrity": state["integrity"],
            "review": state.get("review", {}),
            "attempts": state.get("attempts", []),
            "operations": [e.to_dict() for e in ctx.session.edits],
        }
        if not ctx.dry_run:
            write_artifacts(
                ctx.output,
                ctx.session.package.to_bytes(),
                ctx.report_path,
                (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        return {"status": status}


def build_graph(
    model: BaseChatModel,
    reviewer: Runnable[Any, Any] | None = None,
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    """Create the workflow with injectable editor and structured reviewer models."""
    nodes = _Nodes(model, reviewer)
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("load", nodes.load)
    graph.add_node("agent", nodes.agent)
    graph.add_node("tools", nodes.tools)
    graph.add_node("clarify", nodes.clarify)
    graph.add_node("validate", nodes.validate)
    graph.add_node("review", nodes.review)
    graph.add_node("retry", nodes.retry)
    graph.add_node("save", nodes.save)
    graph.add_edge(START, "load")
    graph.add_edge("load", "agent")
    for name in ("agent", "tools", "validate", "review"):
        graph.add_conditional_edges(name, lambda state: state["next"])
    graph.add_edge("clarify", "agent")
    graph.add_edge("retry", "agent")
    graph.add_edge("save", END)
    return graph.compile(checkpointer=InMemorySaver()).with_config(recursion_limit=420)
