"""What the drawing store's ingestion spends on Gemini, per uploaded file.

jstore calls Gemini with `urllib.request.urlopen` from its ingest worker processes (symbol detection in
`jstore.gemini_detect`, room reading in `jstore.interpret.raster_rooms`). Nothing in jstore is changed:
our worker pool (`drawing_store.make_pool`) runs every job through `metered_call`, which meters each
Gemini response in that process (one job per process at a time, so the process-wide ledger belongs to
that job even though jstore fans out over threads) and writes the ledger to
`<JSTORE_DATA_DIR>/usage/<job key>.json`. Materialising a drawing sums the ledgers of its file and its
drawing key and prices them with `budget.PRICES`.

Prices: `budget.PRICES`, USD per 1M tokens, read from the official Gemini API pricing page
(https://ai.google.dev/gemini-api/docs/pricing) on 13 Nov 2026; recorded in
`.omc/research/stoa/stoa-research.md` section 9 and `.omc/research/gemini/gemini-apis.md`.
gemini-4-carbon is $2 / $10 until 30 Nov 2026, then $4 / $20: update PRICES on 1 Dec.

Billing rules used:
- generateContent: input = `promptTokenCount` (+ `toolUsePromptTokenCount`); output =
  `candidatesTokenCount` + `thoughtsTokenCount` (thinking is billed as output).
- Decisions API (`/decisions`): input = `usage.total_input_tokens`, no output tokens.
- A response that is not HTTP 200 carries no usage and is counted as a call with 0 tokens.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Self

from aura.budget import cost_usd

GEMINI_HOST = "generativelanguage.googleapis.com"
_MODEL_IN_PATH = re.compile(r"/models/([^/:]+):")

_lock = threading.Lock()
_ledger: dict[str, dict[str, int]] | None = None  # model -> {calls, input, output}; None = not metering
_installed = False


def usage_of(url: str, request_body: bytes | None, status: int, body: bytes) -> tuple[str, int, int] | None:
    """(model, input tokens, output tokens) of one Gemini response; None when it is not a Gemini call."""
    if GEMINI_HOST not in url:
        return None
    m = _MODEL_IN_PATH.search(url)
    model = m.group(1) if m else ""
    try:
        js = json.loads(body) if status == 200 else {}
    except ValueError:
        js = {}
    if not model and request_body:
        try:
            model = str(json.loads(request_body).get("model") or "")
        except (ValueError, AttributeError):
            model = ""
    model = model or "unknown"
    if not isinstance(js, dict):
        return model, 0, 0
    if "usageMetadata" in js:
        u = js.get("usageMetadata") or {}
        tin = int(u.get("promptTokenCount") or 0) + int(u.get("toolUsePromptTokenCount") or 0)
        tout = int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)
        return model, tin, tout
    u = js.get("usage") or {}
    return model, int(u.get("total_input_tokens") or 0), int(u.get("total_output_tokens") or 0)


def _record(model: str, tin: int, tout: int) -> None:
    with _lock:
        if _ledger is None:
            return
        row = _ledger.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        row["calls"] += 1
        row["input_tokens"] += tin
        row["output_tokens"] += tout


class _Replay:
    """The response after we read it: what jstore uses of it (status, read, context manager)."""

    def __init__(self, resp: Any, body: bytes):
        self._resp, self._buf = resp, io.BytesIO(body)
        self.status = getattr(resp, "status", 200)
        self.headers = getattr(resp, "headers", None)

    def read(self, *a: Any) -> bytes:
        return self._buf.read(*a)

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        self._resp.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def install() -> None:
    """Wrap urllib.request.urlopen in this process (idempotent). Only Gemini URLs are touched."""
    global _installed
    if _installed:
        return
    orig = urllib.request.urlopen

    def urlopen(req: Any, *a: Any, **kw: Any) -> Any:
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        if GEMINI_HOST not in url or _ledger is None:
            return orig(req, *a, **kw)
        data = req.data if isinstance(req, urllib.request.Request) else None
        try:
            resp = orig(req, *a, **kw)
        except urllib.error.HTTPError as e:
            u = usage_of(url, data if isinstance(data, bytes) else None, e.code, b"")
            if u:
                _record(*u)
            raise
        body = resp.read()
        u = usage_of(url, data if isinstance(data, bytes) else None, getattr(resp, "status", 200), body)
        if u:
            _record(*u)
        return _Replay(resp, body)

    urllib.request.urlopen = urlopen
    _installed = True


def usage_dir() -> Path:
    return Path(os.environ.get("JSTORE_DATA_DIR") or ".") / "usage"


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]


def metered_call(fn: Any, args: tuple, kwargs: dict) -> Any:
    """Run one ingest job (in a worker process) and write what it spent to usage/<first arg>.json."""
    global _ledger
    install()
    with _lock:
        _ledger = {}
    try:
        return fn(*args, **kwargs)
    finally:
        with _lock:
            spent, _ledger = _ledger or {}, None
        if args and spent:
            d = usage_dir()
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{_safe(str(args[0]))}.json"
            prev = load_ledger(p)
            for model, row in spent.items():  # a re-run (retry, resume) adds to what was spent before
                acc = prev.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
                for k in acc:
                    acc[k] += row[k]
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(prev))
            os.replace(tmp, p)


def load_ledger(path: Path) -> dict[str, dict[str, int]]:
    try:
        v = json.loads(path.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def price(ledger: dict[str, dict[str, int]]) -> dict[str, Any]:
    """{calls, input_tokens, output_tokens, cost_usd, by_model} of a ledger, priced with budget.PRICES."""
    out: dict[str, Any] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                           "by_model": {}}
    for model, row in sorted(ledger.items()):
        c = cost_usd(model, row["input_tokens"], row["output_tokens"])
        out["by_model"][model] = {**row, "cost_usd": round(c, 6)}
        out["calls"] += row["calls"]
        out["input_tokens"] += row["input_tokens"]
        out["output_tokens"] += row["output_tokens"]
        out["cost_usd"] += c
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out


def spent_for(*keys: str) -> dict[str, Any]:
    """Priced usage of the ingest jobs keyed by any of `keys` (file id, drawing key)."""
    merged: dict[str, dict[str, int]] = {}
    for k in keys:
        if not k:
            continue
        for model, row in load_ledger(usage_dir() / f"{_safe(k)}.json").items():
            acc = merged.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            for f in acc:
                acc[f] += int(row.get(f) or 0)
    return price(merged)


def totals() -> dict[str, Any]:
    """All ingestion spend recorded on this server (every ledger, deleted uploads included)."""
    merged: dict[str, dict[str, int]] = {}
    for p in usage_dir().glob("*.json"):
        for model, row in load_ledger(p).items():
            acc = merged.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            for f in acc:
                acc[f] += int(row.get(f) or 0)
    t = price(merged)
    return {"ingest_calls_total": t["calls"], "ingest_cost_usd_total": t["cost_usd"]}
