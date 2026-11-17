"""Interactive menu (``docx-agent`` without a command), driven with a scripted model."""

import io
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docx_agent import cli
from docx_agent.agent.models import Settings
from docx_agent.document.integrity import check
from docx_agent.menu import find_documents, output_path
from tests.graph.fake import ScriptedModel, calls

runner = CliRunner()


def settings(gemini: str = "test-key", anthropic: str = "") -> Settings:
    return Settings(
        _env_file=None,
        provider="google_genai",
        model="offline-model",
        max_turns=10,
        max_retries=0,
        GEMINI_API_KEY=gemini,
        ANTHROPIC_API_KEY=anthropic,
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rich_docx: bytes) -> Path:
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "notes.docx").write_bytes(rich_docx)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "Settings", settings)
    return tmp_path


def use_model(monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> None:
    monkeypatch.setattr(cli, "create_model", lambda _: model)


def menu(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def test_no_command_opens_menu_and_quits(workspace: Path) -> None:
    result = runner.invoke(cli.app, [], input=menu("q"))
    assert result.exit_code == 0, result.output
    assert "Edit a document" in result.output
    assert "Gemini · offline-model" in result.output
    assert "1 found in documents/" in result.output
    assert "Bye." in result.output


def test_end_of_input_exits_cleanly(workspace: Path) -> None:
    result = runner.invoke(cli.app, [], input="")
    assert result.exit_code == 0
    assert "Bye." in result.output


def test_edit_saves_verified_copy_and_keeps_original(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = workspace / "documents" / "notes.docx"
    original = source.read_bytes()
    model = ScriptedModel(
        responses=[calls(("replace_text", {"old": "8h", "new": "7h"}), ("finish", {}))]
    )
    use_model(monkeypatch, model)
    result = runner.invoke(cli.app, [], input=menu("1", "1", "Change 8h to 7h", "", "q"))
    assert result.exit_code == 0, result.output
    edited = workspace / "documents" / "edited" / "notes.edited.docx"
    assert "Saved and verified" in result.output
    assert "7h+}" in result.output  # plain-terminal diff markers
    assert "documents/edited/notes.edited.docx" in result.output
    assert "at most 10 editor calls and 1 review calls" in result.output
    assert source.read_bytes() == original
    assert check(original, edited.read_bytes()).ok
    assert edited.with_suffix(".report.json").exists()


def test_edit_asks_clarification_inline(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedModel(
        responses=[
            calls(("ask_clarification", {"question": "Which date do you mean?"})),
            calls(("replace_text", {"old": "8h", "new": "7h"}), ("finish", {})),
        ]
    )
    use_model(monkeypatch, model)
    lines = menu("1", "1", "Change the value", "", "The sleep target", "", "q")
    result = runner.invoke(cli.app, [], input=lines)
    assert result.exit_code == 0, result.output
    assert "Which date do you mean?" in result.output
    assert "Type an answer" in result.output  # the empty answer was refused
    assert "Saved and verified" in result.output


def test_rejected_edit_saves_nothing(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedModel(
        responses=[calls(("finish", {}))],
        decisions=[{"approved": False, "reason": "The date was not changed."}],
    )
    use_model(monkeypatch, model)
    result = runner.invoke(cli.app, [], input=menu("1", "1", "Change the date", "", "q"))
    assert result.exit_code == 0, result.output
    assert "Nothing was saved" in result.output
    assert "The date was not changed." in result.output
    assert not list((workspace / "documents" / "edited").glob("*.docx"))


def test_provider_error_is_explained(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_model(monkeypatch, ScriptedModel(responses=[]))
    result = runner.invoke(cli.app, [], input=menu("1", "1", "Change 8h", "", "q"))
    assert result.exit_code == 0, result.output
    assert "The model call failed" in result.output
    assert "AssertionError: Unexpected LLM call" in result.output
    assert "503 or 429" in result.output


def test_edit_back_and_empty_instruction_return_to_menu(workspace: Path) -> None:
    result = runner.invoke(cli.app, [], input=menu("1", "b", "1", "1", "", "q"))
    assert result.exit_code == 0, result.output
    assert result.output.count("Which document?") == 2
    assert not (workspace / "documents" / "edited").exists()


def test_edit_without_keys_explains_setup(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: settings(gemini=""))
    result = runner.invoke(cli.app, [], input=menu("1", "", "4", "", "q"))
    assert result.exit_code == 0, result.output
    assert "no API key found" in result.output
    assert result.output.count("No API key found.") == 2


def test_typed_path_and_bad_path(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = workspace / "elsewhere folder"
    other.mkdir()
    target = other / "letter.docx"
    target.write_bytes((workspace / "documents" / "notes.docx").read_bytes())
    typed = str(target).replace(" ", "\\ ")
    result = runner.invoke(
        cli.app, [], input=menu("2", "p", typed, "", "2", "p", "'/no/such.docx'", "q")
    )
    assert result.exit_code == 0, result.output
    assert "p0" in result.output  # the ID-tagged view was printed
    assert "Not a .docx file" in result.output


def test_preview_reports_unreadable_file(workspace: Path) -> None:
    (workspace / "documents" / "broken.docx").write_bytes(b"not a zip")
    result = runner.invoke(cli.app, [], input=menu("2", "1", "", "q"))
    assert result.exit_code == 0, result.output
    assert "Could not read it" in result.output


def test_verify_passes_for_a_clean_edit(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedModel(
        responses=[calls(("replace_text", {"old": "8h", "new": "7h"}), ("finish", {}))]
    )
    use_model(monkeypatch, model)
    lines = menu("1", "1", "Change 8h to 7h", "", "3", "1", "1", "", "q")
    result = runner.invoke(cli.app, [], input=lines)
    assert result.exit_code == 0, result.output
    assert "Integrity checks passed" in result.output


def test_verify_flags_a_damaged_copy(workspace: Path, rich_docx: bytes) -> None:
    edited = workspace / "documents" / "edited"
    edited.mkdir()
    with (
        zipfile.ZipFile(io.BytesIO(rich_docx)) as source,
        zipfile.ZipFile(edited / "notes.edited.docx", "w") as target,
    ):
        for info in source.infolist():
            if info.filename != "word/styles.xml":  # a part silently lost
                target.writestr(info, source.read(info))
    result = runner.invoke(cli.app, [], input=menu("3", "1", "1", "", "q"))
    assert result.exit_code == 0, result.output
    assert "integrity violation(s)" in result.output
    assert "--allow-removed image" in result.output


def test_verify_back_returns_to_menu(workspace: Path) -> None:
    result = runner.invoke(cli.app, [], input=menu("3", "1", "b", "3", "b", "q"))
    assert result.exit_code == 0, result.output
    assert result.output.count("Original document") == 2


def test_switch_model(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: settings(anthropic="test-key"))
    result = runner.invoke(cli.app, [], input=menu("4", "2", "q"))
    assert result.exit_code == 0, result.output
    assert "(current)" in result.output
    assert "Claude · claude-sonnet-5-5" in result.output


def test_switch_model_with_one_key_explains(workspace: Path) -> None:
    result = runner.invoke(cli.app, [], input=menu("4", "", "q"))
    assert result.exit_code == 0, result.output
    assert "Add ANTHROPIC_API_KEY to .env" in result.output


def test_web_ui_returns_to_menu_after_ctrl_c(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stopped(*_: object, **__: object) -> None:
        raise KeyboardInterrupt

    class Timer:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(cli.uvicorn, "run", stopped)
    monkeypatch.setattr(cli.threading, "Timer", Timer)
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    result = runner.invoke(cli.app, [], input=menu("5", "q"))
    assert result.exit_code == 0, result.output
    assert "docx-agent UI on http://127.0.0.1:8765" in result.output
    assert "Web UI stopped." in result.output
    assert "Bye." in result.output


def test_find_documents_lists_yours_then_edited_then_samples(tmp_path: Path) -> None:
    for name in ("documents/b.docx", "documents/a.docx", "documents/~$lock.docx"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "documents/edited").mkdir()
    (tmp_path / "documents/edited/a.edited.docx").write_bytes(b"x")
    (tmp_path / "samples/letter").mkdir(parents=True)
    (tmp_path / "samples/letter/input.docx").write_bytes(b"x")
    (tmp_path / "samples/letter/output.docx").write_bytes(b"x")
    sources = [(e.path.relative_to(tmp_path).as_posix(), e.tag) for e in find_documents(tmp_path)]
    assert sources == [
        ("documents/a.docx", ""),
        ("documents/b.docx", ""),
        ("documents/edited/a.edited.docx", "edited"),
        ("samples/letter/input.docx", "sample"),
    ]
    results = [e.tag for e in find_documents(tmp_path, "results")]
    assert results == ["edited", "sample output", "", ""]


def test_output_path_never_overwrites(tmp_path: Path) -> None:
    assert output_path(Path("samples/letter/input.docx"), tmp_path).name == "letter.edited.docx"
    first = output_path(Path("documents/notes.docx"), tmp_path)
    assert first.name == "notes.edited.docx"
    first.write_bytes(b"x")
    second = output_path(Path("documents/notes.docx"), tmp_path)
    assert second.name == "notes.edited-2.docx"
    second.with_suffix(".report.json").write_bytes(b"{}")
    assert output_path(first, tmp_path).name == "notes.edited-3.docx"


def test_web_ui_on_a_busy_port_keeps_the_menu(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_port_free", lambda port: False)
    result = runner.invoke(cli.app, [], input=menu("5", "q"))
    assert result.exit_code == 0, result.output
    assert "port 8765 is already in use" in result.output
    assert "Web UI stopped." in result.output
    assert "Bye." in result.output
