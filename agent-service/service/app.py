"""FastAPI front end of the drawing question-answering agent.

    uvicorn service.app:app --host 127.0.0.1 --port 8090
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
from typing import AsyncIterator

from agents.models.interface import Model
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.datastructures import Headers, QueryParams
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing_extensions import Annotated

from .config import ALLOWED_EXT, SANDBOX_MODES, Settings, model_status, sandbox_available
from .runs import Registry, Run
from .workspace import FileStore, Forbidden, NotFound, extension

# Room/object candidates are injected by the AURA backend, so questions can be long.
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24000)]


class RegisterIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    precomputed_dir: str | None = Field(default=None, max_length=4096)


class AskIn(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=10)
    questions: list[Question] = Field(min_length=1, max_length=100)
    stream: bool = False  # /ask only: answer with the run's event stream instead of blocking


def sse(run: Run, events: AsyncIterator[dict]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for e in events:
            yield f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run.id})


# Files come from uploads and from the agent, i.e. from whoever wrote the drawing.  Only images render inline,
# everything else downloads, and nothing served from this origin may run script or be sniffed into HTML.
INLINE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}
SAFE = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}


def send_file(path: str | Path, name: str, inline: bool = True) -> FileResponse:
    mime = INLINE.get(Path(name).suffix.lower())
    return FileResponse(path, media_type=mime or "application/octet-stream",
                        filename=None if mime and inline else Path(name).name, headers=SAFE)


def authorised(token: str | None, scope: Scope) -> bool:
    """Bearer token when one is configured; `?token=` is accepted too because browsers cannot set headers on
    EventSource or <img>."""
    if token is None:
        return True
    header = Headers(scope=scope).get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else QueryParams(scope["query_string"]).get("token", "")
    return secrets.compare_digest(given.encode(), token.encode())


class Guard:
    """Bearer-token check and request-body budget as pure ASGI middleware: both happen before any of the body is
    read, parsed or spooled to disk, and the budget holds with or without a Content-Length."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.token = app, settings.api_token
        self.limit = settings.max_upload_mb * 1024 * 1024 + 65536  # + multipart framing
        self.too_large = f"body larger than {settings.max_upload_mb} MB"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # /health answers a bare liveness itself; "/" is the static chat page (no data, every API call it makes is authorised)
        if scope["path"] not in ("/health", "/") and not authorised(self.token, scope):
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
                if seen > self.limit:  # stops the body parser; FastAPI passes an HTTPException through
                    raise HTTPException(413, self.too_large)
            return message
        await self.app(scope, counted, send)


def create_app(settings: Settings | None = None, model: Model | None = None) -> FastAPI:
    """`model` replaces the configured model name with a Model instance (used by the tests)."""
    settings = settings or Settings.from_env()
    ok, detail = sandbox_available(settings.sandbox, settings.docker_image)
    if not ok:  # never fall back silently to a weaker mode than the operator asked for
        raise RuntimeError(f"JUNCTION_SANDBOX={settings.sandbox} is not usable here: {detail}")
    files = FileStore(settings.data_dir)
    registry = Registry(settings, files, model)

    available = {m: sandbox_available(m, settings.docker_image)[0] for m in SANDBOX_MODES}  # once: it shells out

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # uvicorn runs lifespan shutdown only after open connections have closed, and an event stream or a
        # blocking /ask closes only when its run ends.  So cancel the runs the moment the stop signal arrives
        # (chained in front of the server's own handler), not in the teardown below.
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

    # the interactive docs describe every route: not for anonymous eyes once a token is required
    docs = {} if settings.api_token is None else dict(docs_url=None, redoc_url=None, openapi_url=None)
    app = FastAPI(title="Junction drawing QA", lifespan=lifespan, **docs)
    app.state.registry, app.state.settings = registry, settings
    app.add_middleware(Guard, settings=settings)

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
        if not authorised(settings.api_token, request.scope):  # liveness only; the configuration is for token holders
            return dict(status="ok")
        injected = dict(name=type(model).__name__, provider="injected", key_present=True)
        return dict(
            status="ok",
            model=await model_status(settings) if model is None else injected,
            sandbox=dict(mode=settings.sandbox, detail=detail, available=available),
            converters=dict(tesseract=shutil.which("tesseract") is not None),
            auth=settings.api_token is not None,
            runs=dict(total=len(registry.runs), active=len(registry.tasks), max_concurrent=settings.max_concurrent))

    # ---- chat page --------------------------------------------------------------------------------------------
    ui = Path(__file__).resolve().parent / "ui" / "index.html"

    @app.get("/", include_in_schema=False)
    async def chat_page() -> HTMLResponse:
        # React comes from esm.sh (no build step); everything else stays on this origin
        csp = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://esm.sh; connect-src 'self' https://esm.sh; "
               "img-src 'self' blob: data:; frame-src 'self' blob:; style-src 'self' 'unsafe-inline'")
        return HTMLResponse(ui.read_text(encoding="utf-8"), headers={"Content-Security-Policy": csp, "Cache-Control": "no-cache"})

    # ---- files ----------------------------------------------------------------------------------------------
    @app.post("/files")
    async def upload(file: UploadFile) -> dict:
        ext = extension(file.filename or "")
        if ext is None:
            raise HTTPException(422, f"extension not allowed; accepted: {', '.join(sorted(ALLOWED_EXT))}")
        limit = settings.max_upload_mb * 1024 * 1024
        fid, dst = files.new_blob(ext)
        size = 0
        try:
            with open(dst, "wb") as out:  # Guard bounds the whole body; this is the exact limit on the file itself
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file larger than {settings.max_upload_mb} MB")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "empty file")
        except BaseException:
            files.discard(fid)
            raise
        meta = files.commit(fid, file.filename or dst.name, dst, registered=False)
        return dict(file_id=meta.file_id, name=meta.original_name, ext=meta.ext, size=meta.size)

    @app.post("/files/register")
    async def register(body: RegisterIn) -> dict:
        try:
            meta = files.register(body.path, settings.register_root, precomputed_dir=body.precomputed_dir)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return dict(file_id=meta.file_id, name=meta.original_name, ext=meta.ext, size=meta.size)

    @app.get("/files/{file_id}/raw")
    async def raw(file_id: str) -> FileResponse:
        meta = files.get(file_id)
        return send_file(meta.path, meta.original_name, inline=False)

    @app.delete("/files/{file_id}")
    async def delete_file(file_id: str) -> dict:
        fid = files.delete(file_id)
        return dict(deleted=fid)

    # ---- runs -----------------------------------------------------------------------------------------------
    @app.post("/ask")
    async def ask(body: AskIn):
        run = registry.create(body.file_ids, body.questions)
        task = registry.start(run)
        if body.stream:
            return sse(run, registry.subscribe(run))
        await asyncio.wait({task})  # not `await task`: a run cancelled at shutdown still gets its answer below
        out = dict(run_id=run.id, status=run.status, answers=run.answers, usage=run.usage, seconds=run.seconds)
        if run.status != "done":  # the agent failed or was cancelled: a clean 502 with the reason
            return JSONResponse(dict(out, error=run.error), 502)
        return out

    @app.post("/runs", status_code=202)
    async def create_run(body: AskIn) -> dict:
        run = registry.create(body.file_ids, body.questions)
        registry.start(run)
        return dict(run_id=run.id, status=run.status)

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict:
        run = await registry.cancel(run_id)
        return dict(id=run.id, status=run.status)

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        return registry.get(run_id).public()

    @app.get("/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        run = registry.get(run_id)
        last = request.headers.get("last-event-id", "")  # set by EventSource when it reconnects
        resume = last.isascii() and last.isdigit() and len(last) < 10  # isdigit() alone admits what int() rejects
        return sse(run, registry.subscribe(run, int(last) if resume else after))

    @app.get("/runs/{run_id}/artifacts")
    async def artifacts(run_id: str) -> dict:
        return dict(run_id=run_id, artifacts=registry.workspace(registry.get(run_id)).artifacts())

    @app.get("/runs/{run_id}/artifacts/{name:path}")
    async def artifact(run_id: str, name: str) -> FileResponse:
        """Any file in the run's workspace: what the agent wrote, and the inputs under their workspace names
        (evidence refers to images by those names).  The listing above leaves the inputs out."""
        ws = registry.workspace(registry.get(run_id))
        path = ws.resolve(name)
        if any(part.startswith(".") for part in path.relative_to(ws.root.resolve()).parts):
            raise NotFound(name)
        return send_file(path, path.name)

    @app.get("/runs/{run_id}/views/{name}")
    async def view(run_id: str, name: str) -> FileResponse:
        """An image exactly as the model saw it (the `view` of a view_image tool_finished event)."""
        path = registry.dir / registry.get(run_id).id / "views" / name
        if not re.fullmatch(r"\d{1,9}\.(png|jpg)", name) or not path.is_file():
            raise NotFound(name)
        return send_file(path, name)

    return app


def __getattr__(name: str) -> FastAPI:
    """`uvicorn service.app:app` builds the app on first access, so importing this module (tests, tools) never
    reads the environment or touches the data dir."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
