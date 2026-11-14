"""App factory `create_app()` (Modal: `aura.main:create_app`; local: uvicorn --factory).

Lifespan: volume reload (on Modal, startup only) → open DB → start the single writer task →
hourly cleanup loop → on shutdown flush the writer and commit the volume.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
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

from aura import ingest_cost, tools, uploads
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
from aura.drawing_store import NOTES as DS_NOTES
from aura.drawing_store import DrawingStore
from aura.gemini.client import GeminiClient, Meter, Transport
from aura.jobs import Jobs
from aura.logging import AccessLogMiddleware, RequestLogFile, setup_logging
from aura.mcp import build_mcp
from aura.netutil import client_ip_hash, is_presenter
from aura.pipeline import Caller, PipelineError, Services, ask, chat_events
from aura.sessions import router as sessions_router
from aura.solvers.pdf import infer_floor_label_and_order
from aura.store import Store, init_db
from aura.voice import router as voice_router
from aura.writer import Writer

log = logging.getLogger("aura.main")

REINDEX_EVERY_S = 60.0  # one store retry per upload per minute
REINDEX_PER_IP_HOUR = 10

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


def _dedup_raw_floors(raw_floors: list) -> list:
    """Keep the first floor entry per `drawing_id`; malformed entries pass through to validation."""
    seen: set[str] = set()
    out = []
    for item in raw_floors:
        if isinstance(item, dict) and isinstance(item.get("drawing_id"), str) and item["drawing_id"]:
            did = item["drawing_id"]
            if did in seen:
                continue
            seen.add(did)
        out.append(item)
    return out


def _synthesize_projects(s: Services) -> list[ProjectBundle]:
    """Merge persisted SQLite `projects` with auto-detected multi-floor groups (`project_id` / IFC stems)."""
    persisted_rows = s.store.project_rows() if s.store else []
    bundles: list[ProjectBundle] = []
    seen_ids: set[str] = set()
    claimed_drawings: set[str] = set()

    for row in persisted_rows:
        try:
            deduped = _dedup_raw_floors(row["floors"] if isinstance(row.get("floors"), list) else [])
            if len(deduped) < 2:
                continue
            floors = sorted(
                deduped,
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
        if not _is_saved_project_row(row):
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
                # Not a guess from names: the storeys of one IFC model (like the pages of one upload), so the
                # client keeps them as a building instead of dropping them with name-matched bundles.
                "auto_matched": False,
            })
            bundles.append(pb)
            seen_ids.add(pid)
        except ValidationError:
            continue

    return bundles


def _tokens(request: Request, header: str) -> list[str]:
    """One or more tokens in a header: comma-separated and/or the header repeated."""
    return [t.strip() for v in request.headers.getlist(header) for t in v.split(",") if t.strip()][:50]


def _project_access(s: Services, request: Request, floor_ids: list[str], token_hash: str | None) -> bool:
    """The request owns the project. A project with an owner token (`token_hash`) is reached only
    with that token (`x-project-token`); a bundle auto-built from an upload (no owner token) is
    reached with the delete token of one of its upload floors (`x-delete-token`)."""
    if token_hash:
        return any(uploads.token_ok(t, token_hash) for t in _tokens(request, "x-project-token"))
    given = _tokens(request, "x-delete-token")
    if given and s.store is not None:
        for did in floor_ids:
            row = s.store.drawing_row(did) if did.startswith("up-") else None
            if row and any(uploads.token_ok(t, row["delete_token_hash"]) for t in given):
                return True
    return False


def _ownerless(row: dict) -> bool:
    """A row saved before project tokens existed, over library drawings only (nothing private)."""
    return not row.get("token_hash") and not any(
        str(f.get("drawing_id", "")).startswith("up-") for f in row.get("floors") or [])


def _is_saved_project_row(row: dict) -> bool:
    """True for user-saved rows in `projects` (never server-built multi-page PDF upload bundles)."""
    return bool(row.get("token_hash")) or _ownerless(row)


def _bundle_visible(s: Services, request: Request, p: ProjectBundle, rows_by_id: dict[str, dict]) -> bool:
    """True when `request` may see or answer across `p`: public server-built bundles for everyone,
    user-saved or upload-backed bundles only for the caller holding their token."""
    row = rows_by_id.get(p.id)
    if (row is not None and _is_saved_project_row(row)) or any(
        f.drawing_id.startswith("up-") for f in p.floors
    ):
        return _project_access(
            s, request, [f.drawing_id for f in p.floors], row.get("token_hash") if row else None
        )
    return True


def _project_of(s: Services, project_id: str) -> ProjectBundle | None:
    return next((p for p in _synthesize_projects(s) if p.id == project_id), None)


def _scope_refused(s: Services, request: Request, drawing_id: str, scope: Any) -> JSONResponse | None:
    """A project scope is used only as the server stored it: every drawing it names (and the
    drawing asked about) must be a floor of that stored bundle, and a saved or upload-backed
    bundle is private exactly as in GET /projects (unknown to anyone without its token)."""
    if scope is None or getattr(scope, "kind", None) != "project":
        return None
    proj = _project_of(s, scope.project_id)
    if proj is not None:
        rows_by_id = {r["id"]: r for r in (s.store.project_rows() if s.store else [])}
        if not _bundle_visible(s, request, proj, rows_by_id):
            proj = None  # same answer as an unknown id: do not confirm it exists
    if proj is None:
        return _err("not_found", f"Unknown project '{scope.project_id}'.", 404)
    members = {f.drawing_id for f in proj.floors}
    outside = [d for d in [drawing_id, *scope.drawing_ids] if d not in members]
    if outside:
        return _err("bad_request", f"Not floors of project '{proj.id}': {', '.join(outside[:5])}.", 400)
    return None


def _visible_projects(s: Services, request: Request) -> list[ProjectBundle]:
    """The bundles this caller may see (same rule as GET /projects)."""
    rows_by_id = {r["id"]: r for r in (s.store.project_rows() if s.store else [])}
    return [p for p in _synthesize_projects(s) if _bundle_visible(s, request, p, rows_by_id)]


def _building_run(s: Services, request: Request, drawing_id: str, question: str, body: Any):
    """A whole-building `run` for a drawing that is a sheet of a multi-plan project, else None."""
    from aura import building

    rows_by_id = {r["id"]: r for r in (s.store.project_rows() if s.store else [])}
    visible = [p for p in _synthesize_projects(s) if _bundle_visible(s, request, p, rows_by_id)]
    saved_ids = {pid for pid, r in rows_by_id.items() if _is_saved_project_row(r)}
    scope = body.scope.model_dump(exclude_none=True) if body.scope else None
    plan = building.plan_for(visible, drawing_id, question,
                             project_id=body.project_id, sheet_ids=body.sheet_ids,
                             scope=scope, saved_ids=saved_ids)
    if plan is None:
        return None
    building.validate_plan(plan)
    from aura import pipeline
    persist = functools.partial(building.persist_answer, plan=plan)
    return functools.partial(building.run, plan=plan, base_run=pipeline.run, persist=persist)


def _same_project(s: Services, did_a: str, did_b: str) -> bool:
    """A conversation may switch floors only within one server-side project (persisted bundle,
    server-set `project_id`, or an auto-detected IFC storey group). Never from client input."""
    if did_a == did_b:
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
        if "sheet_ids" not in {r["name"] for r in store._q("PRAGMA table_info(answers)")}:
            await writer.execute("ALTER TABLE answers ADD COLUMN sheet_ids TEXT")
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
        drawing_store = DrawingStore(settings, writer, store)
        if settings.store_dsn is not None:  # unset = quick mode; jstore is never imported
            await drawing_store.start()
        app.state.drawing_store = drawing_store
        jobs = Jobs(writer, **(job_options or {}))
        jobs.on_done = registry.evict
        jobs.on_ready = drawing_store.on_parsed
        jobs.store_on = drawing_store.available

        def store_failed(did: str) -> None:  # no store index for this page: our detection instead
            task = asyncio.create_task(jobs.detect_later(did, settings.uploads_dir / did,
                                                         drawing_store.ctx.get(did)))
            jobs.tasks.add(task)
            task.add_done_callback(jobs.tasks.discard)

        drawing_store.on_store_failed = store_failed
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
            drawing_store=drawing_store,
        )
        holder["svc"] = app.state.svc

        def materialised(did: str) -> None:  # the drawing file changed: nothing cached may outlive it
            registry.evict(did)
            _drop_cached_sheet(app.state.svc.answers, did)
            app.state.svc.checks.forget_drawing(did)

        drawing_store.on_materialised = materialised
        # Before the first request: the pool's pending set is in memory, so unsettled pages are
        # re-followed now, never read as "not pending" by a status poll.
        if drawing_store.available():
            await drawing_store.resume_all(store.upload_rows())
            drawing_store.start_sweeper()
        cleanup = asyncio.create_task(_cleanup_loop(app.state.svc, drawing_store), name="aura-cleanup")
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            cleanup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup
            await jobs.cancel_all()
            await drawing_store.close()
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
        allow_headers=["content-type", "x-aura-presenter", "x-delete-token", "x-aura-client",
                       "x-project-token"],
        max_age=600,
    )
    app.include_router(sessions_router)
    app.include_router(voice_router)
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
                # the drawing store's ingestion (Gemini detection/reading of uploads), priced per model
                **(ingest_cost.totals() if request.app.state.drawing_store.available() else {}),
            },
        }
        body["store"] = "on" if request.app.state.drawing_store.available() else "off"
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
        projects = _visible_projects(svc(request), request)
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
        if (refused := origin_refused(request)) is not None:
            return refused
        now = time.time()
        pid = str(raw.get("id") or f"proj-{uuid.uuid4().hex[:12]}").strip()
        existing = s.store.project_row(pid) if s.store else None
        token_hash: str | None = existing.get("token_hash") if existing else None
        new_token: str | None = None
        if existing is not None and _ownerless(existing):
            new_token = uploads.new_delete_token()  # a pre-ownership row: the first saver claims it
            token_hash = uploads.hash_token(new_token)
        elif existing is not None:
            if not _project_access(s, request, [f.get("drawing_id", "") for f in existing["floors"]],
                                   token_hash):
                return _err("bad_request", "This project belongs to someone else (send its "
                                           "x-project-token).", 403)
        elif _project_of(s, pid) is not None:
            return _err("bad_request", f"Project id '{pid}' is taken.", 409)
        else:
            new_token = uploads.new_delete_token()
            token_hash = uploads.hash_token(new_token)
        raw_floors = raw.get("floors")
        if raw_floors is None and isinstance(raw.get("drawing_ids"), list):
            raw_floors = []
            unique_dids = list(dict.fromkeys(str(did) for did in raw["drawing_ids"]))
            for idx, did_s in enumerate(unique_dids, 1):
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
        elif isinstance(raw_floors, list):
            raw_floors = _dedup_raw_floors(raw_floors)
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
        given = _tokens(request, "x-delete-token")
        # Floors an already-owned project held before this save stay covered by its owner token;
        # every other upload floor needs its own delete token (one token never covers another).
        covered: set[str] = set()
        if existing is not None and existing.get("token_hash") and _project_access(
                s, request, [], existing["token_hash"]):
            covered = {str(f.get("drawing_id", "")) for f in existing["floors"]}
        for fl in pb.floors:
            if s.registry.get(fl.drawing_id) is None:
                return _err("unknown_drawing", f"Unknown drawing '{fl.drawing_id}'.", 404)
            # An upload may only be bundled by whoever holds its delete token (library drawings
            # are public); otherwise a project would expose someone else's upload.
            row = s.store.drawing_row(fl.drawing_id) if (s.store and fl.drawing_id.startswith("up-")) else None
            if (row is not None and fl.drawing_id not in covered
                    and not any(uploads.token_ok(t, row["delete_token_hash"]) for t in given)):
                return _err("bad_request", f"You do not own drawing '{fl.drawing_id}'.", 403)
        sorted_floors = sorted(pb.floors, key=lambda f: (float(f.order), f.label, f.drawing_id))
        pb = pb.model_copy(update={"floors": sorted_floors})
        dumped = pb.model_dump(exclude_none=True)
        if s.writer is not None:
            await s.writer.execute(
                "INSERT OR REPLACE INTO projects(id, t, title, floors, auto_matched, body, token_hash) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    pb.id,
                    now,
                    pb.title,
                    json.dumps([f.model_dump() for f in pb.floors], ensure_ascii=False),
                    1 if pb.auto_matched else 0,
                    json.dumps(dumped, ensure_ascii=False),
                    token_hash,
                ),
            )
        # The owner token is shown once, on create; only its hash is stored.
        return dumped | ({"token": new_token, "project_token": new_token} if new_token else {})

    @app.delete("/projects/{project_id}")
    async def delete_project(project_id: str, request: Request):
        s = svc(request)
        if (refused := origin_refused(request)) is not None:
            return refused
        row = s.store.project_row(project_id) if s.store else None
        if row is None:
            return _err("not_found", f"Unknown project '{project_id}'.", 404)
        if not _ownerless(row) and not _project_access(
                s, request, [f.get("drawing_id", "") for f in row["floors"]], row.get("token_hash")):
            return _err("bad_request", "This project belongs to someone else (send its "
                                       "x-project-token).", 403)
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
            return body | await _store_status(svc(request), request.app.state.drawing_store, row)
        d = svc(request).registry.get(drawing_id)
        if d is None:
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        return {"contract_version": CONTRACT_VERSION, "id": d.id,
                "status": d.drawing.get("status", "ready")}

    reindex_last: dict[str, float] = {}
    reindex_by_ip: dict[str, deque] = defaultdict(deque)

    @app.get("/uploads/accepted")
    async def uploads_accepted(request: Request) -> dict:
        """What the upload page may offer right now: types, extensions and size caps."""
        ds = request.app.state.drawing_store
        images_open = (is_presenter(request, settings) or settings.unlimited
                       or settings.raster_public_cap > 0)
        return {"contract_version": CONTRACT_VERSION, "mode": ds.mode,
                "types": uploads.accepted(store_on=ds.available(), images_open=images_open),
                "ttl_hours": int(settings.upload_ttl_s // 3600)}

    @app.post("/drawings/{drawing_id}/reindex")
    @app.post("/drawings/{drawing_id}/store/retry")
    async def reindex_drawing(drawing_id: str, request: Request):
        """Retry the drawing store for an upload (all its pages); owner only (x-delete-token)."""
        row = upload_row(request, drawing_id)
        if row is None:
            return _err("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
        if not uploads.token_ok(request.headers.get("x-delete-token"), row["delete_token_hash"]):
            return _err("bad_request", "Wrong delete token.", 403)
        ds = request.app.state.drawing_store
        if not ds.available():
            return _err("bad_request", "The drawing store is off (quick mode); nothing to retry.", 409)
        s = svc(request)
        pages = [drawing_id] + ([r["id"] for r in s.store.upload_rows() if r["id"].startswith(f"{drawing_id}-p")]
                                if s.store and not re.search(r"-p\d+$", drawing_id) else [])
        if any(p in ds.following for p in pages):
            return _err("bad_request", "The drawing store is still working on this upload.", 409)
        now, ip = time.time(), client_ip_hash(request, settings) or "-"
        presenter = is_presenter(request, settings)
        if now - reindex_last.get(drawing_id, 0.0) < REINDEX_EVERY_S:
            return _err("rate_limited", "This upload was retried a moment ago; wait a minute.", 429)
        q = reindex_by_ip[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= REINDEX_PER_IP_HOUR and not presenter:
            return _err("rate_limited", "Too many retries from this address; try again later.", 429)
        reindex_last[drawing_id] = now
        q.append(now)
        for p in pages:  # the fallback detection spends under the requester's budget context
            ds.ctx[p] = {"presenter": presenter, "ip_hash": client_ip_hash(request, settings)}
        await ds.reindex(pages)
        row = upload_row(request, drawing_id)
        body = {"contract_version": CONTRACT_VERSION, "id": drawing_id, "status": row["status"]}
        return JSONResponse(body | await _store_status(s, ds, row), status_code=202)

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
        await _remove_upload(svc(request), drawing_id, request.app.state.drawing_store)
        return Response(status_code=204)

    upload_times: dict[str, deque[float]] = defaultdict(deque)  # ip_hash → recent uploads

    origin_rx = re.compile(settings.allowed_origin_regex) if settings.allowed_origin_regex else None

    def origin_refused(request: Request) -> JSONResponse | None:
        """M2: a simple POST/DELETE needs no CORS preflight, so a foreign Origin is refused here."""
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins and not (
                origin_rx and origin_rx.fullmatch(origin)):
            return _err("bad_request", "Only accepted from the AURA app.", 403)
        return None

    def upload_refused(request: Request, presenter: bool) -> JSONResponse | None:
        """H1: per-IP, queue and storage limits, checked BEFORE the body is read."""
        if (refused := origin_refused(request)) is not None:
            return refused
        jobs = request.app.state.jobs
        # SVGs skip our parse queue (the store reads them) but count toward the same cap.
        svg_busy = (svc(request).store._q(
            "SELECT count(*) n FROM drawings WHERE status='processing' AND meta LIKE '%\"kind\": \"svg\"%'"
        )[0]["n"] if svc(request).store else 0)
        if 0 < settings.max_queued_uploads <= len(jobs.tasks) + svg_busy:
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
                                    or settings.unlimited or settings.raster_public_cap > 0,
                                    store_on=request.app.state.drawing_store.available())
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
        request.app.state.drawing_store.ctx[acc.drawing_id] = {
            "presenter": presenter, "ip_hash": client_ip_hash(request, settings)}
        if kind == "svg" and not await asyncio.to_thread(uploads.svg_is_safe, acc.src):
            await _remove_upload(s, acc.drawing_id)
            return _err("unsupported_type", "This SVG declares a DTD or entities, which are not "
                        "accepted. Save it as plain SVG and upload again.", 415)
        if kind == "svg":
            # No parse of ours: the drawing store reads the SVG; materialise writes the page image
            # and marks the row ready. The SVG itself is never served (SERVED_FILES).
            await s.writer.execute(  # type: ignore[union-attr]
                "UPDATE drawings SET status='processing', progress=NULL WHERE id=?", (acc.drawing_id,))
            skeleton = {"contract_version": CONTRACT_VERSION, "id": acc.drawing_id, "kind": "raster",
                        "source": "upload", "status": "ready", "rooms": [], "units": [],
                        "frame": {}, **meta}
            (acc.dir / "drawing.json").write_text(json.dumps(skeleton), encoding="utf-8")
            request.app.state.drawing_store.on_parsed(acc.drawing_id, [acc.drawing_id])
            return JSONResponse({"contract_version": CONTRACT_VERSION, "drawing_id": acc.drawing_id,
                                 "delete_token": acc.delete_token, "status": "queued"},
                                status_code=202)
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
        if (r := _scope_refused(svc(request), request, body.drawing_id, body.scope)) is not None:
            return r
        meters = {"answer": Meter(), "check": Meter()}
        try:
            async with asyncio.timeout(settings.turn_deadline_s):
                s = svc(request)
                run_fn = _building_run(s, request, body.drawing_id, body.question, body)
                return JSONResponse(await ask(s, body, caller_of(request), meters, run_fn))
        except TimeoutError:
            return _err("timeout", "The answer took too long.", 504, **_spent(*meters.values()))

    @app.post("/chat")
    async def post_chat(body: ChatRequest, request: Request):
        if (r := not_ready(request, body.drawing_id)) is not None:
            return r
        s = svc(request)
        if s.registry.get(body.drawing_id) is None:
            return _err("unknown_drawing", f"Unknown drawing '{body.drawing_id}'", 404)
        if (r := _scope_refused(s, request, body.drawing_id, body.scope)) is not None:
            return r
        if body.conversation_id:
            conv = s.store.conversation(body.conversation_id) if s.store else None
            if conv is not None and not _same_project(s, conv["drawing_id"], body.drawing_id):
                return _err("bad_request", "That conversation belongs to another drawing.", 400)
        gen = chat_events(s, body, caller_of(request), settings.turn_deadline_s,
                          _building_run(s, request, body.drawing_id, body.message, body))
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


def _drop_cached_sheet(answers: AnswerCache, drawing_id: str) -> None:
    """Remove every in-memory cached answer whose own drawing, scope or body includes `drawing_id`."""
    from aura import building

    answers.drop_drawing(drawing_id)
    for k, v in list(answers._d.items()):
        d_id, _q, k_scope, *_ = answers._parse_key(k)
        in_scope = isinstance(k_scope, dict) and drawing_id in (k_scope.get("drawing_ids") or [])
        if d_id == drawing_id or in_scope or building.response_contains_sheet(v, drawing_id):
            answers._d.pop(k, None)
            answers._t.pop(k, None)
            answers._q.pop(k, None)


async def _remove_single_upload(s: Services, drawing_id: str) -> None:
    from aura import building

    s.registry.evict(drawing_id)
    if s.agent is not None:
        await s.agent.evict_drawing(drawing_id)
    d = s.settings.uploads_dir / drawing_id
    if d.is_dir() and d.parent == s.settings.uploads_dir:
        await asyncio.to_thread(shutil.rmtree, d, True)
    _drop_cached_sheet(s.answers, drawing_id)
    s.checks.forget_drawing(drawing_id)

    w = s.writer
    if w is None:
        return
    # Drain queued fire-and-forget writes (answers, answer_cache, check_cache) before scanning/deleting.
    await w.flush()
    await w.execute("DELETE FROM drawings WHERE id=?", (drawing_id,))
    # Everything derived from the upload goes with it: answers, conversations, turns, caches.
    await w.execute("DELETE FROM answers WHERE drawing_id=?", (drawing_id,))
    await w.execute(
        "DELETE FROM turns WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE drawing_id=?)", (drawing_id,))
    await w.execute("DELETE FROM conversations WHERE drawing_id=?", (drawing_id,))
    await w.execute("DELETE FROM check_cache WHERE drawing_id=?", (drawing_id,))
    await w.execute("DELETE FROM answer_cache WHERE drawing_id=?", (drawing_id,))

    if s.store is not None:
        pat = f"%{drawing_id}%"
        has_sheet_col = "sheet_ids" in {r["name"] for r in s.store._q("PRAGMA table_info(answers)")}
        ans_rows = (
            s.store._q(
                "SELECT id, response, sheet_ids FROM answers WHERE sheet_ids LIKE ? OR response LIKE ?",
                (pat, pat),
            )
            if has_sheet_col
            else s.store._q(
                "SELECT id, response, NULL AS sheet_ids FROM answers WHERE response LIKE ?",
                (pat,),
            )
        )
        for row in ans_rows:
            if building.response_contains_sheet(row["response"], drawing_id, row["sheet_ids"]):
                await w.execute("DELETE FROM answers WHERE id=?", (row["id"],))

        cache_rows = s.store._q(
            "SELECT key, response FROM answer_cache WHERE key LIKE ? OR response LIKE ?",
            (pat, pat),
        )
        for crow in cache_rows:
            d_id, _q, k_scope, *_ = s.answers._parse_key(crow["key"])
            in_key = d_id == drawing_id or (
                isinstance(k_scope, dict) and drawing_id in (k_scope.get("drawing_ids") or [])
            )
            if in_key or building.response_contains_sheet(crow["response"], drawing_id):
                await w.execute("DELETE FROM answer_cache WHERE key=?", (crow["key"],))

        turn_rows = s.store._q(
            "SELECT id, conversation_id, response FROM turns WHERE response IS NOT NULL AND response LIKE ?",
            (pat,),
        )
        touched_convs: set[str] = set()
        for trow in turn_rows:
            if building.response_contains_sheet(trow["response"], drawing_id):
                await w.execute("DELETE FROM turns WHERE id=?", (trow["id"],))
                touched_convs.add(trow["conversation_id"])
        for cid in touched_convs:
            await w.execute(
                "DELETE FROM conversations WHERE id=? AND NOT EXISTS "
                "(SELECT 1 FROM turns WHERE conversation_id=?)",
                (cid, cid),
            )

        # Saved chat sessions on the upload (aura/sessions.py) or containing combined turns with it.
        if s.store._q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_sessions'"):
            await w.execute("DELETE FROM chat_sessions WHERE drawing_id=?", (drawing_id,))
            sess_rows = s.store._q(
                "SELECT client_id, id, body FROM chat_sessions WHERE body LIKE ?",
                (pat,),
            )
            for srow in sess_rows:
                try:
                    sbody = json.loads(srow["body"])
                except ValueError:
                    continue
                if isinstance(sbody, dict) and isinstance(sbody.get("turns"), list):
                    kept = [
                        t
                        for t in sbody["turns"]
                        if not building.turn_contains_sheet(t, drawing_id)
                    ]
                    if len(kept) != len(sbody["turns"]):
                        if not kept:
                            await w.execute(
                                "DELETE FROM chat_sessions WHERE client_id=? AND id=?",
                                (srow["client_id"], srow["id"]),
                            )
                        else:
                            sbody["turns"] = kept
                            await w.execute(
                                "UPDATE chat_sessions SET body=? WHERE client_id=? AND id=?",
                                (
                                    json.dumps(sbody, separators=(",", ":")),
                                    srow["client_id"],
                                    srow["id"],
                                ),
                            )

    await w.flush()
    _drop_cached_sheet(s.answers, drawing_id)
    s.checks.forget_drawing(drawing_id)


_STAGE_RANK = {"queued": 0, "parsing": 1, "reading": 2, "interpreting": 3, "materialising": 4}
_STAGE_FAIL = ("failed", "store_failed", "partial")


async def _store_status(s: Services, ds: DrawingStore, row: dict) -> dict:
    """The optional store fields of GET /drawings/{id}/status: stage, mode, pages[], counts, note,
    ingest_calls, elapsed_s, stage_s (see the upload page's status contract)."""
    did = row["id"]
    rows = [row]
    if s.store is not None and not re.search(r"-p\d+$", did):
        sibs = [r for r in s.store.upload_rows() if r["id"].startswith(f"{did}-p")]
        rows += sorted(sibs, key=lambda r: int(r["id"].rsplit("-p", 1)[1] or 0))
    now = time.time()
    pages, counts, calls, stages = [], {}, 0, []
    cost, tok_in, tok_out = 0.0, 0, 0  # Gemini spend of the store's ingestion (aura/ingest_cost.py)
    for i, r in enumerate(rows, 1):
        try:
            meta = json.loads(r.get("meta") or "{}")
        except ValueError:
            meta = {}
        if await ds.refresh_db_stage(r["id"], meta) is not None and s.store is not None:
            meta = json.loads((s.store.drawing_row(r["id"]) or {}).get("meta") or "{}")
        stage, st = ds.page_stage(r, meta)
        stages.append((stage, st))
        page: dict[str, Any] = {"drawing_id": r["id"], "page": i, "stage": stage}
        if meta.get("floor_label"):
            page["label"] = meta["floor_label"]
        for k in ("file_id", "counts", "methods"):
            if st.get(k):
                page[k] = st[k]
        if stage == "store_failed":
            page["error"] = "The file could not be read"
        for k, v in (st.get("counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
        calls += int(st.get("calls") or 0)
        cost += float(st.get("cost_usd") or 0)
        tok_in += int((st.get("tokens") or {}).get("input") or 0)
        tok_out += int((st.get("tokens") or {}).get("output") or 0)
        pages.append(page)
    live = [x for x in stages if x[0] in _STAGE_RANK]
    if live:
        top, top_st = min(live, key=lambda x: _STAGE_RANK[x[0]])
    else:
        top, top_st = next((x for x in stages if x[0] in _STAGE_FAIL), stages[0])
    done = sum(1 for x, _ in stages if x in ("indexed_store", "indexed_ours"))
    note = (f"{done} of {len(stages)} pages indexed" if len(stages) > 1 and live else
            DS_NOTES.get(top))
    if top == "indexed_store" and any(v == "vision" for v in (top_st.get("methods") or {}).values()):
        note = "Indexed by the drawing store; items found by a model are labelled estimates"
    out: dict[str, Any] = {"stage": top, "mode": ds.mode, "pages": pages,
                           "elapsed_s": max(0, int(now - float(row["t"]))),
                           "stage_s": max(0, int(now - float(top_st.get("t_stage") or row["t"])))}
    if counts:
        out["counts"] = {"pages": len(pages)} | counts
    est = sorted({k for _, st in stages for k, v in (st.get("methods") or {}).items() if v == "vision"})
    if est:
        out["estimates"] = est  # subjects whose counts come from a model (label them "estimate")
    if note:
        out["note"] = note
    if ds.available() or calls:
        out["ingest_calls"] = calls
        out["ingest_cost_usd"] = round(cost, 6)
        out["ingest_tokens"] = {"input": tok_in, "output": tok_out}
    return out


def _store_file_ids(s: Services, ids: set[str] | None = None) -> dict[str, set[str]]:
    """file_id in the drawing store -> our upload rows that reference it (two uploads with identical
    bytes share one store project)."""
    refs: dict[str, set[str]] = {}
    for r in s.store.upload_rows() if s.store else []:
        if ids is not None and r["id"] not in ids:
            continue
        try:
            fid = (json.loads(r.get("meta") or "{}").get("store") or {}).get("file_id")
        except ValueError:
            fid = None
        if fid:
            refs.setdefault(fid, set()).add(r["id"])
    return refs


async def _remove_upload(s: Services, drawing_id: str, ds: DrawingStore | None = None) -> None:
    """Delete an upload (and its pages) everywhere: our files and rows, then the drawing store.
    A tombstone per page is kept until the store side is done, so a delete while Postgres is down
    (or while the page is still being registered) is finished by the store sweep."""
    removing = {drawing_id}
    if s.store is not None and not re.search(r"-p\d+$", drawing_id):
        removing |= {r["id"] for r in s.store.upload_rows() if r["id"].startswith(f"{drawing_id}-p")}
    refs = _store_file_ids(s, removing)
    fids = sorted(refs)
    if ds is not None:
        for fid, rows in refs.items():
            for rid in rows:
                await ds.tombstone(rid, fid)
    await _remove_upload_rows(s, drawing_id)
    if ds is not None and ds.available() and fids:
        await ds.forget(fids)  # keeps bytes a live upload or the bundled dataset still uses
        for rid in removing:
            await s.writer.execute("DELETE FROM store_tombstones WHERE drawing_id=?", (rid,))  # type: ignore[union-attr]


async def _remove_upload_rows(s: Services, drawing_id: str) -> None:
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


async def cleanup_once(s: Services, now: float | None = None, ds: DrawingStore | None = None) -> int:
    """Delete expired uploads (files, rows) and answers/conversations past the TTL."""
    now = time.time() if now is None else now
    expired = [r["id"] for r in s.store.upload_rows() if r["expires_at"] and r["expires_at"] < now]
    for did in expired:
        await _remove_upload(s, did, ds)
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


async def _cleanup_loop(s: Services, ds: DrawingStore | None = None) -> None:
    while True:
        await asyncio.sleep(s.settings.cleanup_every_s)
        try:
            await cleanup_once(s, ds=ds)
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
