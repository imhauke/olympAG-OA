from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docx_agent import __version__
from docx_agent.cli import app
from docx_agent.document import ops
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.ns import qn
from docx_agent.document.package import DocxPackage

runner = CliRunner()


@pytest.fixture
def files(tmp_path: Path, rich_docx: bytes) -> dict[str, Path]:
    before = tmp_path / "before.docx"
    before.write_bytes(rich_docx)

    pkg = DocxPackage.from_bytes(rich_docx)
    ops.replace_text(BlockIndex.from_package(pkg), "8h", "7.5h")
    edited = tmp_path / "edited.docx"
    pkg.save(edited)

    pkg = DocxPackage.from_bytes(rich_docx)
    drawing = BlockIndex.from_package(pkg).get("p6").find(f".//{qn('w:drawing')}")
    assert drawing is not None
    parent = drawing.getparent()
    assert parent is not None
    parent.remove(drawing)
    broken = tmp_path / "broken.docx"
    pkg.save(broken)

    junk = tmp_path / "junk.docx"
    junk.write_bytes(b"not a zip")
    return {"before": before, "edited": edited, "broken": broken, "junk": junk}


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_inspect(files: dict[str, Path]) -> None:
    result = runner.invoke(app, ["inspect", str(files["before"])])
    assert result.exit_code == 0
    assert '[p0] (heading 1) "Session Note - Max Mustermann"' in result.stdout
    ranged = runner.invoke(
        app, ["inspect", str(files["before"]), "--from-id", "p8", "--to-id", "p8"]
    )
    assert ranged.stdout.strip().splitlines()[1:] == ['[p8] "Page 1" [read-only: field]']


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["inspect", "{junk}"], "not a zip"),
        (["inspect", "{before}", "--from-id", "p99"], "no block 'p99'"),
        (["verify", "{before}", "{junk}"], "not a zip"),
    ],
)
def test_bad_input_exits_2(files: dict[str, Path], args: list[str], message: str) -> None:
    argv = [a.format(**{k: str(v) for k, v in files.items()}) for a in args]
    result = runner.invoke(app, argv)
    assert result.exit_code == 2
    assert message in result.stderr


def test_verify_clean_edit(files: dict[str, Path]) -> None:
    result = runner.invoke(app, ["verify", str(files["before"]), str(files["edited"])])
    assert result.exit_code == 0
    assert "modified  t0.r1.c1.p0 -> t0.r1.c1.p0" in result.stdout
    assert "  - '8h'" in result.stdout
    assert "  + '7.5h'" in result.stdout
    assert "OK: integrity checks passed." in result.stdout


def test_verify_unchanged(files: dict[str, Path]) -> None:
    result = runner.invoke(app, ["verify", str(files["before"]), str(files["before"])])
    assert result.exit_code == 0
    assert "No paragraph changed." in result.stdout


def test_verify_detects_corruption(files: dict[str, Path]) -> None:
    result = runner.invoke(app, ["verify", str(files["before"]), str(files["broken"])])
    assert result.exit_code == 1
    assert "! Protected content removed or altered: image." in result.stdout
    allowed = runner.invoke(
        app,
        ["verify", str(files["before"]), str(files["broken"]), "--allow-removed", "image"],
    )
    assert allowed.exit_code == 0


def test_verify_json(files: dict[str, Path]) -> None:
    result = runner.invoke(app, ["verify", str(files["before"]), str(files["broken"]), "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["changes"][0]["kind"] == "modified"
    assert data["changes"][0]["before_id"] == "p6"
