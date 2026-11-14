"""Chat session history mirror: GET/POST/PATCH/DELETE /sessions.

The browser keeps the archive (localStorage `aura.sessions.v1`) and mirrors each session here, best
effort, so it survives cleared storage and restarts. Every row is scoped to an anonymous per-browser
client id (128 random bits from the browser's CSPRNG) sent in the `X-Aura-Client` header, never in
the URL; requests without a valid one are rejected. There is no account system.

Abuse bounds (a client can mint ids freely): writes are rate limited per IP, foreign Origins are
refused, the whole table is capped in rows and bytes (the least recently active browsers lose their
oldest unpinned sessions first), and rows no browser touched for 30 days are dropped on write.

Writes go through the app's single `Writer`; the table is created on first use (CREATE TABLE IF NOT
EXISTS), so `store.SCHEMA` stays untouched.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from aura.netutil import client_ip_hash
from aura.store import connect

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT NOT NULL, client_id TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  title TEXT, drawing_id TEXT NOT NULL, project_id TEXT, pinned INTEGER DEFAULT 0,
  body TEXT NOT NULL, seen_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (client_id, id)
);
CREATE INDEX IF NOT EXISTS chat_sessions_client ON chat_sessions(client_id, updated_at);
"""
#: Server time (s) of the last write; drives TTL and global eviction (the client's updated_at is
#: its own clock and is only used to order a browser's own list).
_SEEN_INDEX = "CREATE INDEX IF NOT EXISTS chat_sessions_seen ON chat_sessions(seen_at)"

#: Per-session JSON body cap (turns carry full answers with evidence and trace).
MAX_BODY_BYTES = 1_000_000
#: Sessions kept per browser; the oldest unpinned ones go first.
MAX_PER_CLIENT = 100
LIST_LIMIT = 100
#: Writes (POST/PATCH) per IP within WRITE_WINDOW_S.
WRITES_PER_IP = 120
WRITE_WINDOW_S = 60.0
#: Global bounds on the whole table.
MAX_TOTAL_ROWS = 5_000
MAX_TOTAL_BYTES = 200_000_000
#: Rows not written for this long are dropped (checked at most every TTL_SWEEP_S, on write).
TTL_S = 30 * 86_400
TTL_SWEEP_S = 600.0

_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CLIENT = re.compile(r"^[0-9a-f]{32,64}$")
_ready: set[Path] = set()

router = APIRouter(tags=["sessions"])


class SessionIn(BaseModel):
    """A session as the frontend stores it; unknown fields are kept in the body as is."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=_ID.pattern)
    drawing_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    project_id: str | None = Field(default=None, max_length=200)
    pinned: bool = False
    turns: list[Any] = Field(default_factory=list)
    created_at: float | None = None
    updated_at: float | None = None


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message, "code": code}, status_code=status)


def _db_path(request: Request) -> Path:
    return Path(request.app.state.svc.settings.db_path)


async def _write(request: Request, sql: str, params: tuple = ()) -> None:
    """Through the app's single writer when it runs (tests may mount the router without one)."""
    writer = getattr(request.app.state.svc, "writer", None)
    if writer is not None:
        await writer.execute(sql, params)
        return
    conn = connect(_db_path(request))
    try:
        conn.execute(sql, params)
    finally:
        conn.close()


async def _ensure(request: Request) -> Path:
    path = _db_path(request)
    if path not in _ready:
        for stmt in filter(None, (s.strip() for s in SESSIONS_SCHEMA.split(";"))):
            await _write(request, stmt)
        cols = {r["name"] for r in _read(path, "PRAGMA table_info(chat_sessions)")}
        if "seen_at" not in cols:  # table created by an earlier version of this module
            await _write(request, "ALTER TABLE chat_sessions ADD COLUMN seen_at REAL NOT NULL DEFAULT 0")
            # Backfill: rows that predate the column count as seen now, so the TTL sweep cannot delete them at once.
            await _write(request, "UPDATE chat_sessions SET seen_at = ? WHERE seen_at = 0", (time.time(),))
        await _write(request, _SEEN_INDEX)
        _ready.add(path)
    return path


def _state(request: Request) -> Any:
    """Per-app limiter state (kept on app.state so each app, and each test, starts clean)."""
    st = getattr(request.app.state, "sessions_limits", None)
    if st is None:
        st = {"writes": defaultdict(deque), "swept": 0.0}
        request.app.state.sessions_limits = st
    return st


def _origin_refused(request: Request) -> JSONResponse | None:
    """Same allowlist as uploads: a browser on a foreign site may not use this API."""
    settings = request.app.state.svc.settings
    origin = request.headers.get("origin")
    if not origin or origin in settings.allowed_origins:
        return None
    rx = settings.allowed_origin_regex
    if rx and re.fullmatch(rx, origin):
        return None
    return _err("bad_request", "Sessions are only accepted from the AURA app.", 403)


def _rate_limited(request: Request) -> JSONResponse | None:
    ip = client_ip_hash(request, request.app.state.svc.settings)
    if not ip:
        return None
    q, now = _state(request)["writes"][ip], time.time()
    while q and now - q[0] > WRITE_WINDOW_S:
        q.popleft()
    if len(q) >= WRITES_PER_IP:
        return _err("rate_limited", "Too many session writes from this address; slow down.", 429)
    q.append(now)
    return None


async def _sweep(request: Request, path: Path) -> None:
    """TTL (at most every TTL_SWEEP_S) and the global row/byte cap, after a write."""
    st, now = _state(request), time.time()
    if now - st["swept"] >= TTL_SWEEP_S:
        st["swept"] = now
        await _write(request, "DELETE FROM chat_sessions WHERE seen_at > 0 AND seen_at < ?", (now - TTL_S,))
    total = _read(path, "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(body)), 0) AS b FROM chat_sessions")[0]
    rows_over, bytes_over = total["n"] - MAX_TOTAL_ROWS, total["b"] - MAX_TOTAL_BYTES
    if rows_over <= 0 and bytes_over <= 0:
        return
    # Least recently active browsers first; their oldest unpinned sessions first, pinned last.
    candidates = _read(
        path,
        "SELECT s.client_id, s.id, LENGTH(s.body) AS n FROM chat_sessions s JOIN ("
        "SELECT client_id, MAX(seen_at) AS last FROM chat_sessions GROUP BY client_id) c "
        "ON c.client_id = s.client_id ORDER BY s.pinned ASC, c.last ASC, s.seen_at ASC LIMIT ?",
        (max(rows_over, 0) + 500,),
    )
    for row in candidates:
        if rows_over <= 0 and bytes_over <= 0:
            break
        await _write(request, "DELETE FROM chat_sessions WHERE client_id = ? AND id = ?",
                     (row["client_id"], row["id"]))
        rows_over -= 1
        bytes_over -= row["n"]


def _read(path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = connect(path, readonly=True)
    try:
        return list(conn.execute(sql, params))
    finally:
        conn.close()


def _valid_id(v: str) -> bool:
    return bool(_ID.match(v))


def _client(value: str | None) -> str | None:
    """The caller's client id from `X-Aura-Client`, or None when missing or malformed."""
    return value if value and _CLIENT.match(value) else None


def _no_client() -> JSONResponse:
    return _err("unauthorized", "Missing or invalid X-Aura-Client header.", 401)


@router.get("/sessions")
async def list_sessions(request: Request, drawing_id: str | None = None,
                        project_id: str | None = None,
                        x_aura_client: str | None = Header(default=None)) -> Any:
    if (refused := _origin_refused(request)) is not None:
        return refused
    client_id = _client(x_aura_client)
    if client_id is None:
        return _no_client()
    path = await _ensure(request)
    sql = "SELECT body FROM chat_sessions WHERE client_id = ?"
    params: list[Any] = [client_id]
    if project_id:
        sql += " AND (project_id = ? OR drawing_id = ?)"
        params += [project_id, drawing_id or ""]
    elif drawing_id:
        sql += " AND drawing_id = ?"
        params.append(drawing_id)
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    params.append(LIST_LIMIT)
    rows = _read(path, sql, tuple(params))
    return {"sessions": [json.loads(r["body"]) for r in rows]}


@router.post("/sessions")
async def upsert_session(request: Request, body: SessionIn,
                         x_aura_client: str | None = Header(default=None)) -> Any:
    """Create or replace a session (the frontend mirrors with POST: PATCH is not in the CORS list)."""
    if (refused := _origin_refused(request)) is not None:
        return refused
    client_id = _client(x_aura_client)
    if client_id is None:
        return _no_client()
    if (limited := _rate_limited(request)) is not None:
        return limited
    data = body.model_dump()
    data.pop("client_id", None)
    raw = json.dumps(data, separators=(",", ":"))
    if len(raw) > MAX_BODY_BYTES:
        return _err("too_large", "Session too large to mirror.", 413)
    path = await _ensure(request)
    now = time.time() * 1000  # ms, like the frontend's Date.now()
    created = body.created_at or now
    updated = body.updated_at or now
    await _write(
        request,
        "INSERT INTO chat_sessions(id, client_id, created_at, updated_at, title, drawing_id, "
        "project_id, pinned, body, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(client_id, id) DO UPDATE SET updated_at = excluded.updated_at, "
        "title = excluded.title, drawing_id = excluded.drawing_id, "
        "project_id = excluded.project_id, pinned = excluded.pinned, body = excluded.body, "
        "seen_at = excluded.seen_at",
        (body.id, client_id, created, updated, body.title, body.drawing_id, body.project_id,
         int(body.pinned), raw, time.time()),
    )
    # Keep each browser's archive bounded: drop its oldest unpinned sessions beyond the cap.
    await _write(
        request,
        "DELETE FROM chat_sessions WHERE client_id = ? AND pinned = 0 AND id IN ("
        "SELECT id FROM chat_sessions WHERE client_id = ? AND pinned = 0 "
        "ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
        (client_id, client_id, MAX_PER_CLIENT),
    )
    await _sweep(request, path)
    return {"id": body.id, "updated_at": updated}


@router.patch("/sessions/{session_id}")
async def patch_session(request: Request, session_id: str, body: SessionPatch,
                        x_aura_client: str | None = Header(default=None)) -> Any:
    if (refused := _origin_refused(request)) is not None:
        return refused
    client_id = _client(x_aura_client)
    if client_id is None:
        return _no_client()
    if (limited := _rate_limited(request)) is not None:
        return limited
    if not _valid_id(session_id):
        return _err("bad_request", "Invalid session id.", 400)
    path = await _ensure(request)
    rows = _read(path, "SELECT body FROM chat_sessions WHERE client_id = ? AND id = ?",
                 (client_id, session_id))
    if not rows:
        return _err("not_found", "No such session.", 404)
    data = json.loads(rows[0]["body"])
    if body.title is not None:
        data["title"] = body.title.strip() or data.get("title", "")
        data["customTitle"] = bool(body.title.strip())
    if body.pinned is not None:
        data["pinned"] = body.pinned
    data["updated_at"] = time.time() * 1000
    await _write(
        request,
        "UPDATE chat_sessions SET title = ?, pinned = ?, updated_at = ?, body = ?, seen_at = ? "
        "WHERE client_id = ? AND id = ?",
        (data.get("title", ""), int(bool(data.get("pinned"))), data["updated_at"],
         json.dumps(data, separators=(",", ":")), time.time(), client_id, session_id),
    )
    return data


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str,
                         x_aura_client: str | None = Header(default=None)) -> Any:
    if (refused := _origin_refused(request)) is not None:
        return refused
    client_id = _client(x_aura_client)
    if client_id is None:
        return _no_client()
    if not _valid_id(session_id):
        return _err("bad_request", "Invalid session id.", 400)
    await _ensure(request)
    await _write(request, "DELETE FROM chat_sessions WHERE client_id = ? AND id = ?",
                 (client_id, session_id))
    return {"deleted": session_id}
