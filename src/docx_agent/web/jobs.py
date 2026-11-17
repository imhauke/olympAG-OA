"""Background edit jobs for the web UI.

Each job runs the same LangGraph workflow as the CLI in a worker thread and streams node
updates into a small progress model the browser polls. A clarification interrupt pauses the
job until the user answers (resumed with ``Command(resume=...)``). Results — the edited
document included — stay in memory only, for the most recent jobs; nothing is stored on disk
except the metadata-only history.
"""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from docx_agent.agent.graph import AgentContext, AgentError, AgentState, build_graph, root_cause
from docx_agent.agent.models import Settings
from docx_agent.agent.session import DocumentSession
from docx_agent.web.diff import present
from docx_agent.web.history import History

Status = Literal["running", "question", "done", "failed", "error"]
ModelFactory = Callable[[Settings], BaseChatModel]

STEPS = ("Reading the document", "Editing", "Checking integrity", "Reviewing the result")
_NODE_STEP = {
    "load": 0,
    "agent": 1,
    "tools": 1,
    "clarify": 1,
    "retry": 1,
    "validate": 2,
    "review": 3,
    "save": 3,
}
KEEP_JOBS = 12


@dataclass
class Job:
    id: str
    filename: str
    instruction: str
    provider: str
    model: str
    session: DocumentSession
    created: float = field(default_factory=time.monotonic)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: Status = "running"
    step: int = 0
    detail: str = ""
    question: str | None = None
    message: str = ""
    result: dict[str, Any] | None = None
    output: bytes | None = None
    report: bytes | None = None
    seconds: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    runner: _Runner | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "filename": self.filename,
                "instruction": self.instruction,
                "provider": self.provider,
                "model": self.model,
                "status": self.status,
                "steps": list(STEPS),
                "step": self.step,
                "detail": self.detail,
                "question": self.question,
                "message": self.message,
                "result": self.result,
                "seconds": self.seconds,
                "downloadable": self.output is not None,
            }


class JobStore:
    """Runs jobs and keeps the most recent ones (with their in-memory results)."""

    def __init__(self, model_factory: ModelFactory, history: History) -> None:
        self.model_factory = model_factory
        self.history = history
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, *, data: bytes, filename: str, instruction: str, settings: Settings) -> Job:
        session = DocumentSession(data)  # raises PackageError for an unusable .docx
        model = self.model_factory(settings)  # raises ValueError for missing configuration
        job = Job(
            id=uuid.uuid4().hex,
            filename=filename,
            instruction=instruction,
            provider=settings.provider,
            model=settings.model_name,
            session=session,
        )
        job.runner = _Runner(job, model, settings, self)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > KEEP_JOBS:
                _, evicted = self._jobs.popitem(last=False)
                if evicted.runner is not None:
                    evicted.runner.cleanup()
        threading.Thread(
            target=job.runner.run, args=(AgentState(instruction=instruction),), daemon=True
        ).start()
        return job

    def answer(self, job: Job, answer: str) -> None:
        with job.lock:
            if job.status != "question":
                raise ValueError("This job is not waiting for an answer.")
            job.status, job.question, job.detail = "running", None, "Continuing with your answer"
        assert job.runner is not None
        threading.Thread(target=job.runner.run, args=(Command(resume=answer),), daemon=True).start()

    def record(self, job: Job) -> None:
        result = job.result or {}
        self.history.add(
            {
                "id": job.id,
                "filename": job.filename,
                "instruction": job.instruction[:500],
                "provider": job.provider,
                "model": job.model,
                "status": job.status,
                "changes": len(result.get("changes", [])),
                "seconds": job.seconds,
                "at": job.started_at,
            }
        )


class _Runner:
    def __init__(self, job: Job, model: BaseChatModel, settings: Settings, store: JobStore):
        self.job = job
        self.store = store
        self.graph = build_graph(model)
        self.workdir = Path(tempfile.mkdtemp(prefix="docx-agent-ui-"))
        self.context = AgentContext(
            session=job.session,
            output=self.workdir / "output.docx",
            max_retries=settings.max_retries,
            max_turns=settings.max_turns,
        )
        self.config: RunnableConfig = {
            "configurable": {"thread_id": job.id},
            "recursion_limit": 4 * settings.max_turns + 20,
        }

    def run(self, payload: AgentState | Command[Any]) -> None:
        job = self.job
        try:
            updates: Iterator[dict[str, Any]] = self.graph.stream(
                payload, self.config, context=self.context, stream_mode="updates"
            )
            for update in updates:
                if "__interrupt__" in update:
                    question = update["__interrupt__"][0].value["question"]
                    with job.lock:
                        job.status, job.question = "question", str(question)
                        job.detail = "Waiting for your answer"
                    return
                for node in update:
                    self._progress(node)
            self._finish(self.graph.get_state(self.config).values)
        except AgentError as exc:
            self._end("error", f"{exc}{root_cause(exc)}")
        except Exception as exc:  # unexpected: report, never crash the server
            self._end("error", f"Unexpected error: {type(exc).__name__}: {exc}")

    def _progress(self, node: str) -> None:
        edits = len(self.job.session.edits)
        details = {
            "load": "Indexing paragraphs and tables",
            "agent": f"{edits} edit{'s' if edits != 1 else ''} applied" if edits else "Planning",
            "tools": f"{edits} edit{'s' if edits != 1 else ''} applied",
            "retry": "Starting again from the original",
            "validate": "Comparing every part with the original",
            "review": "Checking the result against your instruction",
            "save": "Preparing your file",
        }
        with self.job.lock:
            self.job.step = max(self.job.step, _NODE_STEP.get(node, self.job.step))
            if node == "retry":
                self.job.step = 1
            self.job.detail = details.get(node, self.job.detail)

    def _finish(self, values: dict[str, Any]) -> None:
        job = self.job
        if values.get("status") == "saved":
            output = self.context.output.read_bytes()
            report = self.context.report_path.read_bytes()
            changes = [present(c) for c in (values.get("integrity") or {}).get("changes", [])]
            removed = sorted({k for e in job.session.edits for k in e.removed})
            with job.lock:
                job.output, job.report = output, report
                job.result = {
                    "summary": values.get("reason") or values.get("summary", ""),
                    "changes": changes,
                    "edits": len(job.session.edits),
                    "removed": removed,
                    "retries": values.get("retries", 0),
                    "integrity": "verified",
                }
            self._end("done", "")
        else:
            reason = str(values.get("reason") or "The edit could not be completed.")
            violations = list((values.get("integrity") or {}).get("violations", []))
            if reason.startswith("Validation failed"):
                reason = "The integrity check rejected the edit, so nothing was saved."
            elif reason.startswith("Review rejected: "):
                reason = reason.removeprefix("Review rejected: ")
            with job.lock:
                job.result = {"violations": violations}
            self._end("failed", reason)

    def _end(self, status: Status, message: str) -> None:
        job = self.job
        with job.lock:
            job.status, job.message = status, message
            job.question = None
            job.seconds = round(time.monotonic() - job.created, 1)
            if status == "done":
                job.step = len(STEPS)
        self.cleanup()
        self.store.record(job)

    def cleanup(self) -> None:
        """Remove the temporary output folder; results live only in memory."""
        for path in self.workdir.glob("*"):
            path.unlink(missing_ok=True)
        if self.workdir.exists():
            self.workdir.rmdir()
