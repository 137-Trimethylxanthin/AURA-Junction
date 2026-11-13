"""Structured JSON logs: one line per HTTP request and one per model request.

Never logged: question text, raw IP, user agent, headers, secrets.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send

from aura.budget import ip_hash

access = logging.getLogger("aura.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"t": round(record.created, 3), "level": record.levelname, "logger": record.name}
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            base.update(extra)
        else:
            base["msg"] = record.getMessage()
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


def setup_logging() -> None:
    root = logging.getLogger("aura")
    if any(getattr(h, "_aura", False) for h in root.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    h._aura = True  # type: ignore[attr-defined]
    root.addHandler(h)
    root.setLevel(logging.INFO)
    root.propagate = False


class AccessLogMiddleware:
    """Pure ASGI (does not buffer streaming responses)."""

    def __init__(self, app: ASGIApp, salt: str):
        self.app = app
        self.salt = salt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        status = {"code": 500}

        async def _send(msg: dict) -> None:
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            client = scope.get("client")
            access.info("", extra={"fields": {
                "kind": "http",
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status": status["code"],
                "ms": int((time.perf_counter() - t0) * 1000),
                "ip_hash": ip_hash(client[0] if client else None, self.salt),
            }})


class RequestLogFile:
    """Appends model-request records to <logs>/requests.jsonl."""

    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / "requests.jsonl"

    def write(self, row: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(row) + "\n")
