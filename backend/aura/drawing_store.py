"""The drawing store (Felix's jstore, in-process): every upload is also read into Postgres, interpreted
(rooms, doors, windows, fixtures) and, for PDF/PNG/JPG, materialised back into the upload's drawing.json.

Switch: AURA_STORE_DSN. Unset = quick mode, and jstore is never imported (its db module falls back to a
hard-coded DSN at import, so the environment is set from our settings BEFORE the first import).

Stages of one upload page (GET /drawings/{id}/status):
    reading (store row registered) -> interpreting (parsed, a worker on it) -> materialising -> indexed_store
    final otherwise: indexed_ours (DXF/IFC: our solvers own rooms/openings), ready_unindexed (quick mode),
    partial (parsed, interpretation ended without a result), store_failed (the store could not read it).

Honesty rules of materialise (no store item is ever labelled cad_geometry):
    - `evidence_method[subject]`: `vision` (an estimate) when any source of that subject is a model detection
      (`raster_detection`), `pdf_vector` when every source is PDF vectors/text, `ocr` for OCR words.
    - a box never gets `note` (BundleEngine reads notes as CAD duplicates); the store reference is `store_ref`.
    - `sink` is dropped (not a contract subject) and counted in `dropped`.

Per-page state lives in the upload row's meta (`meta.store`): {file_id, drawing_key, state, t0, t_stage,
calls, cost_usd, error?}; state is registered | materialised | partial | failed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from contextlib import suppress as contextlib_suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aura.config import Settings

log = logging.getLogger("aura.drawing_store")

STORE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".dxf", ".ifc", ".svg"}
MATERIALISE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".svg"}  # DXF/IFC: our own solvers stay the owner
FINAL = {"indexed_store", "indexed_ours", "ready_unindexed", "partial", "store_failed", "failed"}
NOTES = {
    "reading": "Reading the file into the drawing store",
    "interpreting": "Finding rooms, doors and windows",
    "materialising": "Writing the result into the drawing",
    "indexed_store": "Indexed by the drawing store",
    "indexed_ours": "Rooms and openings from the CAD geometry (exact)",
    "ready_unindexed": "Quick mode: no drawing store, so rooms are not indexed for this file",
    "partial": "Text and lines were read; finding rooms stopped before it finished",
    "store_failed": "The drawing store could not read this file; it stays viewable, without a rooms index",
}
SUBJECTS = {"door", "window", "washbasin", "wc", "bathtub", "shower"}
MODEL_TABLES = {"raster_detection"}
VECTOR_TABLES = {"pdf_path", "pdf_text"}
OCR_TABLES = {"raster_word"}


def configure_env(settings: Settings) -> None:
    """jstore reads its DSN, reader password and data dir from the environment at import time; the worker
    processes inherit it. Called once, before the first jstore import."""
    assert settings.store_dsn is not None
    os.environ["JSTORE_DSN"] = settings.store_dsn.get_secret_value()
    if settings.store_reader_password is not None:
        os.environ["JSTORE_READER_PASSWORD"] = settings.store_reader_password.get_secret_value()
    settings.store_data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JSTORE_DATA_DIR"] = str(settings.store_data_dir)
    os.environ["JSTORE_SHELL"] = "off"
    # A worker caps its data segment (RLIMIT_DATA 4 GB) after its first interpretation, for its whole
    # life; one OCR/OpenCV/BLAS thread per core then fails in pthread_create (measured on 22 cores).
    for k in THREAD_CAPS:
        os.environ.setdefault(k, "4")


THREAD_CAPS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
WORKER_ENV_KEYS = ("GEMINI_API_KEY", "PATH", "HOME", "LANG", "TMPDIR", *THREAD_CAPS)


def worker_env() -> dict[str, str]:
    """The environment an ingest worker needs (DSN, data dir, Gemini key for symbol detection)."""
    return {k: v for k, v in os.environ.items() if k.startswith("JSTORE_") or k in WORKER_ENV_KEYS}


def init_worker(env: dict[str, str]) -> None:
    """Pool initializer. Our parse jobs make every spawn in this process start through a scrubbed
    launcher (parse_worker.start: multiprocessing's executable is process-global), so the store
    workers would start without JSTORE_DSN and silently use jstore's default credentials. The
    environment arrives here through spawn's pipe, before any jstore import in the worker."""
    os.environ.update(env)


def make_pool(workers: int):
    import concurrent.futures as cf
    import multiprocessing

    from aura.ingest_cost import metered_call

    class MeteredPool(cf.ProcessPoolExecutor):
        """Every store job runs through ingest_cost.metered_call: its Gemini tokens land in usage/<key>.json."""

        def submit(self, fn, /, *args, **kwargs):
            return super().submit(metered_call, fn, args, kwargs)

    return MeteredPool(workers, mp_context=multiprocessing.get_context("spawn"),
                       initializer=init_worker, initargs=(worker_env(),))


def interpret_job(drawing_key: str) -> dict:
    """Layer 2 again for a drawing whose worker died after parsing (runs in a worker process)."""
    from jstore import pipeline
    return pipeline.interpret_drawing(drawing_key)


# ---- materialise: the store's drawing as our drawing.json ---------------------------------------


def _method_of(tables: set[str]) -> str:
    if tables & MODEL_TABLES:
        return "vision"
    if tables and tables <= VECTOR_TABLES:
        return "pdf_vector"
    if tables & OCR_TABLES:
        return "ocr"
    return "vision"  # unknown provenance is never presented as measured


def normalise(ours: dict, doc: dict, evidence: dict, model: dict, sources: dict[int, list[str]]) -> dict:
    """Our drawing.json from the store's documents (pure; tested on a captured fixture).

    ours:     the upload's current drawing.json (id, title, images, frame px, dates are kept)
    doc:      aura_model.drawing_doc;  evidence: aura_model.evidence_doc;  model: aura_model.model_doc
    sources:  entity_id -> the Layer-1 tables it was read from (entity_sources.ref_table)
    """
    ent_of_item = {it["id"]: it.get("entity_id") for it in model.get("items", [])}
    ent_of_room = {r["room_id"]: r.get("entity_id") for r in model.get("rooms", [])}
    tables_by_subject: dict[str, set[str]] = {}
    out_ev: dict[str, list[dict]] = {}
    dropped: dict[str, int] = {}
    for subject, boxes in (evidence or {}).items():
        if subject not in SUBJECTS:
            if boxes:
                dropped[subject] = len(boxes)
            continue
        rows = []
        for b in boxes:
            ent = ent_of_item.get(b["id"])
            row = {k: v for k, v in b.items() if k not in ("note", "duplicate_of")}
            if ent is not None:
                row["store_ref"] = ent
                tables_by_subject.setdefault(subject, set()).update(sources.get(ent, []))
            rows.append(row)
        if rows:
            out_ev[subject] = rows
    rooms = []
    room_tables: set[str] = set()
    for r in doc.get("rooms", []):
        row = dict(r)
        ent = ent_of_room.get(r["room_id"])
        if ent is not None:
            row["store_ref"] = ent
            room_tables.update(sources.get(ent, []))
        rooms.append(row)
    methods = {s: _method_of(t) for s, t in tables_by_subject.items()}
    for s in out_ev:
        methods.setdefault(s, "vision")
    if rooms:
        methods["room"] = _method_of(room_tables)
    texts = [{"id": f"txt-{i:03d}", **it["box"], "text": it["label"]}
             for i, it in enumerate(it for it in model.get("items", [])
                                    if it.get("subject") == "label" and it.get("box") and it.get("label"))]
    caps = [c for c in doc.get("capabilities", []) if c in out_ev or c in ("room", "apartment")
            or c in ("bedroom", "kitchen", "bathroom", "balcony", "sauna")]
    out = {k: v for k, v in ours.items() if k not in ("rooms", "units", "evidence", "texts", "capabilities",
                                                      "evidence_method", "store", "m_per_pct_x", "m_per_pct_y")}
    frame = dict(doc.get("frame") or ours.get("frame") or {})
    if (ours.get("frame") or {}).get("px"):
        frame["px"] = ours["frame"]["px"]  # our own render of the same page is the image served
    out.update(
        frame=frame, m_per_pct_x=doc.get("m_per_pct_x", 0), m_per_pct_y=doc.get("m_per_pct_y", 0),
        rooms=rooms, units=doc.get("units", []), evidence=out_ev, texts=texts, capabilities=caps,
        evidence_method=methods,
    )
    if dropped:
        out["store_dropped"] = dropped
    return out


# ---- the store ---------------------------------------------------------------------------------


class DrawingStore:
    """One per app. All jstore calls are sync (pg8000) and run in threads; ingestion runs in jstore's own
    spawn pool (not our parse slots), so a second upload is never blocked by a long interpretation."""

    def __init__(self, settings: Settings, writer: Any, store: Any, on_materialised=None):
        self.s = settings
        self.writer = writer
        self.store = store  # aura.store.Store (SQLite reads)
        self.on_materialised = on_materialised  # callable(drawing_id): evict caches
        self.on_store_failed = None  # callable(drawing_id): our own detection as the fallback
        self.following: set[str] = set()  # drawing ids with a follow task running (reindex guard)
        self.ctx: dict[str, dict] = {}  # drawing id -> the uploader's budget context (presenter, ip)
        self.ok = False
        self.library: Any = None  # aura.store_library.StoreLibrary: its drawings are permanent
        self.ingestor: Any = None
        self.jsettings: Any = None
        self.tasks: set[asyncio.Task] = set()
        # store_upload is check-then-insert: two uploads with the same bytes at once would race.
        self.register_lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        return "full" if self.ok else "quick"

    async def start(self) -> bool:
        if self.s.store_dsn is None:
            return False
        configure_env(self.s)
        try:
            from jstore import db as jdb
            from jstore.service.config import Settings as JSettings
            from jstore.service.files import Ingestor

            # A wrong import order would silently connect with jstore's default credentials.
            if jdb.DSN != self.s.store_dsn.get_secret_value():
                raise RuntimeError("jstore was imported before AURA_STORE_DSN was applied")
            await asyncio.to_thread(jdb.apply_schema)
            self.jsettings = JSettings(data_dir=self.s.store_data_dir.resolve(),
                                       dataset_dir=(self.s.store_data_dir / "dataset").resolve(),
                                       ingest_workers=self.s.store_workers, shell=False, apply_schema=False)
            self.ingestor = Ingestor(self.jsettings)
            self.ingestor.pool = make_pool(self.s.store_workers)  # see init_worker
            self.ok = True
        except Exception as e:  # noqa: BLE001 - Postgres down or schema error: quick mode, never a crash
            log.warning("drawing store unavailable, quick mode: %s", type(e).__name__)
            self.ok = False
        return self.ok

    def available(self) -> bool:
        return self.ok

    async def close(self) -> None:
        for t in list(self.tasks):
            t.cancel()
        if self.ingestor is not None:
            await asyncio.to_thread(self.ingestor.shutdown)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # -- meta in our SQLite row
    def _meta(self, drawing_id: str) -> dict:
        row = self.store.drawing_row(drawing_id) if self.store else None
        if not row:
            return {}
        try:
            return json.loads(row["meta"] or "{}")
        except ValueError:
            return {}

    async def _set_store_meta(self, drawing_id: str, **fields: Any) -> None:
        meta = self._meta(drawing_id)
        st = dict(meta.get("store") or {})
        if fields.get("state") and fields["state"] != st.get("state"):
            fields.setdefault("t_stage", time.time())
        st.update(fields)
        meta["store"] = st
        await self.writer.execute("UPDATE drawings SET meta=? WHERE id=?", (json.dumps(meta), drawing_id))
        if (fields.get("state") in ("failed", "partial") and meta.get("kind") in ("pdf", "png", "jpg")
                and self.on_store_failed is not None):
            self.on_store_failed(drawing_id)
        if meta.get("kind") == "svg" and fields.get("state") in ("failed", "partial"):
            # an SVG has no parse of ours to fall back on
            await self.writer.execute(
                "UPDATE drawings SET status='failed', progress=NULL, error=? WHERE id=?",
                ("The drawing store could not read this SVG.", drawing_id))

    # -- retention: what may be deleted in the store
    def permanent_file_ids(self) -> set[str]:
        """Dataset files of the bundled drawings (build/store-map.json): never deleted with an upload,
        even when an upload had the same bytes."""
        out: set[str] = set()
        for build in self.s.drawing_dirs():
            p = Path(build).parent / "store-map.json"
            for cand in (p, Path(build) / "store-map.json"):
                if cand.is_file():
                    with contextlib_suppress(OSError, ValueError, KeyError, AttributeError):
                        for v in json.loads(cand.read_text(encoding="utf-8")).values():
                            out |= {f["sha256_16"] for f in v.get("files") or [] if f.get("sha256_16")}
        if self.library is not None:  # the store library shows every dataset file: none may go with an upload
            out |= self.library.permanent_file_ids()
        return out

    def live_file_ids(self) -> set[str]:
        out = set()
        for r in self.store.upload_rows() if self.store else []:
            fid = (self._json(r.get("meta")).get("store") or {}).get("file_id")
            if fid:
                out.add(fid)
        return out

    @staticmethod
    def _json(v) -> dict:
        try:
            return json.loads(v or "{}")
        except ValueError:
            return {}

    async def forget(self, file_ids: list[str], *, locked: bool = False) -> None:
        """Delete these store projects unless a live upload or the dataset still uses the bytes.
        Under the registration lock: a registration of the same bytes cannot slip in between the
        reference check and the delete (it would lose its store data). `locked`: caller holds it."""
        if not locked:
            async with self.register_lock:
                return await self.forget(file_ids, locked=True)
        keep = self.live_file_ids() | await asyncio.to_thread(self.permanent_file_ids)
        await self.delete([f for f in file_ids if f and f not in keep])

    async def tombstone(self, drawing_id: str, file_id: str | None) -> None:
        await self.writer.execute(
            "INSERT OR REPLACE INTO store_tombstones(drawing_id, file_id, t) VALUES (?,?,?)",
            (drawing_id, file_id, time.time()))

    async def sweep(self) -> int:
        """Startup and every 10 min: finish store deletes that were skipped (Postgres was down, or
        the upload was deleted while it was being registered) and drop upload projects no live
        upload references. Dataset projects are never touched."""
        if not self.ok:
            return 0
        async with self.register_lock:  # no registration is between store_upload and its meta write
            tomb = self.store._q("SELECT drawing_id, file_id FROM store_tombstones") if self.store else []

            orphans = await asyncio.to_thread(self._upload_projects)
            gone = [r["file_id"] for r in tomb if r["file_id"]] + orphans
            keep = self.live_file_ids() | await asyncio.to_thread(self.permanent_file_ids)
            doomed = sorted({f for f in gone if f not in keep})
            await self.delete(doomed)
        for r in tomb:
            await self.writer.execute("DELETE FROM store_tombstones WHERE drawing_id=?", (r["drawing_id"],))
        return len(doomed)

    def start_sweeper(self, every_s: float = 600.0) -> None:
        async def loop():
            while True:
                try:
                    n = await self.sweep()
                    if n:
                        log.info("store sweep removed %d upload project(s)", n)
                except Exception:  # Postgres down: the tombstones wait for the next round
                    log.warning("store sweep skipped", exc_info=True)
                await asyncio.sleep(every_s)
        self._spawn(loop())

    def _upload_projects(self) -> list[str]:
        """file_ids of every upload project in the store (`upload/<file_id>`)."""
        from jstore.db import DB
        with DB() as db:
            return [r["project_id"].split("/", 1)[1] for r in
                    db.run("select project_id from jstore.project where project_id like 'upload/%'")]

    def _gone(self, drawing_id: str) -> bool:
        return self.store is not None and self.store.drawing_row(drawing_id) is None

    # -- register
    def on_parsed(self, drawing_id: str, page_ids: list[str]) -> None:
        """Jobs hook after our own parse: every page (a multi-page PDF has one per page) goes into the
        store in the background. Returns at once."""
        if not self.ok:
            return
        for did in page_ids:
            self._spawn(self._register_page(did))

    async def _register_page(self, drawing_id: str) -> None:
        d = self.s.uploads_dir / drawing_id
        src = next(iter(sorted(d.glob("source.*"))), None)
        if src is None:
            return
        row = self.store.drawing_row(drawing_id) if self.store else None
        title = (row or {}).get("title") or src.name
        await self.register_upload(drawing_id, src, f"{Path(title).stem}{src.suffix.lower()}")

    async def register_upload(self, drawing_id: str, src: Path, original_name: str) -> dict | None:
        """Copy the upload into the store (jstore MOVES the file it is given: never pass ours) and start
        ingestion. -> the store row, or None when the store is off or the type is not stored."""
        if not self.ok or src.suffix.lower() not in STORE_EXT:
            return None
        # Normally created at start-up; a store switched on later (or a fresh volume) must not fail the first upload.
        self.s.store_data_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="aura-store-", dir=self.s.store_data_dir))
        tmp = tmp_dir / src.name
        try:
            await asyncio.to_thread(shutil.copy2, src, tmp)
            from jstore.service import files as jfiles
            async with self.register_lock:
                if self._gone(drawing_id):  # deleted before we got here
                    return None
                row = await asyncio.to_thread(jfiles.store_upload, self.jsettings, tmp, original_name)
                if self._gone(drawing_id):  # deleted while the store took the file: undo at once
                    await self.forget([row["file_id"]], locked=True)
                    return None
                await self._set_store_meta(drawing_id, file_id=row["file_id"],
                                           drawing_key=row["drawing_key"], state="registered",
                                           t0=time.time(), ext=src.suffix.lower(),
                                           dataset=not str(row.get("project_id") or "").startswith("upload/"))
        except Exception as e:  # noqa: BLE001 - the upload stays usable from our parse
            log.warning("store register failed for %s: %s", drawing_id, type(e).__name__)
            await self._set_store_meta(drawing_id, state="failed", error="register", t0=time.time())
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        self._spawn(self._follow(drawing_id, row["file_id"], row["drawing_key"]))
        return row

    # -- follow one page until it is final
    async def _db_status(self, file_id: str) -> tuple[str | None, str | None]:
        from jstore.db import DB

        def q():
            with DB() as db:
                r = db.one("select status, drawing_key from jstore.source_file where file_id = :f", f=file_id)
                return (r["status"], r["drawing_key"]) if r else (None, None)
        return await asyncio.to_thread(q)

    async def _follow(self, drawing_id: str, file_id: str, key: str) -> None:
        self.following.add(drawing_id)
        try:
            status, _ = await self._db_status(file_id)
            if status in ("registered", "failed"):
                from jstore.db import DB

                def row():
                    with DB() as db:
                        return db.one("select * from jstore.source_file where file_id = :f", f=file_id)
                r = await asyncio.to_thread(row)
                if r is not None:
                    self.ingestor.ensure(r)
            elif status == "parsed" and file_id not in self.ingestor.pending([file_id]):
                # parsed but nobody interprets it (a worker died, or the server restarted): resume
                self.ingestor.jobs[file_id] = self._interpret_pool().submit(interpret_job, key)
            fut = self.ingestor.jobs.get(file_id)
            if fut is not None:
                try:
                    res = await asyncio.wrap_future(fut)
                    log.info("store ingest %s: %s", file_id, {k: v for k, v in (res or {}).items()
                                                              if k in ("status", "seconds", "error")})
                except Exception as e:  # noqa: BLE001 - the worker's failure is recorded on the row
                    log.warning("store ingest %s failed in the worker: %s: %s", file_id,
                                type(e).__name__, str(e)[:300])
            else:
                log.warning("store ingest %s: no job started (status %s)", file_id, status)
            status, _ = await self._db_status(file_id)
            if not self._gone(drawing_id):  # spent even when the store fails: record it now
                await self._set_store_meta(drawing_id, **_spent(file_id, key))
            if self._gone(drawing_id):  # deleted during ingestion: nothing of it may stay
                await self.forget([file_id])
                return
            if status == "interpreted":
                await self.materialise(drawing_id, file_id, key)
            elif status == "parsed":
                await self._set_store_meta(drawing_id, state="partial")
            else:
                await self._set_store_meta(drawing_id, state="failed", error="read")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a background follower must never take the app down
            log.exception("store follow failed for %s", drawing_id)
            await self._set_store_meta(drawing_id, state="failed", error=type(e).__name__)
        finally:
            self.following.discard(drawing_id)

    def _interpret_pool(self):
        if self.ingestor.pool is None:
            self.ingestor.pool = make_pool(self.s.store_workers)
        return self.ingestor.pool

    # -- materialise
    def _build(self, key: str) -> dict | None:
        from jstore.db import DB
        from jstore.service import aura_model as M
        with DB() as db:
            data = M.load(db, key)
            if data is None:
                return None
            m = M.build(db, key, data=data)
            if m is None:
                return None
            sources = {e: sorted({s["ref_table"] for s in v}) for e, v in data["srcs"].items()}
            spec = db.one("""select spec from jstore.definition d join jstore.source_file f using (file_id)
                             where f.drawing_key = :k and d.method = 'gemini_tiled_detection'
                             order by def_id limit 1""", k=key)
            calls = sum(int(p.get("requests") or 0) for p in ((spec or {}).get("spec") or {}).get("pages") or [])
            page = M.page_path(self.jsettings.data_dir, m)
            return {"doc": M.drawing_doc(m), "evidence": M.evidence_doc(m), "model": M.model_doc(m),
                    "sources": sources, "calls": calls, "page": str(page) if page else None}

    async def materialise(self, drawing_id: str, file_id: str, key: str) -> bool:
        meta = self._meta(drawing_id)
        ext = (meta.get("store") or {}).get("ext") or ""
        if ext not in MATERIALISE_EXT:  # DXF / IFC: ours stays the owner; the store serves the sql tool
            path = self.s.uploads_dir / drawing_id / "drawing.json"
            if path.is_file():  # only the reference the sql tool scopes by; our rooms and boxes stay
                d = json.loads(path.read_text(encoding="utf-8"))
                d["store"] = {"file_id": file_id, "drawing_key": key}
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
            await self._set_store_meta(drawing_id, state="materialised", owner="ours", **_spent(file_id, key))
            if self.on_materialised:
                self.on_materialised(drawing_id)
            return True
        await self._set_store_meta(drawing_id, state="materialising")
        built = await asyncio.to_thread(self._build, key)
        if built is None:
            await self._set_store_meta(drawing_id, state="partial")
            return False
        out_dir = self.s.uploads_dir / drawing_id
        path = out_dir / "drawing.json"
        ours = json.loads(path.read_text(encoding="utf-8"))
        if not (out_dir / "original.png").is_file():  # an SVG: no render of ours, use the store's page
            if not built.get("page"):
                await self._set_store_meta(drawing_id, state="partial")
                return False
            size = await asyncio.to_thread(_page_images, Path(built["page"]), out_dir)
            ours["images"] = {"original": f"/drawings/{drawing_id}/original.png"}
            ours["thumbnail"] = f"/drawings/{drawing_id}/thumb.png"
            ours["frame"] = dict(ours.get("frame") or {}, px=list(size))
        doc = normalise(ours, built["doc"], built["evidence"], built["model"],
                        {int(k): v for k, v in built["sources"].items()})
        doc["store"] = {"file_id": file_id, "drawing_key": key}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        raster = out_dir / "raster.json"  # an image upload's own detection gives way to the store's index
        if raster.is_file():
            os.replace(raster, out_dir / "raster.ours.json")
        await self._set_store_meta(drawing_id, state="materialised", owner="store", calls=built["calls"],
                                   counts=_counts(doc), methods=_methods(doc), **_spent(file_id, key))
        if meta.get("kind") == "svg":  # the row waited for this (no parse of ours)
            await self.writer.execute("UPDATE drawings SET status='ready', progress=NULL WHERE id=?",
                                      (drawing_id,))
        if self.on_materialised:
            self.on_materialised(drawing_id)
        return True

    # -- status of one page
    def page_stage(self, row: dict, meta: dict) -> tuple[str, dict]:
        """(stage, store meta) for one upload row; no DB call (see refresh_db_stage)."""
        st = meta.get("store") or {}
        if row["status"] == "failed":
            return "failed", st
        if meta.get("kind") == "svg" and row["status"] != "ready":  # no parse of ours: store stages
            if st.get("state") == "failed":
                return "store_failed", st
            return ("materialising" if st.get("state") == "materialising" else
                    st.get("db_stage", "reading") if st else "queued"), st
        if row["status"] in ("queued", "processing"):
            return ("queued" if row["status"] == "queued" else "parsing"), st
        if meta.get("kind") in ("dxf", "ifc") or st.get("ext") in (".dxf", ".ifc"):
            return "indexed_ours", st  # our CAD/IFC solvers own it; the store fills in silently
        state = st.get("state")
        if not st or (not self.ok and state not in ("materialised", "partial", "failed")):
            return ("reading" if self.ok else "ready_unindexed"), st
        return {"materialised": "indexed_store", "materialising": "materialising", "partial": "partial",
                "failed": "store_failed"}.get(state or "", st.get("db_stage", "reading")), st

    async def refresh_db_stage(self, drawing_id: str, meta: dict) -> str | None:
        """reading vs interpreting from the store row (registered = reading, parsed = interpreting)."""
        st = meta.get("store") or {}
        if not self.ok or st.get("state") != "registered" or not st.get("file_id"):
            return None
        status, _ = await self._db_status(st["file_id"])
        stage = {"registered": "reading", "parsed": "interpreting", "interpreted": "materialising"}.get(status or "")
        if stage and stage != st.get("db_stage"):
            await self._set_store_meta(drawing_id, db_stage=stage, t_stage=time.time())
        return stage

    async def reindex(self, page_ids: list[str]) -> int:
        """Retry (POST /drawings/{id}/reindex): every page goes through the store again; a page the
        store already has is re-followed (resubmitted when failed or only parsed)."""
        if not self.ok:
            return 0
        for did in page_ids:
            st = self._meta(did).get("store") or {}
            if st.get("file_id"):
                await self._set_store_meta(did, state="registered", error=None, db_stage="reading")
                self._spawn(self._follow(did, st["file_id"], st["drawing_key"]))
            else:
                self._spawn(self._register_page(did))
        return len(page_ids)

    # -- restart
    async def resume_all(self, rows: list[dict]) -> int:
        """Pages left unsettled by a restart: re-follow them (ingest_job and interpret are idempotent)."""
        if not self.ok:
            return 0
        n = 0
        for row in rows:
            try:
                meta = json.loads(row.get("meta") or "{}")
            except ValueError:
                continue
            st = meta.get("store") or {}
            if st.get("file_id") and st.get("state") in ("registered", "materialising"):
                self._spawn(self._follow(row["id"], st["file_id"], st["drawing_key"]))
                n += 1
            elif not st and row.get("status") == "ready":
                # ready but never handed to the store (uploaded in quick mode, or the hand-off failed)
                self._spawn(self._register_page(row["id"]))
                n += 1
        return n

    # -- delete
    async def delete(self, file_ids: list[str]) -> None:
        """Remove the upload's store project, files and page renders (as Felix's _delete_upload does).
        Bytes identical to a dataset file belong to the dataset project and are left alone."""
        if not self.ok or not file_ids:
            return
        from jstore.db import DB

        def run():
            with DB() as db:
                for fid in file_ids:
                    project = f"upload/{fid}"
                    if db.one("select 1 from jstore.project where project_id = :p", p=project) is None:
                        continue
                    db.run("delete from jstore.issue where project_id = :p", p=project)
                    db.run("delete from jstore.project where project_id = :p", p=project)
                    for d in (self.jsettings.data_dir / "uploads" / fid, self.jsettings.data_dir / "renders" / fid):
                        shutil.rmtree(d, ignore_errors=True)
        for fid in file_ids:
            fut = self.ingestor.jobs.pop(fid, None) if self.ingestor else None
            if fut is not None:
                fut.cancel()
        await asyncio.to_thread(run)


def _spent(file_id: str, key: str) -> dict:
    """Store meta fields for what the ingest jobs of this file spent on Gemini (ingest_cost ledgers)."""
    from aura.ingest_cost import spent_for

    p = spent_for(file_id, key)
    return {"cost_usd": p["cost_usd"], "metered_calls": p["calls"],
            "tokens": {"input": p["input_tokens"], "output": p["output_tokens"]},
            "by_model": p["by_model"]}


def _page_images(page: Path, out_dir: Path) -> tuple[int, int]:
    """original.png (the store's page render) and thumb.png for an upload without a render of ours."""
    from PIL import Image
    with Image.open(page) as im:
        im = im.convert("RGB")
        size = im.size
        im.save(out_dir / "original.png", optimize=True)
        im.thumbnail((640, 640))
        im.save(out_dir / "thumb.png", optimize=True)
    return size


def _methods(doc: dict) -> dict:
    """Per subject group, how its items were found (vision = a model estimate)."""
    em = doc.get("evidence_method") or {}
    out = {"rooms": em.get("room"), "doors": em.get("door"), "windows": em.get("window")}
    fx = [em[s] for s in ("washbasin", "wc", "bathtub", "shower") if s in em]
    if fx:
        out["fixtures"] = "vision" if "vision" in fx else fx[0]
    return {k: v for k, v in out.items() if v}


def _counts(doc: dict) -> dict:
    ev = doc.get("evidence") or {}
    c = {"rooms": len(doc.get("rooms") or []), "doors": len(ev.get("door") or []),
         "windows": len(ev.get("window") or []),
         "fixtures": sum(len(ev.get(s) or []) for s in ("washbasin", "wc", "bathtub", "shower")),
         "texts": len(doc.get("texts") or [])}
    if doc.get("units"):
        c["apartments"] = len(doc["units"])
    return {k: v for k, v in c.items() if v}
