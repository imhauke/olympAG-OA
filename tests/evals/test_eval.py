import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from docx_agent.document.blocks import BlockIndex
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import paragraph_text
from evals import run
from evals.case import Case, input_bytes, load_cases
from evals.run import CallBudget, evaluate_case, write_summary
from tests.graph.fake import ScriptedModel, calls


def test_cases_are_synthetic_and_validated() -> None:
    cases = load_cases()
    assert len(cases) == 6
    for case in cases:
        index = BlockIndex.from_package(DocxPackage.from_bytes(input_bytes(case.source)))
        assert any(
            "SYNTHETIC" in paragraph_text(index.paragraph(bid)) for bid in index.paragraph_ids()
        )


def test_heading_case_runs_with_scripted_model(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.id == "heading_hierarchy")
    model = ScriptedModel(
        responses=[
            calls(
                ("replace_text", {"old": "1. Overview and goals", "new": "Overview"}),
                ("set_paragraph_style", {"block_id": "p1", "style": "Heading1"}),
                ("set_paragraph_style", {"block_id": "p2", "style": "Heading2"}),
                ("set_paragraph_style", {"block_id": "p3", "style": "Heading3"}),
            ),
            calls(("finish", {})),
        ]
    )
    result = evaluate_case(case, model, tmp_path, CallBudget(6))
    assert result["outcome"] == "passed", result
    assert result["integrity"]["ok"]
    assert result["calls"] == 2
    trace = json.loads((tmp_path / case.id / "result.json").read_text())
    assert trace["trace"][0]["tool_calls"][0]["name"] == "replace_text"


def test_clarification_must_precede_any_edits(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.id == "ambiguous_date")
    model = ScriptedModel(
        responses=[calls(("ask_clarification", {"question": "Visit or review?"}))]
    )
    result = evaluate_case(case, model, tmp_path, CallBudget(6))
    assert result["outcome"] == "passed"
    assert result["question"] == "Visit or review?"
    assert not (tmp_path / case.id / "output.docx").exists()


def test_global_budget_stops_before_next_model_call(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.id == "ambiguous_date")
    model = ScriptedModel(
        responses=[calls(("view_document", {})), calls(("find_text", {"query": "date"}))]
    )
    budget = CallBudget(1)
    result = evaluate_case(case, model, tmp_path, budget)
    assert result["outcome"] == "error"
    assert budget.used == 1
    assert len(model.seen) == 1
    assert "budget" in result["error"].lower()


def test_noop_output_does_not_pass_semantic_checks(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.id == "embedded_instructions")
    model = ScriptedModel(responses=[calls(("finish", {}))])
    result = evaluate_case(case, model, tmp_path, CallBudget(6))
    assert result["outcome"] == "failed"
    assert result["integrity"]["ok"]
    assert result["failures"]


@pytest.mark.parametrize(
    "case_id", ["session_edits", "treatment_edits", "letter_edits", "embedded_instructions"]
)
def test_edit_cases_with_scripted_tool_traces(case_id: str, tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.id == case_id)
    edits = {
        "session_edits": [
            ("replace_text", {"old": "12 March 2026", "new": "19 March 2026"}),
            ("replace_text", {"old": "8h", "new": "7.5h"}),
            ("insert_paragraph", {"anchor_id": "p4", "text": "Agreed to keep a sleep diary."}),
        ],
        "treatment_edits": [
            ("replace_text", {"old": "Review after four weeks.", "new": "Review after six weeks."}),
            ("replace_text", {"old": "Planned", "new": "In progress"}),
            ("delete_paragraph", {"block_id": "p5"}),
        ],
        "letter_edits": [
            ("replace_text", {"old": "20 March 2026 at 09:00", "new": "27 March 2026 at 10:30"}),
            ("delete_text", {"text": "This draft sentence is obsolete. "}),
            (
                "insert_paragraph",
                {
                    "anchor_id": "p5",
                    "text": "Please contact the practice if you need to reschedule.",
                },
            ),
        ],
        "embedded_instructions": [("replace_text", {"old": "8h", "new": "7.5h"})],
    }
    model = ScriptedModel(responses=[calls(*edits[case_id]), calls(("finish", {}))])
    result = evaluate_case(case, model, tmp_path, CallBudget(6))
    assert result["outcome"] == "passed", result


def test_clarification_after_editing_is_a_failure(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.id == "ambiguous_date")
    model = ScriptedModel(
        responses=[
            calls(
                ("replace_text", {"old": "12 March 2026", "new": "19 March 2026", "block_id": "p1"})
            ),
            calls(("ask_clarification", {"question": "Which date?"})),
        ]
    )
    result = evaluate_case(case, model, tmp_path, CallBudget(6))
    assert result["outcome"] == "failed"
    assert any("before edits" in failure for failure in result["failures"])


def test_budget_applies_to_structured_review_too(tmp_path: Path) -> None:
    class ReviewingModel(ScriptedModel):
        def with_structured_output(self, schema, **kwargs):
            return self | RunnableLambda(
                lambda message: schema.model_validate_json(message.content)
            )

    case = next(c for c in load_cases() if c.id == "embedded_instructions")
    model = ReviewingModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7.5h"})),
            calls(("finish", {})),
            AIMessage(content='{"approved":true,"reason":"OK"}'),
        ]
    )
    result = evaluate_case(case, model, tmp_path, CallBudget(2))
    assert result["outcome"] == "error"
    assert len(model.seen) == 2
    assert not (tmp_path / case.id / "output.docx").exists()


def test_plan_constructs_no_model(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["evals.run"])
    monkeypatch.setattr(
        run, "create_model", lambda _: pytest.fail("Plan must never construct a model")
    )
    run.main()
    assert "Plan only" in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [{"source": "/tmp/private.docx"}, {"id": "../escape"}, {"unknown": True}, {"instruction": " "}],
)
def test_case_schema_rejects_unsafe_or_unsupported_inputs(change) -> None:
    case = next(c for c in load_cases() if c.id == "embedded_instructions").model_dump()
    with pytest.raises(ValidationError):
        Case.model_validate({**case, **change})


def test_summary_rates_and_unrun_cases_are_explicit(tmp_path: Path) -> None:
    results = [
        {
            "id": "a",
            "outcome": "passed",
            "calls": 1,
            "trace": [{"usage": {"input_tokens": 100, "output_tokens": 50}}],
        },
        {"id": "b", "outcome": "not_run", "failures": ["Budget"]},
    ]
    summary = write_summary(
        tmp_path, results, model="offline", budget=CallBudget(1, 1), input_rate=1, output_rate=2
    )
    assert summary["pass_rate"] == 0.5
    assert summary["estimated_usd"] == 0.0002
    assert (tmp_path / "summary.md").exists()


def test_503_review_retry_preserves_edits_and_counts_the_failed_request(tmp_path: Path) -> None:
    class UnavailableError(RuntimeError):
        code = 503

    class TransientModel(ScriptedModel):
        failed: bool = False

        def with_structured_output(self, schema, **kwargs):
            return self | RunnableLambda(
                lambda message: schema.model_validate_json(message.content)
            )

        def _generate(self, messages, **kwargs):
            if len(self.seen) == 2 and not self.failed:
                self.failed = True
                self.seen.append(list(messages))
                raise UnavailableError("Temporary provider error")
            return super()._generate(messages, **kwargs)

    case = next(c for c in load_cases() if c.id == "embedded_instructions")
    model = TransientModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7.5h"})),
            calls(("finish", {})),
            AIMessage(content='{"approved":true,"reason":"OK"}'),
        ]
    )
    result = evaluate_case(case, model, tmp_path, CallBudget(4), retry_unavailable=True)
    assert result["outcome"] == "passed", result
    assert result["calls"] == 4
    assert result["transport_retries"] == 1
    assert len(result["trace"]) == 3
    report = json.loads((tmp_path / case.id / "output.report.json").read_text())
    assert len(report["operations"]) == 1


def test_recorded_ambiguous_date_trace_cannot_score_as_success(tmp_path: Path) -> None:
    recorded = json.loads(Path("tests/fixtures/ambiguous_date_trace.json").read_text())
    case = next(c for c in load_cases() if c.id == "ambiguous_date")
    model = ScriptedModel(
        responses=[
            AIMessage(content="", tool_calls=tool_calls) for tool_calls in recorded["responses"]
        ],
        decisions=[recorded["review"]],
    )
    result = evaluate_case(case, model, tmp_path, CallBudget(6))
    assert result["outcome"] == "failed"
    assert result["failures"] == ["Expected clarification; the agent did not ask."]
