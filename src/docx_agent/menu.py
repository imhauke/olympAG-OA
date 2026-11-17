"""Interactive terminal menu, opened by running ``docx-agent`` without a command.

It follows the same steps as the web UI: pick a ``.docx`` from ``documents/`` (or one of the
synthetic samples), describe the change, answer a question if the agent asks one, then read
the verified changes. Edited copies go to ``documents/edited/``; originals are never modified.
The one-shot commands (``edit``, ``inspect``, ``verify``, ``ui``) stay available for scripts.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from rich.console import Console
from rich.padding import Padding
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from docx_agent import __version__
from docx_agent.agent.graph import AgentContext, AgentError, AgentState, build_graph, root_cause
from docx_agent.agent.models import PROVIDER_LABELS, Provider, Settings
from docx_agent.agent.session import DocumentSession
from docx_agent.document.blocks import BlockIndex
from docx_agent.document.errors import EditError
from docx_agent.document.integrity import check
from docx_agent.document.package import DocxPackage, PackageError
from docx_agent.document.styles import StyleCatalog
from docx_agent.document.view import render
from docx_agent.web.diff import present
from docx_agent.web.jobs import NODE_STEP, STEPS, ModelFactory, friendly_reason, progress_detail

DOCUMENTS = Path("documents")
EDITED = DOCUMENTS / "edited"
SAMPLES = Path("samples")
LONG_EQUAL = 140  # unchanged text longer than this is shortened in the change list

Graph = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]


@dataclass(frozen=True)
class Entry:
    path: Path
    tag: str = ""


def _docx_files(folder: Path, pattern: str) -> list[Path]:
    return sorted(
        p for p in folder.glob(pattern) if p.is_file() and not p.name.startswith(("~$", "."))
    )


def find_documents(root: Path, kind: Literal["sources", "results"] = "sources") -> list[Entry]:
    """Documents the menu offers: your files, edited copies and the synthetic samples."""
    mine = [Entry(p) for p in _docx_files(root / DOCUMENTS, "*.docx")]
    edited = [Entry(p, "edited") for p in _docx_files(root / EDITED, "*.docx")]
    samples = [Entry(p, "sample") for p in _docx_files(root / SAMPLES, "*/input.docx")]
    if kind == "sources":
        return mine + edited + samples
    outputs = [Entry(p, "sample output") for p in _docx_files(root / SAMPLES, "*/output.docx")]
    return edited + outputs + mine


def output_path(source: Path, folder: Path) -> Path:
    """``<folder>/<name>.edited.docx``; never overwrites an earlier result or its report."""
    stem = source.parent.name if source.stem == "input" and source.parent.name else source.stem
    stem = re.sub(r"\.edited(-\d+)?$", "", stem) or "document"
    candidate, number = folder / f"{stem}.edited.docx", 2
    while candidate.exists() or candidate.with_suffix(".report.json").exists():
        candidate, number = folder / f"{stem}.edited-{number}.docx", number + 1
    return candidate


def _size(path: Path) -> str:
    size = path.stat().st_size
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 2**20:.1f} MB"


def _clean_path(raw: str) -> Path:
    """Accept paths pasted or dragged into a terminal (quoted, or with escaped spaces)."""
    text = raw.strip().strip("'\"").replace("\\ ", " ")
    return Path(text).expanduser()


class Menu:
    """The interactive loop. Factories are injected so tests can run it with a scripted model."""

    def __init__(
        self,
        *,
        settings_factory: Callable[[], Settings],
        model_factory: ModelFactory,
        launch_ui: Callable[[], None],
        root: Path | None = None,
        console: Console | None = None,
    ) -> None:
        self.settings_factory = settings_factory
        self.model_factory = model_factory
        self.launch_ui = launch_ui
        self.root = root or Path.cwd()
        self.console = console or Console(highlight=False)
        base = settings_factory()
        available = base.available_providers()
        self.provider: Provider | None = (
            base.provider if base.provider in available else next(iter(available), None)
        )

    # ------------------------------------------------------------------ loop

    def run(self) -> None:
        console = self.console
        console.print()
        console.print(
            Text.assemble(
                ("docx-agent", "bold"),
                (f"  {__version__}", "dim"),
                "\nEdit Word documents with plain-language instructions. "
                "Everything you didn't ask to change stays exactly as it was.",
            )
        )
        actions: dict[str, Callable[[], None]] = {
            "1": self.edit,
            "2": self.preview,
            "3": self.verify,
            "4": self.switch_model,
            "5": self.web_ui,
        }
        try:
            while True:
                choice = self._main_menu()
                if choice == "q":
                    break
                actions[choice]()
        except (KeyboardInterrupt, EOFError):
            console.print()
        console.print("[dim]Bye.[/]")

    def _main_menu(self) -> str:
        console = self.console
        console.print()
        console.print(Rule(style="dim"))
        console.print(Text.assemble(("Model      ", "dim"), self._model_label()))
        count = len(find_documents(self.root))
        console.print(
            Text.assemble(
                ("Documents  ", "dim"),
                f"{count} found in {DOCUMENTS}/ and {SAMPLES}/",
            )
        )
        console.print()
        items = Table.grid(padding=(0, 2))
        items.add_row("[bold]1[/]", "Edit a document")
        items.add_row("[bold]2[/]", "Preview a document [dim](what the agent sees)[/]")
        items.add_row("[bold]3[/]", "Verify an edited copy against its original")
        items.add_row("[bold]4[/]", "Switch model")
        items.add_row("[bold]5[/]", "Open the web UI in your browser")
        items.add_row("[bold]q[/]", "Quit")
        console.print(Padding(items, (0, 0, 0, 2)))
        return Prompt.ask(
            "\nChoose",
            choices=["1", "2", "3", "4", "5", "q"],
            default="1",
            show_choices=False,
            console=console,
        )

    def _model_label(self) -> Text:
        settings = self._settings()
        if settings is None:
            return Text("no API key found (add one to .env, see README step 3)", style="bold")
        return Text.assemble(
            (PROVIDER_LABELS[settings.provider], "bold"), f" · {settings.model_name}"
        )

    def _settings(self) -> Settings | None:
        base = self.settings_factory()
        if self.provider is None or self.provider not in base.available_providers():
            return None
        return base.for_provider(self.provider)

    def _pause(self) -> None:
        self.console.input("\n[dim]Press Enter to return to the menu[/]")

    def _no_keys(self) -> None:
        self.console.print(
            "\n[bold]No API key found.[/] Copy [bold].env.example[/] to [bold].env[/], set "
            "GEMINI_API_KEY and/or ANTHROPIC_API_KEY, then start the menu again "
            "(README, step 3)."
        )

    # ------------------------------------------------------------------ picking files

    def _pick(self, title: str, entries: list[Entry]) -> Path | None:
        console = self.console
        console.print(f"\n[bold]{title}[/]")
        if not entries:
            console.print(
                f"[dim]No .docx files yet. Copy some into {DOCUMENTS}/ or type a path.[/]"
            )
        rows = Table.grid(padding=(0, 2))
        for number, entry in enumerate(entries, 1):
            rows.add_row(
                f"[bold]{number}[/]",
                Text(self._relative(entry.path)),
                Text(_size(entry.path), style="dim"),
                Text(entry.tag, style="dim"),
            )
        rows.add_row("[bold]p[/]", "Type or drag in a path to another .docx", "", "")
        rows.add_row("[bold]b[/]", "Back", "", "")
        console.print(Padding(rows, (0, 0, 0, 2)))
        choices = [str(n) for n in range(1, len(entries) + 1)] + ["p", "b"]
        answer = Prompt.ask(
            "Number",
            choices=choices,
            default="1" if entries else "p",
            show_choices=False,
            console=console,
        )
        if answer == "b":
            return None
        if answer != "p":
            return entries[int(answer) - 1].path
        path = _clean_path(Prompt.ask("Path", console=console))
        if not path.is_file() or path.suffix.lower() != ".docx":
            console.print(Text.assemble(("Not a .docx file: ", "bold"), str(path)))
            return None
        return path

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)

    # ------------------------------------------------------------------ actions

    def edit(self) -> None:
        console = self.console
        settings = self._settings()
        if settings is None:
            self._no_keys()
            self._pause()
            return
        source = self._pick("Which document?", find_documents(self.root))
        if source is None:
            return
        console.print(
            "\n[dim]Say where and what, e.g. “Change the session date to 19 March 2026” or "
            "“Remove the bullet about breathing exercises”. Leave empty to go back.[/]"
        )
        instruction = Prompt.ask("What should change?", console=console).strip()
        if not instruction:
            return
        output = output_path(source, self.root / EDITED)
        started = time.monotonic()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            values, context = self._run(source, instruction, output, settings)
        except KeyboardInterrupt:
            console.print("\n[bold]Edit cancelled.[/] Your original file was not modified.")
        except (PackageError, OSError, ValueError) as exc:
            console.print(Text.assemble(("\nCould not start the edit: ", "bold"), str(exc)))
        except AgentError as exc:
            console.print(Text.assemble(("\n✗ The model call failed. ", "bold"), str(exc)))
            if cause := root_cause(exc):
                console.print(Text(cause.strip(), style="dim"))
            console.print(
                "[dim]A 503 or 429 means the provider is busy or rate-limited: wait a minute "
                "and try again, or switch model (4). Nothing was saved.[/]"
            )
        else:
            self._show_result(values, context, time.monotonic() - started)
        self._pause()

    def _run(
        self, source: Path, instruction: str, output: Path, settings: Settings
    ) -> tuple[dict[str, Any], AgentContext]:
        session = DocumentSession(source.read_bytes())
        context = AgentContext(
            session=session,
            output=output,
            max_retries=settings.max_retries,
            max_turns=settings.max_turns,
        )
        graph = build_graph(self.model_factory(settings))
        config: RunnableConfig = {
            "configurable": {"thread_id": uuid4().hex},
            "recursion_limit": 4 * settings.max_turns + 20,
        }
        self.console.print(
            f"\n[dim]Editing with {PROVIDER_LABELS[settings.provider]} "
            f"({settings.model_name}): at most {settings.max_turns} editor calls and "
            f"{settings.max_retries + 1} review calls.[/]"
        )
        payload: AgentState | Command[Any] = AgentState(instruction=instruction)
        while (question := self._stream(graph, payload, config, context)) is not None:
            payload = Command(resume=self._answer(question))
        return graph.get_state(config).values, context

    def _stream(
        self,
        graph: Graph,
        payload: AgentState | Command[Any],
        config: RunnableConfig,
        context: AgentContext,
    ) -> str | None:
        """Run until the graph ends (None) or asks a question (returned)."""
        shown: str | None = None
        with self.console.status("Working", spinner="dots") as status:
            for update in graph.stream(payload, config, context=context, stream_mode="updates"):
                if "__interrupt__" in update:
                    return str(update["__interrupt__"][0].value["question"])
                for node in update:
                    step = STEPS[NODE_STEP.get(node, 1)]
                    detail = progress_detail(node, len(context.session.edits)) or ""
                    status.update(f"{step} [dim]· {detail}[/]")
                    if step != shown:
                        self.console.print(Text.assemble("  • ", step))
                        shown = step
        return None

    def _answer(self, question: str) -> str:
        console = self.console
        console.print()
        console.print(Text("One question before editing", style="bold"))
        console.print(Padding(Text(question), (0, 0, 0, 2)))
        while not (answer := Prompt.ask("Your answer", console=console).strip()):
            console.print("[dim]Type an answer, e.g. which of the matches you mean.[/]")
        return answer

    def _show_result(self, values: dict[str, Any], context: AgentContext, seconds: float) -> None:
        console = self.console
        if values.get("status") != "saved":
            console.print(Text("\n✗ Nothing was saved", style="bold"))
            console.print(Text(friendly_reason(values.get("reason"))))
            for violation in (values.get("integrity") or {}).get("violations", []):
                console.print(Text(f"  ! {violation}", style="dim"))
            console.print(
                "[dim]Your original file was not modified. Try rephrasing the "
                "instruction with the exact text to change.[/]"
            )
            return
        console.print(
            Text.assemble(("\n✓ Saved and verified", "bold blue"), (f"  in {seconds:.1f} s", "dim"))
        )
        summary = values.get("reason") or values.get("summary")
        if summary:
            console.print(Text(str(summary)))
        changes = [present(c) for c in (values.get("integrity") or {}).get("changes", [])]
        self._print_changes(changes)
        removed = sorted({k for e in context.session.edits for k in e.removed})
        if removed:
            console.print(Text(f"Removed on purpose: {', '.join(removed)}", style="dim"))
        table = Table.grid(padding=(0, 2))
        table.add_row(Text("Edited copy", style="dim"), Text(self._relative(context.output)))
        table.add_row(Text("Report", style="dim"), Text(self._relative(context.report_path)))
        console.print(table)

    def _print_changes(self, changes: list[dict[str, Any]]) -> None:
        console = self.console
        plain = console.no_color or not console.is_terminal
        console.print(Text(f"\nChanges ({len(changes)})", style="bold"))
        if not changes:
            console.print(Text("  No paragraph text changed.", style="dim"))
        for change in changes:
            line = Text()
            for segment in change["segments"]:
                kind, text = segment["kind"], segment["text"]
                if kind == "equal":
                    if len(text) > LONG_EQUAL:
                        text = f"{text[:60]} … {text[-60:]}"
                    line.append(text)
                elif kind == "delete":
                    line.append(f"[-{text}-]" if plain else text, style="strike red")
                else:
                    line.append(f"{{+{text}+}}" if plain else text, style="bold green")
            console.print(Text(f"  {change['location']} · {change['kind']}", style="dim"))
            console.print(Padding(line, (0, 0, 1, 4)))

    def preview(self) -> None:
        path = self._pick("Which document?", find_documents(self.root))
        if path is None:
            return
        try:
            pkg = DocxPackage.open(path)
            view = render(BlockIndex.from_package(pkg), StyleCatalog.from_package(pkg))
        except (PackageError, EditError, OSError) as exc:
            message = exc.message if isinstance(exc, EditError) else str(exc)
            self.console.print(Text.assemble(("Could not read it: ", "bold"), message))
        else:
            self.console.print(Rule(self._relative(path), style="dim"))
            self.console.print(Text(view))
            self.console.print(Rule(style="dim"))
            self.console.print(
                "[dim]This is exactly what the agent reads: each block has an ID (p3, "
                "t0.r1.c2.p0) that its edits refer to.[/]"
            )
        self._pause()

    def verify(self) -> None:
        console = self.console
        original = self._pick("Original document", find_documents(self.root))
        if original is None:
            return
        edited = self._pick("Edited copy to check", find_documents(self.root, "results"))
        if edited is None:
            return
        try:
            report = check(original.read_bytes(), edited.read_bytes())
        except (PackageError, OSError) as exc:
            console.print(Text.assemble(("Could not compare them: ", "bold"), str(exc)))
            self._pause()
            return
        self._print_changes([present(c) for c in report.to_dict()["changes"]])
        if report.ok:
            console.print(
                Text("✓ Integrity checks passed: only the changes above differ.", style="bold blue")
            )
        else:
            console.print(Text(f"✗ {len(report.violations)} integrity violation(s):", style="bold"))
            for violation in report.violations:
                console.print(Text(f"  ! {violation}"))
            console.print(
                "[dim]If an image was removed on purpose, run: "
                "uv run docx-agent verify ORIGINAL EDITED --allow-removed image[/]"
            )
        self._pause()

    def switch_model(self) -> None:
        console = self.console
        base = self.settings_factory()
        available = base.available_providers()
        if not available:
            self._no_keys()
            self._pause()
            return
        console.print("\n[bold]Model[/]")
        rows = Table.grid(padding=(0, 2))
        for number, provider in enumerate(available, 1):
            marker = " (current)" if provider == self.provider else ""
            rows.add_row(
                f"[bold]{number}[/]",
                Text.assemble(
                    (PROVIDER_LABELS[provider], "bold"),
                    f" · {base.for_provider(provider).model_name}",
                    (marker, "dim"),
                ),
            )
        console.print(Padding(rows, (0, 0, 0, 2)))
        if len(available) == 1:
            missing = "ANTHROPIC_API_KEY" if available[0] == "google_genai" else "GEMINI_API_KEY"
            console.print(f"[dim]Add {missing} to .env to choose the other model.[/]")
            self._pause()
            return
        numbers = {provider: str(n) for n, provider in enumerate(available, 1)}
        choice = Prompt.ask(
            "Number",
            choices=list(numbers.values()),
            default=numbers.get(self.provider, "1") if self.provider else "1",
            show_choices=False,
            console=console,
        )
        self.provider = available[int(choice) - 1]

    def web_ui(self) -> None:
        self.console.print(
            "\n[dim]Starting the web UI. Press Ctrl+C to stop it and return here.[/]"
        )
        # Ctrl+C stops the server; a failed start (port in use) exits with its own message.
        with contextlib.suppress(KeyboardInterrupt, SystemExit, RuntimeError):
            self.launch_ui()
        self.console.print("[dim]Web UI stopped.[/]")
