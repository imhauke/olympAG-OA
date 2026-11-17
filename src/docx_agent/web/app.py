"""FastAPI app for the local UI. Bind to 127.0.0.1 only; see `docx-agent ui`.

Local-only hardening, because the server spends the user's API credit:
* Host-header allow-list (blocks DNS-rebinding pages from reaching the API).
* Mutating requests must carry the ``X-Docx-Agent`` header. Browsers cannot add it to a
  cross-site request without a CORS preflight, which this app never grants, so another open
  website cannot start edits.
* Uploads are size-limited, parsed in memory and never written to disk.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from importlib.resources import files
from pathlib import PurePath
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from docx_agent import __version__
from docx_agent.agent.models import PROVIDER_LABELS, Provider, Settings, create_model
from docx_agent.document.package import PackageError
from docx_agent.web.history import History, history_path
from docx_agent.web.jobs import Job, JobStore, ModelFactory

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_INSTRUCTION_CHARS = 4_000
STATIC = files("docx_agent.web") / "static"


def _require_ui(x_docx_agent: Annotated[str | None, Header()] = None) -> None:
    if x_docx_agent != "1":
        raise HTTPException(403, "Requests must come from the docx-agent UI.")


def _clean_filename(name: str | None) -> str:
    base = PurePath((name or "document.docx").replace("\\", "/")).name
    base = "".join(c for c in unicodedata.normalize("NFC", base) if c.isprintable()).strip()
    return base[:120] or "document.docx"


def _download_name(filename: str) -> str:
    stem = re.sub(r"\.docx$", "", filename, flags=re.IGNORECASE) or "document"
    return f"{stem}.edited.docx"


def _attachment(name: str) -> dict[str, str]:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    return {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name)}"
    }


async def _read_upload(file: UploadFile, instruction: str) -> tuple[str, bytes, str]:
    filename = _clean_filename(file.filename)
    if not filename.lower().endswith(".docx"):
        raise HTTPException(415, "Choose a Word document (.docx).")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"The file is larger than {MAX_UPLOAD_BYTES // 2**20} MB.")
    text = instruction.strip()
    if not text:
        raise HTTPException(422, "Describe the change you want.")
    if len(text) > MAX_INSTRUCTION_CHARS:
        raise HTTPException(422, f"Keep the instruction under {MAX_INSTRUCTION_CHARS} characters.")
    return filename, data, text


def create_app(
    *,
    settings_factory: Callable[[], Settings] = Settings,
    model_factory: ModelFactory = create_model,
    history: History | None = None,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
) -> FastAPI:
    app = FastAPI(title="docx-agent", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    store = JobStore(model_factory, history or History(history_path()))
    _config_routes(app, settings_factory)
    _job_routes(app, store, settings_factory)
    _file_routes(app, store)
    _history_routes(app, store)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(STATIC / "index.html"), headers={"Cache-Control": "no-store"})

    @app.get("/docs", include_in_schema=False)
    def docs() -> FileResponse:
        return FileResponse(str(STATIC / "docs.html"), headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


def _config_routes(app: FastAPI, settings_factory: Callable[[], Settings]) -> None:
    @app.get("/api/config")
    def config() -> dict[str, Any]:
        base = settings_factory()
        available = base.available_providers()
        providers = [
            {
                "id": provider,
                "label": PROVIDER_LABELS[provider],
                "model": base.for_provider(provider).model_name,
            }
            for provider in available
        ]
        default: str | None = base.provider if base.provider in available else None
        if default is None and available:
            default = available[0]
        return {
            "providers": providers,
            "default": default,
            "maxUploadMb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "maxInstruction": MAX_INSTRUCTION_CHARS,
            "version": __version__,
        }


def _job_or_404(store: JobStore, job_id: str) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "This edit is no longer in memory. Start a new one.")
    return job


def _job_routes(app: FastAPI, store: JobStore, settings_factory: Callable[[], Settings]) -> None:
    @app.post("/api/jobs", dependencies=[Depends(_require_ui)], status_code=201)
    async def start_job(
        file: Annotated[UploadFile, File()],
        instruction: Annotated[str, Form()],
        provider: Annotated[Provider, Form()],
    ) -> dict[str, Any]:
        filename, data, text = await _read_upload(file, instruction)
        base = settings_factory()
        if provider not in base.available_providers():
            raise HTTPException(422, f"{PROVIDER_LABELS[provider]} has no API key configured.")
        try:
            job = store.start(
                data=data, filename=filename, instruction=text, settings=base.for_provider(provider)
            )
        except PackageError as exc:
            raise HTTPException(422, f"This file can't be read as a Word document: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return job.snapshot()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        return _job_or_404(store, job_id).snapshot()

    @app.post("/api/jobs/{job_id}/answer", dependencies=[Depends(_require_ui)])
    def answer(job_id: str, payload: dict[str, str]) -> dict[str, Any]:
        job = _job_or_404(store, job_id)
        text = (payload.get("answer") or "").strip()
        if not text:
            raise HTTPException(422, "Type an answer first.")
        try:
            store.answer(job, text[:MAX_INSTRUCTION_CHARS])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return job.snapshot()


def _file_routes(app: FastAPI, store: JobStore) -> None:
    @app.get("/api/jobs/{job_id}/document")
    def download(job_id: str) -> Response:
        job = _job_or_404(store, job_id)
        if job.output is None:
            raise HTTPException(409, "There is no edited document for this job.")
        return Response(
            job.output,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers=_attachment(_download_name(job.filename)),
        )

    @app.get("/api/jobs/{job_id}/report")
    def report(job_id: str) -> Response:
        job = _job_or_404(store, job_id)
        if job.report is None:
            raise HTTPException(409, "There is no report for this job.")
        stem = _download_name(job.filename).removesuffix(".docx")
        return Response(
            job.report, media_type="application/json", headers=_attachment(f"{stem}.report.json")
        )


def _history_routes(app: FastAPI, store: JobStore) -> None:
    @app.get("/api/history")
    def history_entries() -> list[dict[str, Any]]:
        return store.history.entries()

    @app.delete("/api/history", dependencies=[Depends(_require_ui)], status_code=204)
    def clear_history() -> Response:
        store.history.clear()
        return Response(status_code=204)
