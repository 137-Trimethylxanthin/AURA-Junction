"""Background parse jobs: at most 2 at a time, each in a killable child process.

Status lives in the `drawings` table (queued → processing → ready | failed); clients poll
GET /drawings/{id}/status. A job never runs inside a web request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aura import parse_worker
from aura.writer import Writer

log = logging.getLogger("aura.jobs")

JOIN_TIMEOUT_S = 60.0
DETECT_TIMEOUT_S = 120.0  # measured: median 16 s, max 57 s per image (CubiCasa eval)


class Jobs:
    def __init__(self, writer: Writer, *, concurrency: int = 2,
                 join_timeout_s: float = JOIN_TIMEOUT_S, target=None):
        self.writer = writer
        self.sem = asyncio.Semaphore(concurrency)
        self.join_timeout_s = join_timeout_s
        self.target = target  # test hook: replaces the child entry point
        self.tasks: set[asyncio.Task] = set()
        self.on_done = None  # callback(drawing_id) after a job settles
        # async (drawing_id, out_dir, ctx) -> error str | None; runs after a raster parse
        # (vision detection). Never inside a web request.
        self.after_raster = None

    def submit(self, drawing_id: str, kind: str, src: Path, out: Path, meta: dict,
               ctx: dict | None = None) -> None:
        t = asyncio.create_task(self._run(drawing_id, kind, src, out, meta, ctx or {}),
                                name=f"parse-{drawing_id}")
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _set(self, drawing_id: str, status: str, progress: float | None = None,
                   error: str | None = None) -> None:
        await self.writer.execute(
            "UPDATE drawings SET status=?, progress=?, error=? WHERE id=?",
            (status, progress, error, drawing_id))

    async def _register_pdf_bundle(self, drawing_id: str, meta: dict, ctx: dict, res: dict) -> None:
        """Persist sibling PDF floor drawings + auto-matched ProjectBundle before ready."""
        import json

        project = res.get("project")
        siblings = res.get("siblings") or []
        if not project and not siblings:
            return
        now = time.time()
        del_hash = ctx.get("delete_token_hash")
        exp = ctx.get("expires_at")
        if project:
            p0_floor = next(
                (f for f in project.get("floors", []) if f.get("drawing_id") == drawing_id), None
            )
            p0_meta = {
                **meta,
                "source_ext": ".pdf",
                "project_id": project["id"],
                **({"floor_label": p0_floor["label"], "floor_order": p0_floor["order"]} if p0_floor else {}),
            }
            await self.writer.execute(
                "UPDATE drawings SET meta=? WHERE id=?",
                (json.dumps(p0_meta), drawing_id),
            )
        for sib in siblings:
            await self.writer.execute(
                "INSERT OR REPLACE INTO drawings(id, t, kind, status, progress, title, "
                "delete_token_hash, expires_at, meta) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sib["id"],
                    now,
                    sib.get("kind", "raster"),
                    "ready",
                    1.0,
                    sib["title"],
                    del_hash,
                    exp,
                    json.dumps(sib["meta"]),
                ),
            )
        if project:
            await self.writer.execute(
                "INSERT OR REPLACE INTO projects(id, t, title, floors, auto_matched, body) "
                "VALUES (?,?,?,?,?,?)",
                (
                    project["id"],
                    now,
                    project["title"],
                    json.dumps(project["floors"]),
                    1 if project.get("auto_matched") else 0,
                    json.dumps(project),
                ),
            )

    async def _run(self, drawing_id: str, kind: str, src: Path, out: Path, meta: dict,
                   ctx: dict) -> None:
        async with self.sem:
            await self._set(drawing_id, "processing", 0.1)
            t0 = time.monotonic()
            kw = {"target": self.target} if self.target else {}
            try:
                proc, conn = parse_worker.start(kind, src, out, drawing_id, meta, **kw)
            except Exception:
                log.exception("could not start the parser")
                await self._set(drawing_id, "failed", None, "The parser could not start.")
                return
            try:
                await asyncio.to_thread(proc.join, self.join_timeout_s)
                if proc.is_alive():
                    proc.kill()
                    await asyncio.to_thread(proc.join, 5)
                    await self._set(drawing_id, "failed", None,
                                    f"Parsing took longer than {int(self.join_timeout_s)} s.")
                    log.warning("upload parse killed after %.0f s", time.monotonic() - t0)
                    return
                res = conn.recv() if conn.poll() else {
                    "ok": False, "error": "The parser stopped unexpectedly (memory or CPU limit)."}
                if res.get("ok") and kind == "raster" and self.after_raster is not None:
                    await self._set(drawing_id, "processing", 0.5)
                    try:
                        async with asyncio.timeout(DETECT_TIMEOUT_S):
                            err = await self.after_raster(drawing_id, out, ctx)
                    except TimeoutError:
                        err = f"Detection took longer than {int(DETECT_TIMEOUT_S)} s."
                    except Exception as e:
                        log.exception("raster detection failed")
                        err = f"Detection failed ({type(e).__name__})."
                    if err:
                        res = {"ok": False, "error": err}
                if res.get("ok"):
                    await self._register_pdf_bundle(drawing_id, meta, ctx, res)
                    await self._set(drawing_id, "ready", 1.0)
                else:
                    await self._set(drawing_id, "failed", None, str(res.get("error"))[:300])
            finally:
                if proc.is_alive():  # cancelled (shutdown): never leave a child running
                    proc.kill()
                conn.close()
                if self.on_done:
                    self.on_done(drawing_id)

    async def cancel_all(self) -> None:
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
