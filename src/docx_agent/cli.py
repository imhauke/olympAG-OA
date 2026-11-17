"""Command-line interface for docx-agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from docx_agent import __version__
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.integrity import IntegrityReport, check
from docx_agent.document.package import DocxPackage, PackageError
from docx_agent.document.styles import StyleCatalog
from docx_agent.document.view import render

app = typer.Typer(
    name="docx-agent",
    help="Edit existing Word (.docx) documents with natural-language instructions.",
    no_args_is_help=True,
    add_completion=False,
)

EXIT_INVALID_INPUT = 2

DocxPath = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_INVALID_INPUT)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def inspect(
    path: DocxPath,
    from_id: Annotated[str | None, typer.Option(help="First block to show.")] = None,
    to_id: Annotated[str | None, typer.Option(help="Last block to show.")] = None,
    max_chars: Annotated[int, typer.Option(help="Size budget of the view.")] = 20_000,
) -> None:
    """Print the ID-tagged view of a document (exactly what the agent sees)."""
    try:
        pkg = DocxPackage.open(path)
        view = render(
            BlockIndex.from_package(pkg),
            StyleCatalog.from_package(pkg),
            from_id=from_id,
            to_id=to_id,
            max_chars=max_chars,
        )
    except PackageError as exc:
        raise _fail(str(exc)) from exc
    except EditError as exc:
        raise _fail(exc.message) from exc
    typer.echo(view)


def _print_report(report: IntegrityReport) -> None:
    for change in report.changes:
        ids = f"{change.before_id or '-'} -> {change.after_id or '-'}"
        typer.echo(f"{change.kind:<9} {ids}")
        if change.kind != "inserted":
            typer.echo(f"  - {change.before_text!r}")
        if change.kind != "deleted":
            typer.echo(f"  + {change.after_text!r}")
    if not report.changes:
        typer.echo("No paragraph changed.")
    if report.ok:
        typer.echo("OK: integrity checks passed.")
    else:
        typer.echo(f"FAILED: {len(report.violations)} integrity violation(s):")
        for violation in report.violations:
            typer.echo(f"  ! {violation}")


@app.command()
def verify(
    before: DocxPath,
    after: DocxPath,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable report.")] = False,
    allow_removed: Annotated[
        list[str] | None,
        typer.Option(help="Category whose removal is intended (e.g. image); repeatable."),
    ] = None,
) -> None:
    """Check that AFTER differs from BEFORE only by clean text edits. Exit 1 on violations."""
    try:
        report = check(before.read_bytes(), after.read_bytes(), allow_removed=allow_removed or ())
    except PackageError as exc:
        raise _fail(str(exc)) from exc
    if as_json:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    if not report.ok:
        raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
