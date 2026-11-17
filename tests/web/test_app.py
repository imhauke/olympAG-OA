"""Local web UI: API flow end to end with the real graph and a scripted model (no LLM calls)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from docx_agent.agent.models import Settings
from docx_agent.document.integrity import check
from docx_agent.web.app import create_app
from docx_agent.web.diff import location, segments
from docx_agent.web.history import History
from tests.graph.fake import ScriptedModel, calls

UI = {"X-Docx-Agent": "1"}


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "provider": "google_genai",
        "max_turns": 6,
        "max_retries": 0,
        "GEMINI_API_KEY": "test-key",
        "ANTHROPIC_API_KEY": "test-key",
    }
    return Settings(**(values | overrides))


class Models:
    """Hands out scripted models and records the settings each job was started with."""

    def __init__(self, *scripts: ScriptedModel) -> None:
        self.scripts = list(scripts)
        self.settings: list[Settings] = []

    def __call__(self, configured: Settings) -> ScriptedModel:
        self.settings.append(configured)
        return self.scripts.pop(0)


def client(tmp_path: Path, models: Models, **overrides: Any) -> TestClient:
    app = create_app(
        settings_factory=lambda: settings(**overrides),
        model_factory=models,
        history=History(tmp_path / "history.json"),
        allowed_hosts=("testserver",),
    )
    return TestClient(app)


def start(http: TestClient, data: bytes, instruction: str = "8h to 7h", **form: str) -> Any:
    return http.post(
        "/api/jobs",
        headers=UI,
        files={"file": ("Session Note.docx", data)},
        data={"instruction": instruction, "provider": "google_genai", **form},
    )


FINAL = ("done", "failed", "error")


def wait(http: TestClient, job_id: str, until: tuple[str, ...] = FINAL) -> Any:
    for _ in range(200):
        snapshot = http.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in until:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"job stuck in {snapshot['status']}")


def edit_model(*extra: AIMessage) -> ScriptedModel:
    return ScriptedModel(
        responses=[
            calls(("replace_text", {"old": "8h", "new": "7h"}), ("finish", {"summary": "Done"})),
            *extra,
        ]
    )


# ---------------------------------------------------------------------------- happy path


def test_upload_edit_review_download(tmp_path: Path, rich_docx: bytes) -> None:
    http = client(tmp_path, Models(edit_model()))
    assert http.get("/").status_code == 200
    config = http.get("/api/config").json()
    assert [p["id"] for p in config["providers"]] == ["google_genai", "anthropic"]
    assert config["providers"][1]["model"] == "claude-sonnet-5-5"

    response = start(http, rich_docx)
    assert response.status_code == 201, response.text
    job = wait(http, response.json()["id"])
    assert job["status"] == "done"
    assert job["step"] == len(job["steps"])
    result = job["result"]
    assert result["integrity"] == "verified"
    assert result["edits"] == 1
    (change,) = result["changes"]
    assert change["location"] == "Table 1 · row 2, cell 2"
    assert change["segments"] == [
        {"kind": "delete", "text": "8h"},
        {"kind": "insert", "text": "7h"},
    ]

    download = http.get(f"/api/jobs/{job['id']}/document")
    assert download.status_code == 200
    assert "Session%20Note.edited.docx" in download.headers["content-disposition"]
    assert check(rich_docx, download.content, touched={"t0.r1.c1.p0"}).ok
    report = http.get(f"/api/jobs/{job['id']}/report").json()
    assert report["status"] == "saved"

    (entry,) = http.get("/api/history").json()
    assert entry["filename"] == "Session Note.docx"
    assert entry["status"] == "done"
    assert entry["changes"] == 1
    stored = (tmp_path / "history.json").read_text()
    assert "PK" not in stored  # metadata only: no document bytes
    assert set(entry) == {
        "id",
        "filename",
        "instruction",
        "provider",
        "model",
        "status",
        "changes",
        "seconds",
        "at",
    }


def test_clarification_pauses_and_resumes(tmp_path: Path, rich_docx: bytes) -> None:
    model = ScriptedModel(
        responses=[
            calls(("ask_clarification", {"question": "Which target?"})),
            calls(("replace_text", {"old": "8h", "new": "7h"}), ("finish", {})),
        ]
    )
    http = client(tmp_path, Models(model))
    job = wait(http, start(http, rich_docx).json()["id"], until=("question",))
    assert job["question"] == "Which target?"
    url = f"/api/jobs/{job['id']}/answer"
    assert http.post(url, json={"answer": ""}, headers=UI).status_code == 422
    resumed = http.post(url, json={"answer": "Sleep"}, headers=UI)
    assert resumed.status_code == 200
    assert wait(http, job["id"])["status"] == "done"
    again = http.post(url, json={"answer": "x"}, headers=UI)
    assert again.status_code == 409


def test_rejected_review_saves_nothing(tmp_path: Path, rich_docx: bytes) -> None:
    model = edit_model()
    model.decisions = [{"approved": False, "reason": "The wrong cell was edited."}]
    http = client(tmp_path, Models(model))
    job = wait(http, start(http, rich_docx).json()["id"])
    assert job["status"] == "failed"
    assert job["message"] == "The wrong cell was edited."
    assert job["downloadable"] is False
    assert http.get(f"/api/jobs/{job['id']}/document").status_code == 409
    assert http.get("/api/history").json()[0]["status"] == "failed"


def test_provider_error_is_reported_with_its_cause(tmp_path: Path, rich_docx: bytes) -> None:
    http = client(tmp_path, Models(ScriptedModel(responses=[])))
    job = wait(http, start(http, rich_docx).json()["id"])
    assert job["status"] == "error"
    assert "Editor model call failed" in job["message"]
    assert "Cause: AssertionError" in job["message"]


# ---------------------------------------------------------------------------- input validation


@pytest.mark.parametrize(
    "case",
    [
        ("notes.txt", b"hello", "x", "google_genai", 415),
        ("broken.docx", b"not a zip", "x", "google_genai", 422),
        ("ok.docx", None, "   ", "google_genai", 422),
        ("ok.docx", None, "x" * 4001, "google_genai", 422),
        ("ok.docx", None, "x", "openai", 422),
    ],
)
def test_upload_validation(
    tmp_path: Path, rich_docx: bytes, case: tuple[str, bytes | None, str, str, int]
) -> None:
    name, data, instruction, provider, status = case
    http = client(tmp_path, Models())
    response = http.post(
        "/api/jobs",
        headers=UI,
        files={"file": (name, data if data is not None else rich_docx)},
        data={"instruction": instruction, "provider": provider},
    )
    assert response.status_code == status, response.text


def test_provider_without_key_is_not_offered_or_accepted(tmp_path: Path, rich_docx: bytes) -> None:
    http = client(tmp_path, Models(), ANTHROPIC_API_KEY="")
    assert [p["id"] for p in http.get("/api/config").json()["providers"]] == ["google_genai"]
    response = start(http, rich_docx, provider="anthropic")
    assert response.status_code == 422
    assert "Claude has no API key" in response.json()["detail"]


def test_configured_gemini_model_does_not_leak_to_claude(tmp_path: Path, rich_docx: bytes) -> None:
    models = Models(edit_model())
    http = client(tmp_path, models, model="gemini-custom")
    config = http.get("/api/config").json()
    assert {p["id"]: p["model"] for p in config["providers"]} == {
        "google_genai": "gemini-custom",
        "anthropic": "claude-sonnet-5-5",
    }
    wait(http, start(http, rich_docx, provider="anthropic").json()["id"])
    assert models.settings[0].model_name == "claude-sonnet-5-5"


# ---------------------------------------------------------------------------- local-only hardening


def test_mutations_need_the_ui_header_and_a_local_host(tmp_path: Path, rich_docx: bytes) -> None:
    http = client(tmp_path, Models())
    response = http.post(
        "/api/jobs",
        files={"file": ("a.docx", rich_docx)},
        data={"instruction": "x", "provider": "google_genai"},
    )
    assert response.status_code == 403
    assert http.delete("/api/history").status_code == 403
    assert http.get("/api/config", headers={"Host": "evil.example"}).status_code == 400


def test_unknown_job_and_history_clear(tmp_path: Path, rich_docx: bytes) -> None:
    http = client(tmp_path, Models(edit_model()))
    assert http.get("/api/jobs/nope").status_code == 404
    wait(http, start(http, rich_docx).json()["id"])
    assert len(http.get("/api/history").json()) == 1
    assert http.delete("/api/history", headers=UI).status_code == 204
    assert http.get("/api/history").json() == []


def test_corrupt_history_file_is_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{not json")
    history = History(path, limit=2)
    assert history.entries() == []
    for i in range(3):
        history.add({"id": str(i)})
    assert [e["id"] for e in history.entries()] == ["2", "1"]
    path.write_text(json.dumps({"not": "a list"}))
    assert history.entries() == []


# ---------------------------------------------------------------------------- presentation helpers


def test_editor_and_docs_pages_link_to_each_other(tmp_path: Path) -> None:
    http = client(tmp_path, Models())
    editor, docs = http.get("/"), http.get("/docs")
    assert editor.status_code == docs.status_code == 200
    assert 'href="/docs"' in editor.text
    assert "How it was built" in docs.text
    assert 'href="/"' in docs.text
    assert http.get("/static/docs.html").status_code == 200
    assert 'data-lang="de"' in docs.text  # German version of the docs page
    assert "Wie es entstand" in docs.text
    i18n = http.get("/static/i18n.js")
    assert i18n.status_code == 200
    assert "Dokument bearbeiten" in i18n.text  # editor strings in both languages
    assert 'data-set-lang="de"' in editor.text
    assert http.get("/openapi.json").status_code == 200  # docs route did not replace the API


def test_segments_and_locations() -> None:
    assert segments("weekly session", "biweekly session") == [
        {"kind": "delete", "text": "weekly"},
        {"kind": "insert", "text": "biweekly"},
        {"kind": "equal", "text": " session"},
    ]
    assert segments("", "New paragraph.") == [{"kind": "insert", "text": "New paragraph."}]
    image = "a" + chr(0xFFFC)
    assert segments(image, image) == [{"kind": "equal", "text": "a[object]"}]
    assert location("p0") == "Paragraph 1"
    assert location("n3") == "Block"
    assert location(None) == "Document"
    assert location("t1.r0.c2.p0") == "Table 2 · row 1, cell 3"
    assert location("t0.r0.c0.t0.r1.c1.p0") == "Table 1 · row 1, cell 1 (nested)"
    assert location("t0.x") == "Table"


def test_rewritten_text_is_shown_whole_not_interleaved() -> None:
    assert segments("Practised breathing exercises.", "Reduced caffeine intake.") == [
        {"kind": "delete", "text": "Practised breathing exercises."},
        {"kind": "insert", "text": "Reduced caffeine intake."},
    ]
