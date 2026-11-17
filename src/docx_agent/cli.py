"""Command-line interface for docx-agent."""

from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import typer
import uvicorn
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from docx_agent import __version__
from docx_agent.agent.graph import AgentContext, AgentError, build_graph, root_cause
from docx_agent.agent.models import Settings, create_model
from docx_agent.agent.session import DocumentSession
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.integrity import IntegrityReport, check
from docx_agent.document.package import DocxPackage, PackageError
from docx_agent.document.styles import StyleCatalog
from docx_agent.document.view import render
from docx_agent.web.app import create_app

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


@app.command()
def edit(
    path: DocxPath,
    instruction: Annotated[str, typer.Argument(help="Requested edits in natural language.")],
    *,
    output: Annotated[Path, typer.Option("--output", "-o", help="Output .docx path.")],
    report: Annotated[Path | None, typer.Option(help="Default: OUTPUT.report.json.")] = None,
    model: Annotated[str | None, typer.Option(help="Override DOCX_AGENT_MODEL.")] = None,
    provider: Annotated[
        Literal["google_genai", "anthropic"] | None,
        typer.Option(help="Override DOCX_AGENT_PROVIDER."),
    ] = None,
    non_interactive: Annotated[
        bool, typer.Option(help="Exit 3 if clarification is needed.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option(help="Validate and review without writing files.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Save failed validation/review after retries (exit 1).")
    ] = False,
) -> None:
    """Edit a document with a bounded tool-calling agent; preserve the original."""
    try:
        settings = Settings()
        if provider is not None:
            settings.provider = provider
        if model is not None:
            settings.model = model
        session = DocumentSession(path.read_bytes())
        context = AgentContext(
            session=session,
            output=output,
            report=report,
            dry_run=dry_run,
            force=force,
            max_retries=settings.max_retries,
            max_turns=settings.max_turns,
        )
        if not instruction.strip():
            raise ValueError("The instruction must not be empty.")
        for destination in (output, context.report_path):
            if destination.resolve() == path.resolve() or (
                destination.exists() and destination.samefile(path)
            ):
                raise ValueError("Output and report must not overwrite the input.")
            if not destination.parent.is_dir() or destination.is_dir():
                raise ValueError(f"Destination is not a writable file path: {destination}")
        chat_model = create_model(settings)
        graph = build_graph(chat_model)
        config: RunnableConfig = {
            "configurable": {"thread_id": str(uuid4())},
            "recursion_limit": 4 * settings.max_turns + 20,
        }
        typer.echo(
            f"Editing with {settings.provider}/{settings.model_name}; "
            f"at most {settings.max_turns} editor calls and "
            f"{settings.max_retries + 1} review calls.",
            err=True,
        )
        state = graph.invoke({"instruction": instruction}, config, context=context)
        while state.get("__interrupt__"):
            question = state["__interrupt__"][0].value["question"]
            if non_interactive:
                typer.echo(f"Clarification required: {question}", err=True)
                raise typer.Exit(3)
            answer = typer.prompt(question)
            state = graph.invoke(Command(resume=answer), config, context=context)
    except (PackageError, OSError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    except AgentError as exc:
        typer.echo(f"error: {exc}{root_cause(exc)}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{state['status']}: {state.get('reason', '')}")
    if state.get("integrity"):
        typer.echo(json.dumps(state["integrity"], ensure_ascii=False, indent=2))
    if state["status"] in {"failed", "forced"}:
        raise typer.Exit(1)


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Local port.")] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open a browser tab.")
    ] = True,
) -> None:
    """Start the local web UI on http://127.0.0.1:PORT (never exposed beyond this machine)."""
    settings = Settings()
    if not settings.available_providers():
        typer.echo(
            "warning: no GEMINI_API_KEY or ANTHROPIC_API_KEY found; edits will be disabled.",
            err=True,
        )
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"docx-agent UI on {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    app()
