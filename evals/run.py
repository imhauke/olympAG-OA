"""Bounded manual evaluations. Default is a plan; --live explicitly permits API calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import ToolMessage

from docx_agent.agent.graph import AgentContext, AgentError, build_graph
from docx_agent.agent.models import Settings, create_model
from docx_agent.agent.session import DocumentSession
from docx_agent.document.integrity import check
from evals.case import Case, input_bytes, load_cases
from evals.verify import verify_output


class CallBudgetError(RuntimeError):
    """No model request is allowed once the shared suite budget is exhausted."""


@dataclass
class CallBudget:
    maximum: int
    used: int = 0

    def take(self) -> None:
        if self.used >= self.maximum:
            raise CallBudgetError(f"Global model-call budget exhausted ({self.maximum}).")
        self.used += 1


class _Trace(BaseCallbackHandler):
    raise_error = True

    def __init__(self, budget: CallBudget) -> None:
        self.budget = budget
        self.calls = 0
        self.responses: list[dict[str, Any]] = []

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self.budget.take()
        self.calls += 1
        print(f"  model call {self.budget.used}/{self.budget.maximum}", flush=True)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        for group in response.generations:
            for generation in group:
                message = generation.message
                self.responses.append(
                    {
                        "tool_calls": message.tool_calls,
                        "usage": message.usage_metadata,
                        "model": message.response_metadata.get("model_name"),
                    }
                )


def _causes(exc: BaseException) -> list[BaseException]:
    result = []
    while exc is not None:
        result.append(exc)
        exc = exc.__cause__  # type: ignore[assignment]
    return result


def evaluate_case(
    case: Case,
    model: BaseChatModel,
    root: Path,
    budget: CallBudget,
    *,
    max_turns: int = 5,
    retry_unavailable: bool = False,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    directory = root / case.id
    directory.mkdir(parents=True, exist_ok=False)
    original = input_bytes(case.source)
    (directory / "input.docx").write_bytes(original)
    session = DocumentSession(original)
    context = AgentContext(
        session=session, output=directory / "output.docx", max_turns=max_turns, max_retries=0
    )
    trace = _Trace(budget)
    config = {"configurable": {"thread_id": str(uuid4())}, "callbacks": [trace]}
    graph = build_graph(model)
    result: dict[str, Any] = {
        "id": case.id,
        "case": case.model_dump(),
        "input_sha256": hashlib.sha256(original).hexdigest(),
        "transport_retries": 0,
        "failures": [],
    }
    started = time.monotonic()
    try:
        try:
            state = graph.invoke({"instruction": case.instruction}, config, context=context)
        except AgentError as exc:
            if not retry_unavailable or not any(
                getattr(c, "code", None) == 503 for c in _causes(exc)
            ):
                raise
            result["transport_retries"] = 1
            state = graph.invoke(None, config, context=context)
        if state.get("__interrupt__"):
            result["question"] = state["__interrupt__"][0].value["question"]
            intact = check(original, session.package.to_bytes())
            result["integrity"] = intact.to_dict()
            if not case.should_ask_clarification:
                result["failures"].append("Unexpected clarification request.")
            if session.edits or intact.changes or not intact.ok or context.output.exists():
                result["failures"].append(
                    "Clarification must occur before edits or output publication."
                )
        elif case.should_ask_clarification:
            result["failures"].append("Expected clarification; the agent did not ask.")
        elif state.get("status") != "saved":
            result["failures"].append(
                f"Agent ended with {state.get('status')}: {state.get('reason')}"
            )
        else:
            output = context.output.read_bytes()
            result["failures"], result["integrity"] = verify_output(case, original, output)
            result["output_sha256"] = hashlib.sha256(output).hexdigest()
        result["outcome"] = "failed" if result["failures"] else "passed"
    except Exception as exc:
        message = " -> ".join(f"{type(c).__name__}: {c}" for c in _causes(exc))
        for secret in secrets:
            if secret:
                message = message.replace(secret, "<redacted>")
        result.update(outcome="error", error=message[:6000])
    state_values = graph.get_state(config).values
    result.update(
        calls=trace.calls,
        trace=trace.responses,
        elapsed_seconds=round(time.monotonic() - started, 3),
        tool_results=[
            {"name": m.name, "call_id": m.tool_call_id, "content": m.content}
            for m in state_values.get("messages", [])
            if isinstance(m, ToolMessage)
        ],
    )
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"{case.id}: {result['outcome']} ({trace.calls} calls)", flush=True)
    return result


def write_summary(
    root: Path,
    results: list[dict[str, Any]],
    *,
    model: str,
    budget: CallBudget,
    input_rate: float | None = None,
    output_rate: float | None = None,
) -> dict[str, Any]:
    usage = [
        event["usage"]
        for result in results
        for event in result.get("trace", [])
        if event.get("usage")
    ]
    input_tokens = sum(u["input_tokens"] for u in usage)
    output_tokens = sum(u["output_tokens"] for u in usage)
    passed = sum(r["outcome"] == "passed" for r in results)
    cost = (
        (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
        if input_rate is not None and output_rate is not None
        else None
    )
    summary = {
        "model": model,
        "cases": len(results),
        "passed": passed,
        "pass_rate": passed / len(results),
        "calls": budget.used,
        "max_calls": budget.maximum,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_usd": cost,
        "cost_note": "Uniform user-supplied token rates; failed-call usage unknown."
        if cost is not None
        else "Not priced: no token rates supplied; failed-call usage unknown.",
        "results": results,
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Synthetic live evaluation",
        "",
        f"Model: `{model}`. Passed: **{passed}/{len(results)}**.",
        f"Calls: {budget.used}/{budget.maximum}. "
        f"Input/output tokens: {input_tokens}/{output_tokens}.",
        f"Estimated USD: {cost if cost is not None else 'not priced'}. {summary['cost_note']}",
        "",
        "| Case | Outcome | Calls | Seconds | Failure |",
        "|---|---|---:|---:|---|",
    ]
    for result in results:
        reason = (
            str(result.get("error") or "; ".join(result.get("failures", [])))
            .replace("|", "\\|")
            .replace("\n", " ")
        )
        lines.append(
            f"| {result['id']} | {result['outcome']} | {result.get('calls', 0)} | "
            f"{result.get('elapsed_seconds', 0)} | {reason} |"
        )
    (root / "summary.md").write_text("\n".join(lines) + "\n")
    return summary


def main() -> None:
    cases = load_cases()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly authorize synthetic model calls."
    )
    parser.add_argument("--case", action="append", choices=[c.id for c in cases])
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-calls", type=int, default=36)
    parser.add_argument("--retry-unavailable", action="store_true")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    args = parser.parse_args()
    if not 1 <= args.max_calls <= 100:
        parser.error("Use 1..100 total model calls.")
    rates = (args.input_usd_per_million, args.output_usd_per_million)
    if (rates[0] is None) != (rates[1] is None) or any(
        rate is not None and rate < 0 for rate in rates
    ):
        parser.error("Provide both nonnegative token rates, or neither.")
    selected = [c for c in cases if args.case is None or c.id in args.case]
    print(
        f"{len(selected)} synthetic cases; global cap {args.max_calls} calls; "
        "reset/SDK retries disabled."
    )
    if not args.live:
        for case in selected:
            print(f"  {case.id}: {case.source}")
        print("Plan only. Add --live to call Gemini; no model was constructed.")
        return
    settings = Settings(provider="google_genai", model=args.model, max_retries=0, max_turns=5)
    model = create_model(settings)
    secrets = tuple(
        key.get_secret_value()
        for key in (settings.gemini_api_key, settings.anthropic_api_key)
        if key
    )
    root = Path("evals/reports") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    root.mkdir(parents=True)
    budget = CallBudget(args.max_calls)
    results = []
    for case in selected:
        if budget.used >= budget.maximum:
            results.append(
                {"id": case.id, "outcome": "not_run", "failures": ["Global call budget exhausted."]}
            )
            continue
        results.append(
            evaluate_case(
                case, model, root, budget, retry_unavailable=args.retry_unavailable, secrets=secrets
            )
        )
    summary = write_summary(
        root,
        results,
        model=settings.model_name,
        budget=budget,
        input_rate=rates[0],
        output_rate=rates[1],
    )
    print(f"Passed {summary['passed']}/{summary['cases']}. Report: {root / 'summary.md'}")
    raise SystemExit(0 if summary["passed"] == summary["cases"] else 1)


if __name__ == "__main__":
    main()
