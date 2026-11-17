import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from docx_agent import cli
from docx_agent.agent.models import Settings
from docx_agent.document.integrity import check
from tests.graph.fake import ScriptedModel, calls

runner = CliRunner()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: Settings(
            _env_file=None,
            provider="google_genai",
            model="offline-model",
            max_turns=10,
            max_retries=0,
            GEMINI_API_KEY="",
            ANTHROPIC_API_KEY="",
        ),
    )


@pytest.fixture
def paths(tmp_path: Path, rich_docx: bytes) -> tuple[Path, Path, Path]:
    source, output, report = tmp_path / "in.docx", tmp_path / "out.docx", tmp_path / "r.json"
    source.write_bytes(rich_docx)
    return source, output, report


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("dry_run", [False, True])
def test_edit_cli(paths, monkeypatch: pytest.MonkeyPatch, dry_run: bool) -> None:
    source, output, report = paths
    original = source.read_bytes()
    model = ScriptedModel(
        responses=[calls(("replace_text", {"old": "8h", "new": "7h"})), calls(("finish", {}))]
    )
    monkeypatch.setattr(cli, "create_model", lambda _: model)
    args = [
        "edit",
        str(source),
        "Change 8h to 7h",
        "-o",
        str(output),
        "--report",
        str(report),
        "--model",
        "offline-override",
        "--provider",
        "anthropic",
    ]
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "anthropic/offline-override" in result.stderr
    assert "at most 10 editor calls and 1 review calls" in result.stderr
    assert '"after_text": "7h"' in result.stdout
    assert source.read_bytes() == original
    if dry_run:
        assert not output.exists()
        assert not report.exists()
    else:
        assert check(original, output.read_bytes()).ok
        assert json.loads(report.read_text())["status"] == "saved"


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("interactive", [False, True])
def test_edit_cli_clarification(paths, monkeypatch: pytest.MonkeyPatch, interactive: bool) -> None:
    source, output, _ = paths
    model = ScriptedModel(
        responses=[calls(("ask_clarification", {"question": "Which date?"})), calls(("finish", {}))]
    )
    monkeypatch.setattr(cli, "create_model", lambda _: model)
    args = ["edit", str(source), "Change the date", "-o", str(output)]
    if not interactive:
        args.append("--non-interactive")
    result = runner.invoke(cli.app, args, input="Keep the dates unchanged\n")
    assert result.exit_code == (0 if interactive else 3), result.output
    assert "Which date?" in result.output
    assert output.exists() is interactive


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("force", [False, True])
def test_edit_cli_failed_review(paths, monkeypatch: pytest.MonkeyPatch, force: bool) -> None:
    source, output, _ = paths
    model = ScriptedModel(
        responses=[calls(("finish", {}))], decisions=[{"approved": False, "reason": "Missing edit"}]
    )
    monkeypatch.setattr(cli, "create_model", lambda _: model)
    args = ["edit", str(source), "Change sleep", "-o", str(output)]
    if force:
        args.append("--force")
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1, result.output
    assert "Missing edit" in result.stdout
    assert output.exists() is force


@pytest.mark.usefixtures("configured")
def test_edit_cli_provider_failure(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output, _ = paths
    monkeypatch.setattr(cli, "create_model", lambda _: ScriptedModel(responses=[]))
    result = runner.invoke(cli.app, ["edit", str(source), "Change sleep", "-o", str(output)])
    assert result.exit_code == 1
    assert "Editor model call failed" in result.stderr
    assert not output.exists()


@pytest.mark.usefixtures("configured")
def test_edit_cli_invalid_review(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output, _ = paths
    model = ScriptedModel(
        responses=[calls(("finish", {}))], decisions=[{"approved": "yes", "reason": "bad schema"}]
    )
    monkeypatch.setattr(cli, "create_model", lambda _: model)
    result = runner.invoke(cli.app, ["edit", str(source), "Change sleep", "-o", str(output)])
    assert result.exit_code == 1
    assert "structured response failed" in result.stderr
    assert not output.exists()


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("case", ["input", "report_input", "same", "directory", "empty", "key"])
def test_edit_cli_bad_input(paths, case: str) -> None:
    source, output, report = paths
    instruction = "Change sleep"
    if case == "input":
        output = source
    elif case == "report_input":
        report = source
    elif case == "same":
        report = output
    elif case == "directory":
        output = output.parent
    elif case == "empty":
        instruction = " "
    result = runner.invoke(
        cli.app, ["edit", str(source), instruction, "-o", str(output), "--report", str(report)]
    )
    assert result.exit_code == 2, result.output


@pytest.mark.usefixtures("configured")
def test_output_and_report_hardlink_rejected(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output, report = paths
    output.write_bytes(b"old artifact")
    report.hardlink_to(output)
    model = ScriptedModel(responses=[calls(("finish", {}))])
    monkeypatch.setattr(cli, "create_model", lambda _: model)
    result = runner.invoke(
        cli.app, ["edit", str(source), "No change", "-o", str(output), "--report", str(report)]
    )
    assert result.exit_code == 2, result.output
    assert output.read_bytes() == b"old artifact"


@pytest.mark.usefixtures("configured")
def test_input_hardlink_rejected(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output, _ = paths
    output.hardlink_to(source)
    original = source.read_bytes()
    monkeypatch.setattr(
        cli, "create_model", lambda _: ScriptedModel(responses=[AIMessage(content="")])
    )
    result = runner.invoke(cli.app, ["edit", str(source), "Change sleep", "-o", str(output)])
    assert result.exit_code == 2
    assert source.read_bytes() == original
