"""Stage both artifacts before publishing; roll back a failed publish."""

from pathlib import Path
from tempfile import NamedTemporaryFile


def write_artifacts(output: Path, document: bytes, report: Path, report_bytes: bytes) -> None:
    """Keep previous artifacts intact on ordinary write/rename errors.

    This is not a crash-atomic two-file transaction; each individual rename is atomic.
    """
    staged: list[tuple[Path, Path]] = []
    published: list[Path] = []
    previous = {path: path.read_bytes() if path.exists() else None for path in (output, report)}
    try:
        for destination, data in ((output, document), (report, report_bytes)):
            with NamedTemporaryFile(
                dir=destination.parent, prefix=".docx-agent-", delete=False
            ) as f:
                temporary = Path(f.name)
                staged.append((temporary, destination))
                f.write(data)
            # The final rename preserves these restrictive temporary-file permissions.
        for temporary, destination in staged:
            temporary.replace(destination)
            published.append(destination)
    except OSError:
        for destination in reversed(published):
            old = previous[destination]
            if old is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(old)
        raise
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
