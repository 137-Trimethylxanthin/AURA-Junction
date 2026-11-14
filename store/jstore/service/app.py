"""HTTP API of the lossless project store: upload and register files, ask questions, read drawings and evidence.

    uvicorn jstore.service.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

from agents.models.interface import Model
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.datastructures import Headers, QueryParams
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing_extensions import Annotated

from ..db import DB, apply_schema
from . import aura, store
from .config import ALLOWED_EXT, Settings
from .files import (Forbidden, Ingestor, NotFound, check_id, contained, extension, locate, new_id, register_path,
                    store_upload)
from .runs import Registry, Run

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class RegisterIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class AskIn(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=10)
    questions: list[Question] = Field(min_length=1, max_length=100)
    stream: bool = False  # /ask only: answer with the run's event stream instead of blocking


def sse(run: Run, events: AsyncIterator[dict]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for e in events:
            yield f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run.id})


# Files come from uploads and from the agent, i.e. from whoever wrote the drawing.  Only raster images render
# inline, everything else downloads, and nothing served from this origin may run script or be sniffed into HTML.
INLINE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SAFE = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}


def send_file(path: str | Path, name: str, inline: bool = True) -> FileResponse:
    mime = INLINE.get(Path(name).suffix.lower())
    return FileResponse(path, media_type=mime or "application/octet-stream",
                        filename=None if mime and inline else Path(name).name, headers=SAFE)


def authorised(token: str | None, scope: Scope, given: str | None = None) -> bool:
    """Bearer token when one is configured; `?token=` is accepted too because browsers cannot set headers on
    EventSource or <img>.  `given` is a token that came another way (the /aura/t/<token>/ path prefix)."""
    if token is None:
        return True
    if given is None:
        header = Headers(scope=scope).get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else QueryParams(scope["query_string"]).get("token", "")
    return secrets.compare_digest(given.encode(), token.encode())


class Guard:
    """Bearer-token check and request-body budget as pure ASGI middleware: both happen before any of the body is
    read, parsed or spooled to disk."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.token = app, settings.api_token
        self.limit = settings.max_upload_mb * 1024 * 1024 + 65536  # + multipart framing
        self.too_large = f"body larger than {settings.max_upload_mb} MB"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # the AURA frontend cannot send a header or a query parameter: its token is part of the base URL
        in_path = aura.path_token(scope)
        # /health answers a bare liveness itself; "/" is the static chat page (no data; every call it makes is authorised)
        if scope["path"] not in ("/health", "/") and not authorised(self.token, scope, in_path):
            return await JSONResponse({"detail": "missing or wrong token"}, 401,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        length = Headers(scope=scope).get("content-length", "")
        if length.isascii() and length.isdigit() and (len(length) > 18 or int(length) > self.limit):
            return await JSONResponse({"detail": self.too_large}, 413)(scope, receive, send)
        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise HTTPException(413, self.too_large)
            return message
        await self.app(scope, counted, send)


async def in_db(fn: Callable, *args, **kw):
    """Run fn(db, ...) on a fresh connection in a worker thread (the driver is synchronous)."""
    def call():
        with DB() as db:
            return fn(db, *args, **kw)
    return await asyncio.to_thread(call)


def create_app(settings: Settings | None = None, model: Model | None = None) -> FastAPI:
    """`model` replaces the configured model with a Model instance (used by the tests)."""
    settings = settings or Settings.from_env()
    if settings.apply_schema:  # idempotent; also rebuilds the q views and the reader role
        apply_schema()
    ingestor = Ingestor(settings)
    registry = Registry(settings, ingestor, model)
    uploads = settings.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # uvicorn runs lifespan shutdown only after open connections have closed, and an event stream closes only
        # when its run ends: cancel the runs the moment the stop signal arrives.
        loop, previous = asyncio.get_running_loop(), {}

        def on_signal(sig: int, frame: object) -> None:
            loop.call_soon_threadsafe(registry.stop)
            if callable(previous[sig]):
                previous[sig](sig, frame)
        if threading.current_thread() is threading.main_thread():
            previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            for sig in previous:
                signal.signal(sig, on_signal)
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            await registry.shutdown()
            ingestor.shutdown()

    docs = {} if settings.api_token is None else dict(docs_url=None, redoc_url=None, openapi_url=None)
    app = FastAPI(title="Lossless project store", lifespan=lifespan, **docs)
    app.state.registry, app.state.settings, app.state.ingestor = registry, settings, ingestor
    app.add_middleware(Guard, settings=settings)
    # A front end served from another origin (the AURA dev server on localhost) may call the API; the token is
    # still required.  Added after Guard, so it wraps it: preflights and 401s carry the CORS headers too.
    app.add_middleware(CORSMiddleware, allow_origin_regex=settings.cors_origin_regex, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["content-type", "accept", "authorization", "x-aura-presenter", "x-delete-token"],
                       max_age=600)
    app.add_middleware(aura.PathToken)     # outermost: no later layer, and no access log, sees a token in a path
    app.include_router(aura.router)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, e: NotFound) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, 404)

    @app.exception_handler(Forbidden)
    async def _forbidden(_: Request, e: Forbidden) -> JSONResponse:
        return JSONResponse({"detail": "path not allowed"}, 403)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception) -> JSONResponse:  # no stack traces to clients
        return JSONResponse({"detail": f"internal error: {type(e).__name__}"}, 500)

    @app.get("/health")
    async def health(request: Request) -> dict:
        if not authorised(settings.api_token, request.scope):  # liveness only; the rest is for token holders
            return dict(status="ok")
        try:
            counts = await in_db(lambda db: db.one(
                """select (select count(*) from jstore.source_file) files,
                          (select count(distinct drawing_key) from jstore.source_file) drawings,
                          (select count(*) from jstore.entity) entities"""))
            database = dict(ok=True, **counts)
        except Exception as e:  # noqa: BLE001
            database = dict(ok=False, error=type(e).__name__)
        return dict(status="ok" if database["ok"] else "degraded", database=database,
                    model=dict(name=settings.model if model is None else type(model).__name__,
                               reasoning_effort=settings.reasoning_effort, key_present=settings.api_key is not None,
                               endpoint=re.sub(r"//[^/@]*@", "//", settings.openai_base_url)),
                    tools=dict(sql=True, view_image=True, shell=settings.shell),
                    converters=dict(dwg2dxf=shutil.which("dwg2dxf") is not None),
                    auth=settings.api_token is not None,
                    runs=dict(total=len(registry.runs), active=len(registry.tasks), max_concurrent=settings.max_concurrent))

    ui = Path(__file__).resolve().parent / "ui" / "index.html"

    @app.get("/", include_in_schema=False)
    async def chat_page() -> HTMLResponse:
        csp = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://esm.sh; connect-src 'self' https://esm.sh; "
               "img-src 'self' blob: data:; frame-src 'self' blob:; style-src 'self' 'unsafe-inline'")
        return HTMLResponse(ui.read_text(encoding="utf-8"), headers={"Content-Security-Policy": csp, "Cache-Control": "no-cache"})

    # ---- files ----------------------------------------------------------------------------------------------
    def file_out(row: dict, ingesting: bool) -> dict:
        return dict(file_id=row["file_id"], name=Path(row["path"]).name, ext=row["format"], size=row["bytes"],
                    drawing=row["drawing_key"], project=row["project_id"], status=row["status"], ingesting=ingesting)

    @app.post("/files")
    async def upload(file: UploadFile) -> dict:
        ext = extension(file.filename or "")
        if ext is None:
            raise HTTPException(422, f"extension not allowed; accepted: {', '.join(sorted(ALLOWED_EXT))}")
        limit, size = settings.max_upload_mb * 1024 * 1024, 0
        tmp = uploads / f".incoming-{new_id()}.{ext}"
        try:
            with open(tmp, "wb") as out:  # Guard bounds the whole body; this is the exact limit on the file itself
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file larger than {settings.max_upload_mb} MB")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "empty file")
            row = await asyncio.to_thread(store_upload, settings, tmp, file.filename or tmp.name)
        finally:
            tmp.unlink(missing_ok=True)
        return file_out(row, ingestor.ensure(row))

    @app.post("/files/register")
    async def register(body: RegisterIn) -> dict:
        try:
            row = await asyncio.to_thread(register_path, settings, body.path)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return file_out(row, ingestor.ensure(row))

    @app.get("/files/{file_id}")
    async def file_status(file_id: str) -> dict:
        info = await in_db(store.file_info, check_id(file_id))
        if info is None:
            raise NotFound(file_id)
        return dict(info, ingesting=bool(ingestor.pending([file_id])))

    async def source_row(file_id: str) -> dict:
        row = await in_db(lambda db: db.one(
            "select file_id, path, meta from jstore.source_file where file_id = :f", f=check_id(file_id)))
        if row is None:
            raise NotFound(file_id)
        return row

    @app.get("/files/{file_id}/raw")
    async def raw(file_id: str) -> FileResponse:
        row = await source_row(file_id)
        path = locate(settings, row["path"], row["meta"])
        if path is None:
            raise NotFound(file_id)
        return send_file(path, Path(row["path"]).name, inline=False)

    async def page_image(file_id: str, page_no: int) -> Path:
        page = await in_db(lambda db: db.one(
            "select image from jstore.page where file_id = :f and page_no = :n", f=check_id(file_id), n=page_no))
        if page is None or not page["image"]:
            raise NotFound(file_id)
        path = contained(settings.data_dir, page["image"])
        if not path.is_file():
            raise NotFound(file_id)
        return path

    @app.get("/files/{file_id}/pages/{page_no}/image")
    async def page_render(file_id: str, page_no: int) -> FileResponse:
        path = await page_image(file_id, page_no)
        return send_file(path, path.name)

    # ---- drawings and evidence ------------------------------------------------------------------------------
    @app.get("/drawings")
    async def list_drawings(project: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        return await in_db(store.drawings, project or None, q or None, max(1, min(limit, 1000)), max(0, offset))

    @app.get("/drawings/{key}")
    async def get_drawing(key: str) -> dict:
        out = await in_db(store.drawing, key)
        if out is None:
            raise NotFound(key)
        return out

    @app.get("/drawings/{key}/entities")
    async def drawing_entities(key: str, kind: str | None = None, limit: int = 500, offset: int = 0) -> dict:
        return await in_db(store.entities, key, kind or None, max(1, min(limit, 5000)), max(0, offset))

    @app.get("/evidence")
    async def get_evidence(entity_ids: str) -> dict:
        if not re.fullmatch(r"\d{1,18}(,\d{1,18}){0,499}", entity_ids):
            raise HTTPException(422, "entity_ids: 1 to 500 comma-separated integers")
        return await in_db(store.evidence, [int(i) for i in entity_ids.split(",")])

    # ---- runs -----------------------------------------------------------------------------------------------
    @app.post("/ask")
    async def ask(body: AskIn):
        run = await asyncio.to_thread(registry.create, body.file_ids, body.questions)
        task = registry.start(run)
        if body.stream:
            return sse(run, registry.subscribe(run))
        await asyncio.wait({task})  # not `await task`: a run cancelled at shutdown still gets its answer below
        out = dict(run_id=run.id, status=run.status, answers=run.answers, usage=run.usage, seconds=run.seconds)
        if run.status == "error":  # the agent failed, not the request: a clean 502 with the reason
            return JSONResponse(dict(out, error=run.error), 502)
        return out

    @app.post("/runs", status_code=202)
    async def create_run(body: AskIn) -> dict:
        run = await asyncio.to_thread(registry.create, body.file_ids, body.questions)
        registry.start(run)
        return dict(run_id=run.id, status=run.status)

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        return registry.get(run_id).public()

    @app.get("/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        run = registry.get(run_id)
        last = request.headers.get("last-event-id", "")  # set by EventSource when it reconnects
        resume = last.isascii() and last.isdigit() and len(last) < 10
        return sse(run, registry.subscribe(run, int(last) if resume else after))

    def workspace(run: Run) -> Path:
        return registry.run_dir(run) / "ws"

    @app.get("/runs/{run_id}/artifacts")
    async def artifacts(run_id: str) -> dict:
        """Files the shell tool left behind (the copies of the inputs and hidden files are left out)."""
        ws, out = workspace(registry.get(run_id)), []
        for p in sorted(ws.rglob("*")) if ws.is_dir() else []:
            rel = p.relative_to(ws)
            if any(part.startswith(".") for part in rel.parts) or re.fullmatch(r"drawing(_\d+)?\.\w+", str(rel)):
                continue
            try:
                if p.is_file() and contained(ws, rel):
                    out.append(dict(name=str(rel), size=p.stat().st_size))
            except (Forbidden, OSError):
                continue
        return dict(run_id=run_id, artifacts=out)

    @app.get("/runs/{run_id}/artifacts/{name:path}")
    async def artifact(run_id: str, name: str) -> FileResponse:
        """A file in the run's shell directory, or `page-<file_id>-<n>.png`: the page render an evidence box refers to."""
        run = registry.get(run_id)
        if m := re.fullmatch(r"page-([0-9a-f]{16})-(\d{1,6})\.png", name):
            path = await page_image(m[1], int(m[2]))
            return send_file(path, path.name)
        ws = workspace(run)
        path = contained(ws, name)
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(ws.resolve()).parts):
            raise NotFound(name)
        return send_file(path, path.name)

    @app.get("/runs/{run_id}/views/{name}")
    async def view(run_id: str, name: str) -> FileResponse:
        """An image exactly as the model saw it (the `view` of a view_image tool_finished event)."""
        path = registry.run_dir(registry.get(run_id)) / "views" / name
        if not re.fullmatch(r"\d{1,9}\.png", name) or not path.is_file():
            raise NotFound(name)
        return send_file(path, name)

    return app


def __getattr__(name: str) -> FastAPI:
    """`uvicorn jstore.service.app:app` builds the app on first access, so importing this module (tests, tools)
    never reads the environment or touches the database."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
