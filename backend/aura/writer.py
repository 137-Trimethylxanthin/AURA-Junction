"""The single DB writer: an asyncio queue drained by one task, each batch in BEGIN IMMEDIATE.

`submit(sql, params)` is fire-and-forget (safe from any thread); `execute(...)` awaits the
commit. On Modal, the volume is committed every `commit_every_s` and on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from aura.store import connect

log = logging.getLogger("aura.writer")

_STOP = object()


class Writer:
    def __init__(
        self,
        path: Path,
        *,
        volume_commit: Callable[[], None] | None = None,
        commit_every_s: float = 60.0,
    ):
        self.path = path
        self._volume_commit = volume_commit
        self._every = commit_every_s
        self._queue: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._conn: sqlite3.Connection | None = None
        self._dirty = False
        self._last_volume_commit = time.monotonic()
        self._lock = threading.Lock()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._conn = connect(self.path)
        self._task = asyncio.create_task(self._run(), name="aura-writer")

    def submit(self, sql: str, params: tuple = ()) -> None:
        """Queue a write from any thread or coroutine; does not wait."""
        if self._loop is None or self._queue is None:
            raise RuntimeError("writer not started")
        item = (sql, params, None)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._queue.put_nowait(item)
        else:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    async def execute(self, sql: str, params: tuple = ()) -> None:
        assert self._queue is not None
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((sql, params, fut))
        await fut

    async def flush(self) -> None:
        await self.execute("SELECT 1")

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self._every)
            except TimeoutError:
                self._maybe_volume_commit(force=False)
                continue
            if item is _STOP:
                break
            batch = [item]
            while not self._queue.empty() and len(batch) < 200:
                nxt = self._queue.get_nowait()
                if nxt is _STOP:
                    self._queue.put_nowait(_STOP)
                    break
                batch.append(nxt)
            await asyncio.to_thread(self._apply, batch)
            self._maybe_volume_commit(force=False)

    def _apply(self, batch: list) -> None:
        assert self._conn is not None
        conn = self._conn
        err: BaseException | None = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            for sql, params, _ in batch:
                conn.execute(sql, params)
            conn.execute("COMMIT")
            self._dirty = True
        except BaseException as e:
            err = e
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            log.exception("writer batch failed (%d statements)", len(batch))
        for _, _, fut in batch:
            if fut is not None and self._loop is not None:
                if err is None:
                    self._loop.call_soon_threadsafe(_set_result, fut)
                else:
                    self._loop.call_soon_threadsafe(_set_exc, fut, err)

    def _maybe_volume_commit(self, *, force: bool) -> None:
        if self._volume_commit is None or not self._dirty:
            return
        if force or time.monotonic() - self._last_volume_commit >= self._every:
            try:
                self._volume_commit()
                self._dirty = False
            except Exception:
                log.exception("volume commit failed")
            self._last_volume_commit = time.monotonic()

    async def stop(self) -> None:
        if self._task is None or self._queue is None:
            return
        await self._queue.put(_STOP)
        await self._task
        self._task = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._maybe_volume_commit(force=True)


def _set_result(fut: asyncio.Future) -> None:
    if not fut.done():
        fut.set_result(None)


def _set_exc(fut: asyncio.Future, e: BaseException) -> None:
    if not fut.done():
        fut.set_exception(e)
