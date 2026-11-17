"""Manual, capped live generation of sample outputs. Never invoked by CI."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel

from docx_agent.agent.graph import AgentContext, AgentError, build_graph
from docx_agent.agent.models import Settings, create_model
from docx_agent.agent.session import DocumentSession
from docx_agent.document.integrity import check
from samples.generate import ROOT, SAMPLES, generate


class _Trace(BaseCallbackHandler):
    def __init__(self) -> None:
        self.calls = 0
        self.responses: list[dict[str, Any]] = []

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self.calls += 1
        print(f"  model call {self.calls}", flush=True)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        for group in response.generations:
            for generation in group:
                message = generation.message
                self.responses.append(
                    {
                        "tools": [call["name"] for call in message.tool_calls],
                        "usage": message.usage_metadata,
                        "model": message.response_metadata.get("model_name"),
                        "finish_reason": message.response_metadata.get("finish_reason"),
                    }
                )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _errors(exc: BaseException, settings: Settings) -> list[dict[str, str]]:
    errors = []
    while exc is not None:
        message = str(exc)
        for secret in (settings.gemini_api_key, settings.anthropic_api_key):
            if secret and secret.get_secret_value():
                message = message.replace(secret.get_secret_value(), "<redacted>")
        errors.append({"type": type(exc).__name__, "message": message[:5000]})
        exc = exc.__cause__  # type: ignore[assignment]
    return errors


def _unavailable(exc: BaseException | None) -> bool:
    while exc is not None:
        if getattr(exc, "code", None) in (503, "503"):
            return True
        exc = exc.__cause__
    return False


def _diff(report: dict[str, Any], model: str) -> str:
    lines = [
        "# Live edit diff",
        "",
        "SYNTHETIC - all content is fictitious.",
        "",
        f"Model: `{model}`. Integrity passed; review approved.",
        "",
    ]
    for change in report["changes"]:
        lines.extend(
            [
                f"## {change['kind']}: {change['before_id'] or '-'} -> {change['after_id'] or '-'}",
                "",
                "```diff",
            ]
        )
        if change["kind"] != "inserted":
            lines.extend("- " + line for line in change["before_text"].splitlines() or [""])
        if change["kind"] != "deleted":
            lines.extend("+ " + line for line in change["after_text"].splitlines() or [""])
        lines.extend(["```", ""])
    return "\n".join(lines)


def run_sample(
    name: str,
    model: BaseChatModel,
    settings: Settings,
    *,
    root: Path = ROOT,
    run_kind: Literal["live", "scripted"] = "scripted",
    retry_unavailable: bool = False,
) -> dict[str, Any]:
    directory = root / name
    original = (directory / "input.docx").read_bytes()
    instruction = (directory / "instruction.txt").read_text().strip()
    trace = _Trace()
    record: dict[str, Any] = {
        "run_kind": run_kind,
        "started_at": datetime.now(UTC).isoformat(),
        "provider": settings.provider,
        "model": settings.model_name,
        "effort": settings.effort,
        "max_editor_calls": settings.max_turns,
        "max_review_calls": settings.max_retries + 1,
        "max_transport_retries": int(retry_unavailable),
        "transport_retries": 0,
        "input_sha256": _sha(original),
        "instruction_sha256": _sha(instruction.encode()),
    }
    cap = settings.max_turns + settings.max_retries + 1 + int(retry_unavailable)
    print(
        f"{name}: max {cap} calls ({settings.max_turns} editor + "
        f"{settings.max_retries + 1} review + {int(retry_unavailable)} explicit 503 retry)",
        flush=True,
    )
    try:
        graph = build_graph(model)
        config = {"configurable": {"thread_id": str(uuid4())}, "callbacks": [trace]}
        context = AgentContext(
            session=DocumentSession(original),
            output=directory / "output.docx",
            report=directory / "report.json",
            max_turns=settings.max_turns,
            max_retries=settings.max_retries,
        )
        try:
            state = graph.invoke({"instruction": instruction}, config, context=context)
        except AgentError as exc:
            if not retry_unavailable or not _unavailable(exc):
                raise
            record.update(transport_retries=1, transport_errors=_errors(exc, settings))
            print(
                "  one explicit 503 retry at the failed node; completed edits retained", flush=True
            )
            state = graph.invoke(None, config, context=context)
        record["status"] = state.get("status", "clarification_required")
        if state.get("__interrupt__"):
            record["question"] = state["__interrupt__"][0].value
        if record["status"] == "saved":
            output = (directory / "output.docx").read_bytes()
            independent = check(original, output)
            if not independent.ok:
                raise ValueError("Independent verification failed: " + str(independent.violations))
            record["output_sha256"] = _sha(output)
            record["editor_calls"] = state["turns"]
            record["integrity_ok"] = True
            (directory / "diff.md").write_text(_diff(independent.to_dict(), settings.model_name))
        else:
            record["reason"] = state.get("reason", "")
    except Exception as exc:
        record.update(status="error", errors=_errors(exc, settings))
    record.update(
        api_calls=trace.calls, responses=trace.responses, finished_at=datetime.now(UTC).isoformat()
    )
    (directory / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"{name}: {record['status']} ({trace.calls} calls)", flush=True)
    if record.get("errors"):
        print(json.dumps(record["errors"]), flush=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", choices=list(SAMPLES))
    parser.add_argument("--model", help="Gemini model ID; default first GEMINI_MODELS entry.")
    parser.add_argument(
        "--retry-unavailable",
        action="store_true",
        help="Allow one extra call after HTTP 503, resuming the failed node.",
    )
    args = parser.parse_args()
    generate(check=True)
    settings = Settings(provider="google_genai", model=args.model, max_turns=5, max_retries=0)
    model = create_model(settings)
    records = [
        run_sample(name, model, settings, run_kind="live", retry_unavailable=args.retry_unavailable)
        for name in args.names or SAMPLES
    ]
    raise SystemExit(0 if all(r["status"] == "saved" for r in records) else 1)


if __name__ == "__main__":
    main()
