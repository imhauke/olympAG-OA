import sys
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from docx_agent.agent.models import Settings
from samples import run
from samples.generate import SAMPLES, generate
from tests.graph.fake import ScriptedModel, calls


def test_default_runner_selects_all_samples_without_making_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = []
    monkeypatch.setattr(sys, "argv", ["samples.run"])
    monkeypatch.setattr(run, "generate", lambda **_: None)
    monkeypatch.setattr(run, "create_model", lambda _: object())
    monkeypatch.setattr(
        run,
        "Settings",
        lambda **kwargs: Settings(
            _env_file=None,
            GEMINI_API_KEY="",
            ANTHROPIC_API_KEY="",
            **kwargs,
        ),
    )

    def fake_run(name, model, settings, **kwargs):
        selected.append(name)
        return {"status": "saved"}

    monkeypatch.setattr(run, "run_sample", fake_run)
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0
    assert selected == list(SAMPLES)


def test_error_record_redacts_keys() -> None:
    settings = Settings(_env_file=None, GEMINI_API_KEY="fake-sensitive-key", ANTHROPIC_API_KEY="")
    errors = run._errors(ValueError("Request failed with fake-sensitive-key"), settings)
    assert errors == [{"type": "ValueError", "message": "Request failed with <redacted>"}]


def test_transient_review_retry_resumes_without_replaying_edits(tmp_path: Path) -> None:
    generate(tmp_path)

    class UnavailableError(RuntimeError):
        code = 503

    class TransientReviewModel(ScriptedModel):
        def with_structured_output(self, schema, **kwargs):
            attempts = 0

            def review(_):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise UnavailableError("Temporarily unavailable")
                return schema(approved=True, reason="Correct")

            return RunnableLambda(review)

    model = TransientReviewModel(
        responses=[
            calls(
                ("replace_text", {"old": "12 March 2026", "new": "19 March 2026"}),
                ("replace_text", {"old": "8h", "new": "7.5h"}),
                ("insert_paragraph", {"anchor_id": "p4", "text": "Agreed to keep a sleep diary."}),
            ),
            calls(("finish", {})),
        ]
    )
    settings = Settings(
        _env_file=None,
        provider="google_genai",
        model="offline",
        max_turns=5,
        max_retries=0,
        GEMINI_API_KEY="",
        ANTHROPIC_API_KEY="",
    )
    record = run.run_sample(
        "therapy_session_note", model, settings, root=tmp_path, retry_unavailable=True
    )
    assert record["status"] == "saved", record
    assert record["transport_retries"] == 1
    assert len(model.seen) == 2
