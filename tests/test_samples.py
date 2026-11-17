import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from docx import Document

from docx_agent.agent.models import Settings
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.integrity import check
from docx_agent.document.package import DocxPackage
from docx_agent.document.runs import paragraph_text
from samples.generate import ROOT, SAMPLES, ZIP_DATE, generate
from samples.run import run_sample
from tests.graph.fake import ScriptedModel, calls


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_inputs_are_reproducible(name: str) -> None:
    sample = SAMPLES[name]
    expected = (ROOT / name / "input.docx").read_bytes()
    assert sample.build() == expected
    assert (ROOT / name / "instruction.txt").read_text() == sample.instruction + "\n"
    assert sample.build() == expected
    doc = Document(io.BytesIO(expected))
    assert any("SYNTHETIC" in p.text for p in doc.paragraphs)
    with zipfile.ZipFile(io.BytesIO(expected)) as z:
        assert all(entry.date_time == ZIP_DATE for entry in z.infolist())


def test_generate_check_detects_stale_input(tmp_path: Path) -> None:
    generate(tmp_path)
    generate(tmp_path, check=True)
    (tmp_path / "practice_letter" / "instruction.txt").write_text("wrong instruction")
    with pytest.raises(ValueError, match=r"instruction\.txt"):
        generate(tmp_path, check=True)


@pytest.mark.parametrize("name", SAMPLES)
def test_live_sample_outputs(name: str) -> None:
    folder = ROOT / name
    before, after = (folder / "input.docx").read_bytes(), (folder / "output.docx").read_bytes()
    saved = json.loads((folder / "report.json").read_text())
    assert saved["status"] == "saved"
    assert saved["review"]["approved"] is True
    assert saved["integrity"]["ok"] is True
    assert saved["instruction"] == SAMPLES[name].instruction
    _assert_live_provenance(folder, before, after, name)
    touched = {bid for op in saved["operations"] for bid in op["touched"]}
    removed = {category for op in saved["operations"] for category in op["removed"]}
    assert not removed
    independent = check(before, after, touched=touched, allow_removed=removed)
    assert independent.ok, independent.violations
    assert independent.to_dict() == saved["integrity"]
    Document(io.BytesIO(after))
    index = BlockIndex.from_package(DocxPackage.from_bytes(after))
    texts = [paragraph_text(index.paragraph(bid)) for bid in index.paragraph_ids()]
    _assert_expected_changes(name, index, texts, independent)


def _assert_live_provenance(folder, before, after, name):
    provenance = json.loads((folder / "run.json").read_text())
    assert provenance["run_kind"] == "live"
    assert provenance["status"] == "saved"
    assert provenance["provider"] == "google_genai"
    assert provenance["model"] == "gemini-3.5-flash-lite"
    assert provenance["input_sha256"] == hashlib.sha256(before).hexdigest()
    assert provenance["output_sha256"] == hashlib.sha256(after).hexdigest()
    assert (
        provenance["instruction_sha256"]
        == hashlib.sha256(SAMPLES[name].instruction.encode()).hexdigest()
    )
    retries = provenance.get("transport_retries", 0)
    assert provenance["api_calls"] == len(provenance["responses"]) + retries
    assert provenance["api_calls"] <= provenance["max_editor_calls"] + provenance[
        "max_review_calls"
    ] + provenance.get("max_transport_retries", 0)
    assert provenance["api_calls"] == provenance["editor_calls"] + 1 + retries
    assert all(response["usage"]["total_tokens"] > 0 for response in provenance["responses"])
    assert (folder / "diff.md").is_file()


def _assert_expected_changes(name, index, texts, independent):
    if name == "therapy_session_note":
        assert "The client Max Mustermann attended the weekly session on 19 March 2026." in texts
        assert paragraph_text(index.paragraph("t0.r1.c1.p0")) == "7.5h"
        anchor = texts.index("Practised breathing exercises.")
        assert texts[anchor + 1] == "Agreed to keep a sleep diary."
        inserted = index.paragraph("p5")
        assert (
            inserted.find("{*}pPr/{*}pStyle").get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"
            )
            == "ListBullet"
        )
        assert {c.before_id for c in independent.changes if c.kind == "modified"} == {
            "p2",
            "t0.r1.c1.p0",
        }
        assert [c.kind for c in independent.changes].count("inserted") == 1
    elif name == "treatment_plan":
        assert "Review after six weeks." in texts
        assert "Internal draft placeholder - remove before sharing." not in texts
        assert paragraph_text(index.paragraph("t0.r1.c2.p0")) == "In progress"
        assert {c.before_id for c in independent.changes} == {"p4", "p5", "t0.r1.c2.p0"}
        assert [c.kind for c in independent.changes].count("deleted") == 1
    else:
        assert "Your appointment is on 27 March 2026 at 10:30." in texts
        assert "Please bring the completed form.1" in texts
        anchor = texts.index("Please bring the completed form.1")
        assert texts[anchor + 1] == "Please contact the practice if you need to reschedule."
        assert {c.before_id for c in independent.changes if c.kind == "modified"} == {"p4", "p5"}
        assert [c.kind for c in independent.changes].count("inserted") == 1
    assert len(independent.changes) == 3


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_runner_with_scripted_model(name: str, tmp_path: Path) -> None:
    generate(tmp_path)
    edits = {
        "therapy_session_note": [
            ("replace_text", {"old": "12 March 2026", "new": "19 March 2026"}),
            ("replace_text", {"old": "8h", "new": "7.5h"}),
            ("insert_paragraph", {"anchor_id": "p4", "text": "Agreed to keep a sleep diary."}),
        ],
        "treatment_plan": [
            ("replace_text", {"old": "Review after four weeks.", "new": "Review after six weeks."}),
            ("replace_text", {"old": "Planned", "new": "In progress"}),
            ("delete_paragraph", {"block_id": "p5"}),
        ],
        "practice_letter": [
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
    }
    model = ScriptedModel(responses=[calls(*edits[name]), calls(("finish", {}))])
    settings = Settings(
        _env_file=None,
        provider="google_genai",
        model="offline",
        max_retries=0,
        max_turns=5,
        GEMINI_API_KEY="",
        ANTHROPIC_API_KEY="",
    )
    record = run_sample(name, model, settings, root=tmp_path)
    assert record["status"] == "saved", record
    assert record["run_kind"] == "scripted"
    assert check(
        (tmp_path / name / "input.docx").read_bytes(),
        (tmp_path / name / "output.docx").read_bytes(),
    ).ok
