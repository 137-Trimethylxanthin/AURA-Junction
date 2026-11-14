"""App factory `create_app()` (Modal: `aura.main:create_app`; local: uvicorn --factory).

Lifespan: volume reload (on Modal, startup only) → open DB → start the single writer task →
hourly cleanup loop → on shutdown flush the writer and commit the volume.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import statistics
import tempfile
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.types import ASGIApp, Receive, Scope, Send

from aura import tools, uploads
from aura.agent_client import AgentClient
from aura.budget import AGENT_PURPOSES, RASTER_PURPOSES, Budget, Ledger, RequestRecord
from aura.cache import AnswerCache, CheckCache
from aura.config import Settings, get_settings, kill_file, models_enabled
from aura.contract import (
    CONTRACT_VERSION,
    AskRequest,
    ChatRequest,
    DrawingSummary,
    ProjectBundle,
    ProjectList,
    RewriteRequest,
)
from aura.csvout import evidence_csv
from aura.gemini.client import GeminiClient, Meter, Transport
from aura.jobs import Jobs
from aura.logging import AccessLogMiddleware, RequestLogFile, setup_logging
from aura.mcp import build_mcp
from aura.netutil import client_ip_hash, is_presenter
from aura.pipeline import Caller, PipelineError, Services, ask, chat_events
from aura.sessions import router as sessions_router
from aura.solvers.pdf import infer_floor_label_and_order
from aura.store import Store, init_db
from aura.writer import Writer

log = logging.getLogger("aura.main")

SERVED_FILES = {"plan.svg", "original.png", "thumb.png"}  # allow-list: no other names
LIBRARY_CACHE = "public, max-age=3600"
NO_STORE = "private, no-store"


def _err(code: str, message: str, status: int, **extra) -> JSONResponse:
    return JSONResponse({"contract_version": CONTRACT_VERSION, "error": message, "code": code,
                         **extra}, status_code=status)


def _check_deploy_settings(settings: Settings) -> None:
    """Fail closed on a deploy: a public IP salt makes the hashed IPs reversible (M3), and an
    implicit budget seed would ignore requests made before this database existed (H3)."""
    if settings.ip_salt.get_secret_value() == "aura-local-salt":
        raise RuntimeError("AURA_IP_SALT must be set (Modal secret aura-presenter).")
    if not os.environ.get("AURA_BUDGET_SEED", "").strip():
        raise RuntimeError("AURA_BUDGET_SEED must be set explicitly at deploy time: the number of "
                           "Gemini requests already used on this key (see Google's console).")


def _dir_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.stat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _spent(*meters) -> dict:
    """Model requests already sent (counted even when the step then failed)."""
    return {"calls": sum(m.calls for m in meters),
            "cost_usd": round(sum(m.cost_usd for m in meters), 8)}


def _on_modal() -> bool:
    try:
        import modal

        return not modal.is_local()
    except Exception:  # noqa: BLE001 - modal missing or not configured: not on Modal
        return False


def _volume():
    import modal

    return modal.Volume.from_name("aura-data")


def _synthesize_projects(s: Services) -> list[ProjectBundle]:
    """Merge persisted SQLite `projects` with auto-detected multi-floor groups (`project_id` / IFC stems)."""
    persisted_rows = s.store.project_rows() if s.store else []
    bundles: list[ProjectBundle] = []
    seen_ids: set[str] = set()
    claimed_drawings: set[str] = set()

    for row in persisted_rows:
        try:
            floors = sorted(
                row["floors"],
                key=lambda f: (float(f.get("order", 0)), str(f.get("label", "")), str(f.get("drawing_id", ""))),
            )
            pb = ProjectBundle.model_validate({
                "id": row["id"],
                "title": row["title"],
                "floors": floors,
                "auto_matched": row["auto_matched"],
                "created_at": _iso(float(row["t"])) if row.get("t") else None,
            })
        except ValidationError:
            continue
        bundles.append(pb)
        seen_ids.add(pb.id)
        for fl in pb.floors:
            claimed_drawings.add(fl.drawing_id)

    # Auto-synthesize bundles from library + ready upload drawings sharing explicit `project_id` or IFC stem
    candidate_ids = list(s.registry.library_ids())
    if s.store is not None:
        for ur in s.store.upload_rows():
            if ur.get("status") == "ready" and ur["id"] not in candidate_ids:
                candidate_ids.append(ur["id"])

    by_proj: dict[str, list[tools.LoadedDrawing]] = defaultdict(list)
    by_ifc_stem: dict[str, list[tools.LoadedDrawing]] = defaultdict(list)
    for did in candidate_ids:
        if did in claimed_drawings:
            continue
        d = s.registry.get(did)
        if d is None:
            continue
        pid = d.drawing.get("project_id")
        if pid:
            by_proj[str(pid)].append(d)
        elif did.startswith("ifc-") and "--" in did:
            stem = did.split("--", 1)[0]
            by_ifc_stem[stem].append(d)

    for pid, members in by_proj.items():
        if pid in seen_ids or len(members) < 2:
            continue
        floors = []
        for idx, m in enumerate(members, 1):
            dr = m.drawing
            lbl = dr.get("floor_label")
            ord_v = dr.get("floor_order")
            if lbl is None or ord_v is None:
                inf_l, inf_o = infer_floor_label_and_order(f"{dr.get('title', '')} {dr.get('subtitle', '')}", idx)
                lbl = lbl or inf_l
                ord_v = inf_o if ord_v is None else ord_v
            floors.append({"drawing_id": m.id, "label": str(lbl), "order": ord_v})
        floors.sort(key=lambda f: (float(f["order"]), f["label"], f["drawing_id"]))
        base_title = str(members[0].drawing.get("title") or pid).split(" · ")[0].strip() or pid
        try:
            pb = ProjectBundle.model_validate({
                "id": pid,
                "title": base_title[:200],
                "floors": floors,
                "auto_matched": True,
            })
            bundles.append(pb)
            seen_ids.add(pid)
        except ValidationError:
            continue

    for stem, members in by_ifc_stem.items():
        pid = f"proj-{stem}"
        if pid in seen_ids or len(members) < 2:
            continue
        floors = []
        for idx, m in enumerate(members, 1):
            dr = m.drawing
            sub = str(dr.get("subtitle") or m.id.split("--", 1)[-1])
            inf_l, inf_o = infer_floor_label_and_order(sub, idx)
            floors.append({
                "drawing_id": m.id,
                "label": str(dr.get("floor_label") or inf_l),
                "order": dr.get("floor_order") if dr.get("floor_order") is not None else inf_o,
            })
        floors.sort(key=lambda f: (float(f["order"]), f["label"], f["drawing_id"]))
        base_title = str(members[0].drawing.get("title") or stem).split("—")[0].split("·")[0].strip() or stem
        try:
            pb = ProjectBundle.model_validate({
                "id": pid,
                "title": base_title[:200],
                "floors": floors,
                "auto_matched": True,
            })
            bundles.append(pb)
            seen_ids.add(pid)
        except ValidationError:
            continue

    return bundles


def _same_project(s: Services, did_a: str, did_b: str, scope: Any) -> bool:
    """Allow a multi-floor project conversation thread to switch floors without 400."""
    if did_a == did_b:
        return True
    if scope is not None and getattr(scope, "kind", None) == "project":
        dids = set(getattr(scope, "drawing_ids", None) or [])
        if did_a in dids and did_b in dids:
            return True
    da, db = s.registry.get(did_a), s.registry.get(did_b)
    if da is not None and db is not None:
        pa, pb = da.drawing.get("project_id"), db.drawing.get("project_id")
        if pa and pa == pb:
            return True
    for proj in _synthesize_projects(s):
        member_ids = {f.drawing_id for f in proj.floors}
        if did_a in member_ids and did_b in member_ids:
            return True
    return False


def create_app(
    settings: Settings | None = None,
    *,
    transport: Transport | None = None,
    sidecar_transport: Any | None = None,
    extra_drawings: list[tools.LoadedDrawing] | None = None,
    use_gemini: bool = True,
    job_options: dict | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()
    on_modal = _on_modal()
    if on_modal:
        _check_deploy_settings(settings)
    holder: dict[str, Services] = {}
    mcp_app = build_mcp(lambda: holder["svc"]).http_app(path="/", stateless_http=True,
                                                        json_response=True)

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
            # The DB row was written by the Ledger before the request was sent (H3).
            row = rec.as_dict()
            reqlog.write({k: v for k, v in row.items() if k != "ip_hash"})  # audit file: no IP data
            logging.getLogger("aura.model").info("", extra={"fields": {"kind": "model", **row}})

        # Jobs live in this process only: anything still queued/processing was cut off by a
        # restart and would otherwise poll as "processing" until it expires.
        await writer.execute(
            "UPDATE drawings SET status='failed', progress=NULL, error=? "
            "WHERE status IN ('queued','processing')",
            ("The server restarted while this file was being processed; upload it again.",))
        now_ts = time.time()
        total, by_bucket, by_day = store.request_counts()
        recent_ip, raster_used = store.recent_limits(now_ts, AGENT_PURPOSES, RASTER_PURPOSES)
        today_str = time.strftime("%Y-%m-%d", time.gmtime(now_ts))
        recent_ip_runs, runs_today = store.recent_agent_runs(now_ts, today_str)
        budget = Budget(settings, persisted_total=total, persisted_by_bucket=by_bucket,
                        persisted_by_day=by_day, sink=sink, recent_ip_agent=recent_ip,
                        recent_ip_agent_runs=recent_ip_runs,
                        agent_runs_today=(today_str, runs_today),
                        raster_public_used=raster_used,
                        ledger=Ledger(settings.db_path,
                                      flush=volume.commit if volume else None))
        client = GeminiClient(settings, budget, transport) if use_gemini else None
        registry = tools.Registry(settings.drawing_dirs(),
                                  {d.id: d for d in extra_drawings or []},
                                  uploads_dir=settings.uploads_dir)
        agent = (
            AgentClient(settings, budget, client, registry, transport=sidecar_transport)
            if (settings.sidecar_url or sidecar_transport is not None)
            else None
        )
        jobs = Jobs(writer, **(job_options or {}))
        jobs.on_done = registry.evict
        jobs.after_raster = _raster_hook(client)
        app.state.jobs = jobs
        app.state.svc = Services(
            settings=settings,
            budget=budget,
            client=client,
            registry=registry,
            store=store,
            writer=writer,
            answers=AnswerCache(ttl_s=settings.upload_ttl_s, store=store, writer=writer),
            checks=CheckCache(store, writer),
            agent=agent,
        )
        holder["svc"] = app.state.svc
        cleanup = asyncio.create_task(_cleanup_loop(app.state.svc), name="aura-cleanup")
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            cleanup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup
            await jobs.cancel_all()
            await writer.stop()
            if budget.ledger is not None:
                budget.ledger.close()
            if agent is not None:
                await agent.aclose()
            closer = getattr(client.transport if client and client._transport else None,
                             "aclose", None)
            if closer:
                await closer()

    docs = {} if not on_modal else {"docs_url": None, "redoc_url": None, "openapi_url": None}  # L3
    app = FastAPI(title="AURA API", version=CONTRACT_VERSION, lifespan=lifespan, **docs)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_origin_regex=settings.allowed_origin_regex or None,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-aura-presenter", "x-delete-token", "x-aura-client"],
        max_age=600,
    )
    app.include_router(sessions_router)
    app.mount("/mcp", mcp_app)
    app.add_middleware(BodyCapMiddleware, cap=uploads.BODY_CAP)
    app.add_middleware(DefaultNoStoreMiddleware)
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
        ids = s.registry.library_ids()
        drawings = sum(1 for i in ids if s.registry.get(i) is not None)
        skipped = len(s.registry.skipped & set(ids))
        ok = db_ok and data_ok
        body: dict[str, Any] = {
            "ok": ok,
            "contract_version": CONTRACT_VERSION,
            "answerer": settings.answerer,
            "checks": {
                "db": db_ok,
                "data_dir": data_ok,
                "drawings": drawings,
                "drawings_skipped": skipped,
                "models": budget["models"],
                # None = unlimited (AURA_EVENT_CAP=0)
                "budget_left": (budget["event"]["cap"] - budget["event"]["used"]
                                if budget["event"]["cap"] > 0 else None),
                **(s.store.spend_totals() if s.store else {}),
            },
        }
        if s.agent is not None:
            body["agent_ok"] = await s.agent.health_ok()
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/drawings")
    async def list_drawings(request: Request) -> dict:
        s = svc(request)
        out = []
        for did in s.registry.library_ids():
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
                "source_ext": dr.get("source_ext"),
                "project_id": dr.get("project_id"),
                "floor_label": dr.get("floor_label"),
                "floor_order": dr.get("floor_order"),
            }
            try:
                out.append(DrawingSummary.model_validate(summ).model_dump(exclude_none=True))
            except ValueError:  # a malformed entry is skipped, never a 500 for everyone
                s.registry.skipped.add(did)
                log.warning("skipped a drawing with an invalid summary")
        return {"contract_version": CONTRACT_VERSION, "drawings": out}

    @app.get("/projects")
    async def list_projects(request: Request) -> dict:
        s = svc(request)
        projects = _synthesize_projects(s)
        return ProjectList(projects=projects).model_dump(exclude_none=True)

    @app.post("/projects")
    async def create_or_update_project(request: Request):
        s = svc(request)
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            return _err("bad_request", "Invalid JSON body.", 400)
        if not isinstance(raw, dict):
            return _err("bad_request", "Expected a JSON object.", 400)
        now = time.time()
        pid = str(raw.get("id") or f"proj-{uuid.uuid4().hex[:12]}").strip()
        raw_floors = raw.get("floors")
        if raw_floors is None and isinstance(raw.get("drawing_ids"), list):
            raw_floors = []
            for idx, did in enumerate(raw["drawing_ids"], 1):
                did_s = str(did)
                d_obj = s.registry.get(did_s)
                if d_obj is None:
                    return _err("unknown_drawing", f"Unknown drawing '{did_s}'.", 404)
                dr = d_obj.drawing
                lbl = dr.get("floor_label")
                ord_v = dr.get("floor_order")
                if lbl is None or ord_v is None:
                    inf_l, inf_o = infer_floor_label_and_order(
                        f"{dr.get('title', '')} {dr.get('subtitle', '')}", idx - 1
                    )
                    lbl = lbl or inf_l
                    ord_v = inf_o if ord_v is None else ord_v
                raw_floors.append({"drawing_id": did_s, "label": str(lbl), "order": ord_v})
        if not isinstance(raw_floors, list) or len(raw_floors) < 2:
            return _err("bad_request", "A project bundle requires at least 2 floors.", 400)
        try:
            pb = ProjectBundle.model_validate({
                "id": pid,
                "title": raw.get("title", ""),
                "floors": raw_floors,
                "auto_matched": bool(raw.get("auto_matched", False)),
                "created_at": raw.get("created_at") or _iso(now),
            })
        except ValidationError as e:
            return JSONResponse({"detail": jsonable_encoder(e.errors())}, status_code=422)
        for fl in pb.floors:
            if s.registry.get(fl.drawing_id) is None:
                return _err("unknown_drawing", f"Unknown drawing '{fl.drawing_id}'.", 404)
        sorted_floors = sorted(pb.floors, key=lambda f: (float(f.order), f.label, f.drawing_id))
        pb = pb.model_copy(update={"floors": sorted_floors})
        dumped = pb.model_dump(exclude_none=True)
        if s.writer is not None:
            await s.writer.execute(
                "INSERT OR REPLACE INTO projects(id, t, title, floors, auto_matched, body) "
                "VALUES (?,?,?,?,?,?)",
                (
                    pb.id,
                    now,
                    pb.title,
                    json.dumps([f.model_dump() for f in pb.floors], ensure_ascii=False),
                    1 if pb.auto_matched else 0,
                    json.dumps(dumped, ensure_ascii=False),
                ),
            )
        return dumped

    @app.delete("/projects/{project_id}")
    async def delete_project(project_id: str, request: Request):
        s = svc(request)
        row = s.store.project_row(project_id) if s.store else None
        if row is None:
            return _err("not_found", f"Unknown project '{project_id}'.", 404)
        if s.writer is not None:
            await s.writer.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return Response(status_code=204)

    def upload_row(request: Request, drawing_id: str) -> dict | None:
        st = svc(request).store
        return st.drawing_row(drawing_id) if st and drawing_id.startswith("up-") else None

    @app.get("/drawings/{drawing_id}/status")
    async def drawing_status(drawing_id: str, request: Request):
        row = upload_row(request, drawing_id)
        if row is not None:
            body = {"contract_version": CONTRACT_VERSION, "id": drawing_id, "status": row["status"]}
            if row["progress"] is not None and row["status"] == "processing":
                body["progress"] = row["progress"]
            if row["error"]:
                body["error"] = row["error"]
            return body
        d = svc(request).registry.get(drawing_id)
        if d is None:
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        return {"contract_version": CONTRACT_VERSION, "id": d.id,
                "status": d.drawing.get("status", "ready")}

    @app.get("/drawings/{drawing_id}")
    async def get_drawing(drawing_id: str, request: Request):
        row = upload_row(request, drawing_id)
        if row is not None and row["status"] != "ready":
            why = row["error"] if row["status"] == "failed" else None
            return _err("processing", why or f"Drawing '{drawing_id}' is {row['status']}.", 409)
        d = svc(request).registry.get(drawing_id)
        if d is None:
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        status = d.drawing.get("status", "ready")
        if status != "ready":
            why = d.drawing.get("error") if status == "failed" else None
            return _err("processing", why or f"Drawing '{drawing_id}' is {status}.", 409)
        out = d.public_drawing()
        if row is not None:
            out["created_at"] = _iso(row["t"])
            out["expires_at"] = _iso(row["expires_at"])
        return out

    @app.delete("/drawings/{drawing_id}")
    async def delete_drawing(drawing_id: str, request: Request):
        row = upload_row(request, drawing_id)
        if row is None:
            if svc(request).registry.get(drawing_id) is not None:
                return _err("bad_request", "Only uploads can be deleted.", 403)
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        if not uploads.token_ok(request.headers.get("x-delete-token"), row["delete_token_hash"]):
            return _err("bad_request", "Wrong delete token.", 403)
        await _remove_upload(svc(request), drawing_id)
        return Response(status_code=204)

    upload_times: dict[str, deque[float]] = defaultdict(deque)  # ip_hash → recent uploads

    origin_rx = re.compile(settings.allowed_origin_regex) if settings.allowed_origin_regex else None

    def upload_refused(request: Request, presenter: bool) -> JSONResponse | None:
        """H1: per-IP, queue and storage limits, checked BEFORE the body is read. M2: a
        multipart POST needs no CORS preflight, so a foreign Origin is refused here."""
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins and not (
                origin_rx and origin_rx.fullmatch(origin)):
            return _err("bad_request", "Uploads are only accepted from the AURA app.", 403)
        jobs = request.app.state.jobs
        if 0 < settings.max_queued_uploads <= len(jobs.tasks):
            return _err("rate_limited", "Uploads are busy; try again in a few minutes.", 429)
        if settings.upload_quota_bytes > 0 and _dir_bytes(settings.uploads_dir) >= settings.upload_quota_bytes:
            return _err("rate_limited", "Upload storage is full; try again later.", 429)
        ip = client_ip_hash(request, settings)
        if not presenter and ip:
            q, now = upload_times[ip], time.time()
            while q and now - q[0] > 3600:
                q.popleft()
            if 0 < settings.uploads_per_ip_hour <= len(q):
                return _err("rate_limited", "Too many uploads from this address; try again later.",
                            429)
            q.append(now)
        return None

    @app.post("/uploads", status_code=202)
    async def post_upload(request: Request):
        s = svc(request)
        presenter = is_presenter(request, settings)
        if (refused := upload_refused(request, presenter)) is not None:
            return refused
        try:
            form = await request.form(max_files=1, max_fields=4)
        except Exception:  # noqa: BLE001 - malformed multipart
            return _err("bad_request", "Send the file as multipart/form-data field 'file'.", 400)
        f = form.get("file")
        if not isinstance(f, UploadFile):
            return _err("bad_request", "Send the file as multipart/form-data field 'file'.", 400)
        head = await f.read(65536)
        await f.seek(0)
        size = f.size if f.size is not None else len(head)
        try:
            kind = uploads.validate(head, size, presenter_ok=presenter
                                    or settings.raster_public_cap >= 0)
        except uploads.UploadError as e:
            return _err(e.code, str(e), e.status)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=settings.uploads_dir, prefix=".tmp-")
        os.close(fd)  # H2: a leaked descriptor kept deleted uploads on disk until restart
        tmp = Path(name)
        await asyncio.to_thread(_copy_to, f.file, tmp)
        acc = uploads.store(settings.uploads_dir, kind, tmp)
        now = time.time()
        exp_ts = now + settings.upload_ttl_s
        del_hash = uploads.hash_token(acc.delete_token)
        title = _safe_title(f.filename, kind)
        db_kind = "cad_dxf" if kind == "dxf" else "ifc" if kind == "ifc" else "raster"
        worker_kind = kind if kind in ("dxf", "pdf", "ifc") else "raster"
        await s.writer.execute(  # type: ignore[union-attr]
            "INSERT INTO drawings(id, t, kind, status, progress, error, title, delete_token_hash, "
            "expires_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (acc.drawing_id, now, db_kind, "queued", 0.0, None,
             title, del_hash, exp_ts,
             json.dumps({"kind": kind})))
        meta = {"title": title, "created_at": _iso(now),
                "expires_at": _iso(exp_ts)}
        request.app.state.jobs.submit(
            acc.drawing_id, worker_kind, acc.src, acc.dir, meta,
            {"presenter": presenter, "ip_hash": client_ip_hash(request, settings),
             "delete_token_hash": del_hash, "expires_at": exp_ts})
        return JSONResponse({"contract_version": CONTRACT_VERSION, "drawing_id": acc.drawing_id,
                             "delete_token": acc.delete_token, "status": "queued"},
                            status_code=202)

    @app.get("/drawings/{drawing_id}/{name}")
    async def drawing_file(drawing_id: str, name: str, request: Request):
        if name not in SERVED_FILES:
            return _err("not_found", "No such file.", 404)
        is_upload = drawing_id.startswith("up-")
        if is_upload:
            row = upload_row(request, drawing_id)
            if row is None:
                svc(request).registry.evict(drawing_id)
                return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
            if row["status"] != "ready":
                return _err("processing", f"Drawing '{drawing_id}' is {row['status']}.", 409)
        d = svc(request).registry.get(drawing_id)
        p = d.file(name) if d is not None else None
        if p is None:
            return _err("not_found", "No such file.", 404)
        cache = NO_STORE if is_upload or d.source == "upload" else LIBRARY_CACHE
        # L1: a plan.svg opened directly from the API origin can never run script.
        return FileResponse(p, headers={"cache-control": cache, "x-content-type-options": "nosniff",
                                        "content-security-policy": "default-src 'none'; "
                                        "style-src 'unsafe-inline'; img-src data:; sandbox"})

    def not_ready(request: Request, drawing_id: str) -> JSONResponse | None:
        row = upload_row(request, drawing_id)
        if row is not None and row["status"] != "ready":
            return _err("processing", f"Drawing '{drawing_id}' is {row['status']}.", 409)
        return None

    def caller_of(request: Request) -> Caller:
        return Caller(ip_hash=client_ip_hash(request, settings),
                      presenter=is_presenter(request, settings))

    @app.post("/ask")
    async def post_ask(body: AskRequest, request: Request):
        if (r := not_ready(request, body.drawing_id)) is not None:
            return r
        meters = {"answer": Meter(), "check": Meter()}
        try:
            async with asyncio.timeout(settings.turn_deadline_s):
                return JSONResponse(await ask(svc(request), body, caller_of(request), meters))
        except TimeoutError:
            return _err("timeout", "The answer took too long.", 504, **_spent(*meters.values()))

    @app.post("/chat")
    async def post_chat(body: ChatRequest, request: Request):
        if (r := not_ready(request, body.drawing_id)) is not None:
            return r
        s = svc(request)
        if s.registry.get(body.drawing_id) is None:
            return _err("unknown_drawing", f"Unknown drawing '{body.drawing_id}'", 404)
        if body.conversation_id:
            conv = s.store.conversation(body.conversation_id) if s.store else None
            if conv is not None and not _same_project(s, conv["drawing_id"], body.drawing_id, body.scope):
                return _err("bad_request", "That conversation belongs to another drawing.", 400)
        gen = chat_events(s, body, caller_of(request), settings.turn_deadline_s)
        return StreamingResponse(_sse(gen), media_type="text/event-stream",
                                 headers={"cache-control": NO_STORE, "x-accel-buffering": "no"})

    @app.post("/admin/models")
    async def admin_models(request: Request):
        """Live kill switch, presenter token only: {"on": false} stops every model call at once
        (a MODELS_OFF file on the Volume, read on every call); {"on": true} turns them back on."""
        if not is_presenter(request, settings):
            return _err("presenter_required", "Needs the presenter token.", 403)
        try:
            on = bool((await request.json())["on"])
        except Exception:  # noqa: BLE001 - any malformed body
            return _err("bad_request", 'Send {"on": true|false}.', 400)
        f = kill_file()
        if on:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch()
        if on_modal:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(_volume().commit)
        return {"contract_version": CONTRACT_VERSION, "models": models_enabled()}

    @app.post("/rewrite")
    async def post_rewrite(body: RewriteRequest, request: Request):
        """Rambling text / voice transcript → question blocks for the user to confirm.
        One flash-lite call, reported in `calls`/`cost_usd`; nothing is answered here."""
        from aura.agent.rewrite import rewrite
        from aura.budget import BudgetError
        from aura.gemini.client import GeminiError
        from aura.pipeline import ensure_ready

        s = svc(request)
        ensure_ready(s, body.drawing_id)
        d = s.registry.get(body.drawing_id)
        if d is None:
            return _err("unknown_drawing", f"Unknown drawing '{body.drawing_id}'", 404)
        if not s.models_usable():
            return _err("budget_exhausted", "Models are off or the budget is used up; "
                                            "type the question instead.", 429)
        caller = caller_of(request)
        meter = Meter()
        try:
            async with asyncio.timeout(30):
                r = await rewrite(s.client, d, body.text, ip_hash=caller.ip_hash,  # type: ignore[arg-type]
                                  presenter=caller.presenter, meter=meter)
        except BudgetError as e:
            code = "rate_limited" if e.code == "rate_limited" else "budget_exhausted"
            return _err(code, str(e), 429)
        except (GeminiError, TimeoutError):
            return _err("timeout", "The rewrite model did not answer in time.", 504, **_spent(meter))
        return {"contract_version": CONTRACT_VERSION, "drawing_id": d.id,
                "detected_lang": r.detected_lang, "blocks": r.blocks,
                "not_answerable": r.not_answerable, "calls": r.calls,
                "cost_usd": round(r.cost_usd, 8), "tokens": r.tokens, "model": r.model}

    @app.get("/conversations/{conv_id}")
    async def get_conversation(conv_id: str, request: Request):
        st = svc(request).store
        conv = st.conversation(conv_id) if st else None
        if conv is None:
            return _err("not_found", "Unknown conversation.", 404)
        return {"contract_version": CONTRACT_VERSION, **conv}

    @app.get("/answers/{answer_id}/evidence.csv")
    async def get_evidence_csv(answer_id: str, request: Request):
        s = svc(request)
        resp = s.store.answer(answer_id) if s.store else None
        if resp is None:
            return _err("not_found", "Unknown answer.", 404)
        drawing_id = s.store.answer_drawing(answer_id) if s.store else None
        d = s.registry.get(drawing_id) if drawing_id else None
        rooms = {r["room_id"]: r for r in (d.drawing.get("rooms", []) if d else [])}
        return Response(evidence_csv(resp, rooms), media_type="text/csv; charset=utf-8",
                        headers={"content-disposition":
                                 f'attachment; filename="aura-{answer_id}.csv"'})

    @app.get("/budget")
    async def get_budget(request: Request) -> dict:
        snap = svc(request).budget.snapshot()
        return {"contract_version": CONTRACT_VERSION,
                "models": "on" if models_enabled() else "off",
                "event_used": snap["event"]["used"], "event_cap": snap["event"]["cap"],
                "day_used": snap["today"]["used"], "day_cap": snap["today"]["cap"],
                **(s.store.spend_totals() if (s := svc(request)).store else {})}

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


DETECT_MAX_SIDE = 2048


def _detect_image(path: Path) -> tuple[bytes, str]:
    """The upload as sent to detection: at most 2048 px per side (box_2d is relative)."""
    import io

    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > DETECT_MAX_SIDE:
            im.thumbnail((DETECT_MAX_SIDE, DETECT_MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"


def _raster_hook(client: GeminiClient | None):
    """After a raster upload is parsed: one detection call (budget purpose 'raster_detect';
    presenter token or AURA_RASTER_PUBLIC_CAP), stored as raster.json. Answers are then served
    from it at 0 calls."""

    async def hook(drawing_id: str, out: Path, ctx: dict) -> str | None:
        from aura.budget import BudgetError
        from aura.gemini.client import GeminiError
        from aura.solvers import raster

        if client is None or not client.available():
            return "Image analysis is not available on this server (no model key)."
        data, mime = await asyncio.to_thread(_detect_image, out / "original.png")
        try:
            r = await raster.detect(client, data, mime_type=mime, presenter=ctx.get("presenter",
                                    False), ip_hash=ctx.get("ip_hash"))
        except BudgetError as e:
            return f"Detection was not run: {e}"
        except GeminiError as e:
            return f"Detection failed: {e}"
        (out / "raster.json").write_text(json.dumps(r, ensure_ascii=False))
        dp = out / "drawing.json"
        drawing = json.loads(dp.read_text())
        drawing["capabilities"] = list(raster.SUBJECTS)
        drawing["detection"] = {"model": r["model"], "input_tokens": r["input_tokens"],
                                "output_tokens": r["output_tokens"], "cost_usd": r["cost_usd"],
                                "latency_ms": r["latency_ms"]}
        dp.write_text(json.dumps(drawing, ensure_ascii=False))
        # Room and fixture counts: ONE Decisions score request, paid by the uploader (presenter),
        # so later questions are answered from score.json at 0 calls. Best effort: a failure
        # here leaves those questions to the ask-time path (also presenter-only).
        from aura.gemini.score import run_score

        try:
            sc = await run_score(client, out / "original.png", ip_hash=ctx.get("ip_hash"),
                                 presenter=ctx.get("presenter", False))
            (out / "score.json").write_text(json.dumps(sc, ensure_ascii=False))
        except (BudgetError, GeminiError) as e:
            log.warning("score counts not run: %s", e)
        return None

    return hook


def _copy_to(src, dest: Path) -> None:
    with dest.open("wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _safe_title(filename: str | None, kind: str) -> str:
    """Display title only (never a path): the base name, printable, at most 80 characters."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()[:80]
    if name:
        return name
    defaults = {"dxf": "Uploaded DXF", "pdf": "Uploaded PDF", "ifc": "Uploaded IFC"}
    return defaults.get(kind, "Uploaded image")


async def _sse(gen) -> AsyncIterator[bytes]:
    async for event, data in gen:
        if event.startswith(":"):
            yield f": progress {data.get('seconds', 0)}\n\n".encode()
        else:
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _remove_single_upload(s: Services, drawing_id: str) -> None:
    s.registry.evict(drawing_id)
    if s.agent is not None:
        await s.agent.evict_drawing(drawing_id)
    d = s.settings.uploads_dir / drawing_id
    if d.is_dir() and d.parent == s.settings.uploads_dir:
        await asyncio.to_thread(shutil.rmtree, d, True)
    w = s.writer
    await w.execute("DELETE FROM drawings WHERE id=?", (drawing_id,))  # type: ignore[union-attr]
    # Everything derived from the upload goes with it: answers, conversations, turns, caches.
    await w.execute("DELETE FROM answers WHERE drawing_id=?", (drawing_id,))  # type: ignore[union-attr]
    await w.execute(  # type: ignore[union-attr]
        "DELETE FROM turns WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE drawing_id=?)", (drawing_id,))
    await w.execute("DELETE FROM conversations WHERE drawing_id=?", (drawing_id,))  # type: ignore[union-attr]
    await w.execute("DELETE FROM check_cache WHERE drawing_id=?", (drawing_id,))  # type: ignore[union-attr]
    await w.execute("DELETE FROM answer_cache WHERE drawing_id=?", (drawing_id,))  # type: ignore[union-attr]
    s.answers.drop_drawing(drawing_id)
    s.checks.forget_drawing(drawing_id)


async def _remove_upload(s: Services, drawing_id: str) -> None:
    await _remove_single_upload(s, drawing_id)
    # If this is the primary page of a multi-page PDF (`up-<tok>`), also remove sibling pages & auto-bundle
    if not re.search(r"-p\d+$", drawing_id) and s.store is not None:
        prefix = f"{drawing_id}-p"
        siblings = [r["id"] for r in s.store.upload_rows() if r["id"].startswith(prefix)]
        for sib_id in siblings:
            await _remove_single_upload(s, sib_id)
        if drawing_id.startswith("up-") and s.writer is not None:
            proj_id = f"proj-{drawing_id.removeprefix('up-')}"
            await s.writer.execute("DELETE FROM projects WHERE id=?", (proj_id,))


async def cleanup_once(s: Services, now: float | None = None) -> int:
    """Delete expired uploads (files, rows) and answers/conversations past the TTL."""
    now = time.time() if now is None else now
    expired = [r["id"] for r in s.store.upload_rows() if r["expires_at"] and r["expires_at"] < now]
    for did in expired:
        await _remove_upload(s, did)
    cutoff = now - s.settings.upload_ttl_s
    for table in ("answers", "turns", "conversations", "answer_cache"):
        await s.writer.execute(f"DELETE FROM {table} WHERE t < ?", (cutoff,))  # type: ignore[union-attr]
    # Privacy: the hashed IP is only needed for the last hour's rate limits; older request rows
    # keep their count and cost (the event cap) but lose the hash.
    await s.writer.execute(  # type: ignore[union-attr]
        "UPDATE requests SET ip_hash=NULL WHERE t < ? AND ip_hash IS NOT NULL", (cutoff,))
    # Upload folders with no DB row (a failure between storing and the INSERT) are swept too.
    known = {r["id"] for r in s.store.upload_rows()}
    for p in s.settings.uploads_dir.glob("up-*") if s.settings.uploads_dir.is_dir() else []:
        if p.is_dir() and p.name not in known and p.stat().st_mtime < now - 3600:
            await asyncio.to_thread(shutil.rmtree, p, True)
    await s.writer.execute(  # type: ignore[union-attr]
        "DELETE FROM check_cache WHERE drawing_id LIKE 'up-%' AND t < ?", (cutoff,))
    # Stray temp files from interrupted uploads.
    for p in s.settings.uploads_dir.glob(".tmp-*") if s.settings.uploads_dir.is_dir() else []:
        if p.stat().st_mtime < now - 3600:
            p.unlink(missing_ok=True)
    return len(expired)


async def _cleanup_loop(s: Services) -> None:
    while True:
        await asyncio.sleep(s.settings.cleanup_every_s)
        try:
            await cleanup_once(s)
        except Exception:
            log.exception("cleanup failed")


class DefaultNoStoreMiddleware:
    """Every response that does not choose its own caching gets `cache-control: private,
    no-store`: answers, uploads, conversations and CSVs must not outlive their deletion in a
    browser or shared cache. Only library files opt in to public caching."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(msg: dict) -> None:
            if msg["type"] == "http.response.start":
                headers = list(msg.get("headers") or [])
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", NO_STORE.encode()))
                    msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, receive, _send)


class BodyCapMiddleware:
    """413 for request bodies above the cap: by Content-Length up front, and by counting
    bytes for chunked bodies (the stream is cut off as soon as it passes the cap)."""

    def __init__(self, app: ASGIApp, cap: int):
        self.app = app
        self.cap = cap

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT"):
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.cap:
            await _err("too_large", "The request body is too large (65 MB max).", 413)(
                scope, receive, send)
            return
        seen = 0
        over = False

        async def capped() -> dict:
            nonlocal seen, over
            if over:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.cap:
                    over = True  # the app sees a disconnect; the client gets a 413
                    return {"type": "http.disconnect"}
            return msg

        replaced = False

        async def guarded(msg: dict) -> None:
            nonlocal replaced
            if over:
                if not replaced:
                    replaced = True
                    body = json.dumps({"contract_version": CONTRACT_VERSION, "code": "too_large",
                                       "error": "The request body is too large (65 MB max)."})
                    await send({"type": "http.response.start", "status": 413,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body.encode()})
                return
            await send(msg)

        await self.app(scope, capped, guarded)
