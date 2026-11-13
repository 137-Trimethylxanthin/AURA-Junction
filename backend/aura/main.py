"""App factory `create_app()` (Modal: `aura.main:create_app`; local: uvicorn --factory).

Lifespan: volume reload (on Modal, startup only) → open DB → start the single writer task →
hourly cleanup loop → on shutdown flush the writer and commit the volume.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import statistics
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from aura import tools
from aura.budget import Budget, RequestRecord
from aura.cache import AnswerCache, CheckCache
from aura.config import BACKEND_DIR, Settings, get_settings, models_enabled
from aura.contract import CONTRACT_VERSION, AskRequest, DrawingSummary
from aura.gemini.client import GeminiClient, Transport
from aura.logging import AccessLogMiddleware, RequestLogFile, setup_logging
from aura.netutil import client_ip_hash, is_presenter
from aura.pipeline import Caller, PipelineError, Services, ask
from aura.store import Store, init_db
from aura.writer import Writer

log = logging.getLogger("aura.main")

SERVED_FILES = {"plan.svg", "original.png", "original-crop.png", "thumb.png"}


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"contract_version": CONTRACT_VERSION, "error": message, "code": code},
                        status_code=status)


def _on_modal() -> bool:
    try:
        import modal

        return not modal.is_local()
    except Exception:  # noqa: BLE001 - modal missing or not configured: not on Modal
        return False


def _volume():
    import modal

    return modal.Volume.from_name("aura-data")


def _eval_results_dirs(settings: Settings) -> list[Path]:
    return [settings.data_dir / "eval" / "results", BACKEND_DIR / "eval" / "results"]


def _full_set(settings: Settings) -> dict | None:
    for base in _eval_results_dirs(settings):
        p = base / "swiss.json"
        if p.is_file():
            try:
                r = json.loads(p.read_text())
            except ValueError:
                return None
            if all(k in r for k in ("correct", "gold_total")):
                return {"correct": r["correct"], "gold_total": r["gold_total"],
                        "measured_at": r.get("measured_at", ""), "source": "eval/swiss_full.py"}
    return None


def create_app(
    settings: Settings | None = None,
    *,
    transport: Transport | None = None,
    extra_drawings: list[tools.LoadedDrawing] | None = None,
    use_gemini: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()
    on_modal = _on_modal()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        volume = None
        if on_modal:
            volume = _volume()
            try:
                volume.reload()
            except Exception:
                log.exception("volume reload failed")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        init_db(settings.db_path)
        store = Store(settings.db_path)
        writer = Writer(settings.db_path, volume_commit=volume.commit if volume else None,
                        commit_every_s=settings.volume_commit_every_s)
        await writer.start()
        reqlog = RequestLogFile(settings.logs_dir)

        def sink(rec: RequestRecord) -> None:
            row = rec.as_dict()
            writer.submit(
                "INSERT INTO requests(t, day, model, purpose, bucket, ip_hash, input_tokens, "
                "output_tokens, cost_usd, latency_ms, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rec.t, rec.day, rec.model, rec.purpose, rec.bucket, rec.ip_hash,
                 rec.input_tokens, rec.output_tokens, rec.cost_usd, rec.latency_ms, rec.status))
            reqlog.write(row)
            logging.getLogger("aura.model").info("", extra={"fields": {"kind": "model", **row}})

        total, by_bucket, by_day = store.request_counts()
        budget = Budget(settings, persisted_total=total, persisted_by_bucket=by_bucket,
                        persisted_by_day=by_day, sink=sink)
        client = GeminiClient(settings, budget, transport) if use_gemini else None
        registry = tools.Registry(settings.drawing_dirs(),
                                  {d.id: d for d in extra_drawings or []})
        app.state.svc = Services(settings=settings, budget=budget, client=client,
                                 registry=registry, store=store, writer=writer,
                                 answers=AnswerCache(), checks=CheckCache(store, writer))
        cleanup = asyncio.create_task(_cleanup_loop(settings, writer), name="aura-cleanup")
        try:
            yield
        finally:
            cleanup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup
            await writer.stop()
            closer = getattr(client.transport if client and client._transport else None,
                             "aclose", None)
            if closer:
                await closer()

    app = FastAPI(title="AURA API", version=CONTRACT_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_origin_regex=settings.allowed_origin_regex or None,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["content-type", "x-aura-presenter", "x-delete-token"],
        max_age=600,
    )
    app.add_middleware(AccessLogMiddleware, salt=settings.ip_salt.get_secret_value())

    @app.exception_handler(PipelineError)
    async def _pipeline_error(_req: Request, e: PipelineError) -> JSONResponse:
        return _err(e.code, str(e), e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, e: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": jsonable_encoder(e.errors())}, status_code=422)

    @app.exception_handler(Exception)
    async def _internal(_req: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _err("internal", "Internal error.", 500)

    def svc(request: Request) -> Services:
        return request.app.state.svc

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        s = svc(request)
        db_ok = s.store is not None and s.store.ok()
        data_ok = settings.data_dir.is_dir()
        budget = s.budget.snapshot()
        drawings = len(s.registry.ids())
        ok = db_ok and data_ok
        body = {"ok": ok, "contract_version": CONTRACT_VERSION,
                "checks": {"db": db_ok, "data_dir": data_ok, "drawings": drawings,
                           "models": budget["models"],
                           "budget_left": budget["event"]["cap"] - budget["event"]["used"]}}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/drawings")
    async def list_drawings(request: Request) -> dict:
        s = svc(request)
        out = []
        for did in s.registry.ids():
            d = s.registry.get(did)
            if d is None:
                continue
            dr = d.drawing
            summ = {
                "id": d.id, "title": dr.get("title", d.id), "kind": dr.get("kind", "cad_dxf"),
                "status": dr.get("status", "ready"),
                "thumbnail": (dr.get("thumbnail") or (f"/drawings/{d.id}/thumb.png"
                              if d.file("thumb.png") else dr.get("images", {}).get("rebuilt", ""))),
                "credit": dr.get("credit"), "source": dr.get("source", d.source),
                "subtitle": dr.get("subtitle"), "facts": dr.get("facts"),
            }
            out.append(DrawingSummary.model_validate(summ).model_dump(exclude_none=True))
        body: dict = {"contract_version": CONTRACT_VERSION, "drawings": out}
        fs = _full_set(settings)
        if fs:
            body["full_set"] = fs
        return body

    @app.get("/drawings/{drawing_id}/status")
    async def drawing_status(drawing_id: str, request: Request):
        d = svc(request).registry.get(drawing_id)
        if d is None:
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        return {"contract_version": CONTRACT_VERSION, "id": d.id,
                "status": d.drawing.get("status", "ready")}

    @app.get("/drawings/{drawing_id}")
    async def get_drawing(drawing_id: str, request: Request):
        d = svc(request).registry.get(drawing_id)
        if d is None:
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        return d.public_drawing()

    @app.get("/drawings/{drawing_id}/{name}")
    async def drawing_file(drawing_id: str, name: str, request: Request):
        d = svc(request).registry.get(drawing_id)
        p = d.file(name) if d is not None and name in SERVED_FILES else None
        if p is None:
            return _err("bad_request", "No such file.", 404)
        return FileResponse(p, headers={"cache-control": "public, max-age=3600"})

    @app.post("/ask")
    async def post_ask(body: AskRequest, request: Request):
        s = svc(request)
        caller = Caller(ip_hash=client_ip_hash(request, settings),
                        presenter=is_presenter(request, settings))
        try:
            async with asyncio.timeout(settings.turn_deadline_s):
                return JSONResponse(await ask(s, body, caller))
        except TimeoutError:
            return _err("internal", "The answer took too long.", 504)

    @app.get("/budget")
    async def get_budget(request: Request) -> dict:
        snap = svc(request).budget.snapshot()
        return {"contract_version": CONTRACT_VERSION,
                "models": "on" if models_enabled() else "off",
                "event_used": snap["event"]["used"], "event_cap": snap["event"]["cap"],
                "day_used": snap["today"]["used"], "day_cap": snap["today"]["cap"]}

    @app.get("/stats")
    async def get_stats(request: Request) -> dict:
        s = svc(request)
        day = s.budget.day()
        raw = s.store.stats(day) if s.store else {"answers": [], "model_calls_today": 0}
        rows = raw["answers"]
        body: dict = {"contract_version": CONTRACT_VERSION, "answers_today": len(rows),
                      "model_calls_today": raw["model_calls_today"]}
        if rows:
            geo = sum(1 for r in rows if not r["calls"] and not r["check_calls"])
            body["geometry_only_share"] = {"value": round(geo / len(rows), 4), "n": len(rows)}
        checked = [float(r["cost_usd"] or 0) + float(r["check_cost_usd"] or 0) for r in rows
                   if r["check_calls"]]
        if checked:
            body["median_cost_per_checked_answer_usd"] = {
                "value": round(statistics.median(checked), 6), "n": len(checked)}
        return body

    return app


async def _cleanup_loop(settings: Settings, writer: Writer) -> None:
    """Hourly: drop answers, conversations and turns older than the upload TTL."""
    while True:
        await asyncio.sleep(settings.cleanup_every_s)
        cutoff = time.time() - settings.upload_ttl_s
        try:
            writer.submit("DELETE FROM answers WHERE t < ?", (cutoff,))
            writer.submit("DELETE FROM turns WHERE t < ?", (cutoff,))
            writer.submit("DELETE FROM conversations WHERE t < ?", (cutoff,))
        except RuntimeError:
            return
