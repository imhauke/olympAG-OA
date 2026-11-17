from pathlib import Path

import pytest

from docx_agent.agent.artifacts import write_artifacts


@pytest.mark.parametrize("existing", [False, True])
def test_publish_failure_restores_previous_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    output, report = tmp_path / "out.docx", tmp_path / "report.json"
    if existing:
        output.write_bytes(b"previous doc")
        report.write_bytes(b"previous report")
    replace = Path.replace

    def fail_report(path, target):
        if target == report:
            raise PermissionError("Simulated report publish failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_report)
    with pytest.raises(PermissionError, match="Simulated"):
        write_artifacts(output, b"new doc", report, b"new report")
    if existing:
        assert output.read_bytes() == b"previous doc"
        assert report.read_bytes() == b"previous report"
    else:
        assert not output.exists()
        assert not report.exists()
    assert not list(tmp_path.glob(".docx-agent-*"))
