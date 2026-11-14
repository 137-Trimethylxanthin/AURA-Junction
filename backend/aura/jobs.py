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


class Jobs:
    def __init__(self, writer: Writer, *, concurrency: int = 2,
                 join_timeout_s: float = JOIN_TIMEOUT_S, target=None):
        self.writer = writer
        self.sem = asyncio.Semaphore(concurrency)
        self.join_timeout_s = join_timeout_s
        self.target = target  # test hook: replaces the child entry point
        self.tasks: set[asyncio.Task] = set()
        self.on_done = None  # callback(drawing_id) after a job settles

    def submit(self, drawing_id: str, kind: str, src: Path, out: Path, meta: dict) -> None:
        t = asyncio.create_task(self._run(drawing_id, kind, src, out, meta),
                                name=f"parse-{drawing_id}")
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _set(self, drawing_id: str, status: str, progress: float | None = None,
                   error: str | None = None) -> None:
        await self.writer.execute(
            "UPDATE drawings SET status=?, progress=?, error=? WHERE id=?",
            (status, progress, error, drawing_id))

    async def _run(self, drawing_id: str, kind: str, src: Path, out: Path, meta: dict) -> None:
        async with self.sem:
            await self._set(drawing_id, "processing", 0.1)
            t0 = time.monotonic()
            kw = {"target": self.target} if self.target else {}
            proc, conn = parse_worker.start(kind, src, out, drawing_id, meta, **kw)
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
                if res.get("ok"):
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
