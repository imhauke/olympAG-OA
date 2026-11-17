"""Command-line interface for docx-agent."""

from __future__ import annotations

import typer

from docx_agent import __version__

app = typer.Typer(
    name="docx-agent",
    help="Edit existing Word (.docx) documents with natural-language instructions.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """Keep a command group even while only one sub-command exists."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
