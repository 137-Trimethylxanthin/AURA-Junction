"""Upload a manifest of dataset files through the running backend and record what happened to each.

    python -I backend/scripts/upload_matrix.py MANIFEST.json OUT.md [--base http://localhost:8000]
                                               [--limit N] [--deadline 900] [--parallel 2] [--ask]

MANIFEST.json is a list of {folder, type, ext, path}. Every file is sent to POST /uploads exactly as the
browser does, then GET /drawings/{id}/status is polled until a final state. The table written to OUT.md has
one row per file: final status and stage, seconds to "ready" and to the final stage, counts, ingest calls and
cost when the backend reports them, and the refusal message when the upload was rejected. Delete tokens are
saved next to OUT.md (mode 600) so the uploads can be removed later; they are never printed.

With --ask, one uncached question is asked on every upload that became ready ("How many rooms are there?"),
and its answer, method, route, model calls and latency are added to the row.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ORIGIN = "http://localhost:5173"  # the upload route checks the Origin header like a browser request
FINAL_STAGES = {"indexed_store", "indexed_ours", "ready_unindexed", "partial", "store_failed", "failed"}
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "pdf": "application/pdf",
        "dxf": "application/dxf", "dwg": "application/acad", "ifc": "application/x-step", "svg": "image/svg+xml"}


def _req(method: str, url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: float = 120):
    req = urllib.request.Request(url, data=data, method=method, headers={"Origin": ORIGIN, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"error": str(e)}


def _multipart(path: Path, ext: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {MIME.get(ext, 'application/octet-stream')}\r\n\r\n").encode()
    return head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def run_one(base: str, item: dict, deadline_s: float, ask: bool) -> dict:
    path, ext = Path(item["path"]), item["ext"]
    row = {"folder": item["folder"], "ext": ext, "file": path.name, "bytes": path.stat().st_size}
    body, ctype = _multipart(path, ext)
    t0 = time.time()
    code, resp = _req("POST", f"{base}/uploads", data=body, headers={"Content-Type": ctype}, timeout=300)
    if code != 202:
        row.update(result="refused", http=code, message=str(resp.get("error") or resp.get("message") or resp)[:160])
        return row
    did, row["drawing_id"], row["_token"] = resp["drawing_id"], resp["drawing_id"], resp.get("delete_token")
    ready_at = None
    status: dict = {}
    while time.time() - t0 < deadline_s:
        code, status = _req("GET", f"{base}/drawings/{did}/status", timeout=30)
        st, stage = status.get("status"), status.get("stage")
        if st == "ready" and ready_at is None:
            ready_at = time.time() - t0
        if st == "failed" or stage in FINAL_STAGES or (st == "ready" and stage is None):
            break
        time.sleep(2)
    else:
        row["result"] = "too_slow"
    row.setdefault("result", status.get("stage") or status.get("status") or "unknown")
    row.update(status=status.get("status"), stage=status.get("stage"), ready_s=round(ready_at, 1) if ready_at else None,
               final_s=round(time.time() - t0, 1), counts=status.get("counts"), pages=len(status.get("pages") or []) or None,
               ingest_calls=status.get("ingest_calls"), ingest_cost_usd=status.get("ingest_cost_usd"),
               error=(status.get("error") or status.get("note") or "")[:140] or None)
    if ask and status.get("status") == "ready":
        ta = time.time()
        code, ans = _req("POST", f"{base}/ask", data=json.dumps(
            {"drawing_id": did, "question": "How many rooms are there?"}).encode(),
            headers={"Content-Type": "application/json"}, timeout=200)
        a = ans.get("answer") or {}
        row.update(q_http=code, q_value=str(a.get("value"))[:40], q_kind=a.get("kind"), q_method=ans.get("method"),
                   q_route=ans.get("route"), q_calls=ans.get("calls"), q_s=round(time.time() - ta, 1))
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("out")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--deadline", type=float, default=900.0)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--ask", action="store_true")
    args = ap.parse_args()

    items = json.loads(Path(args.manifest).read_text())
    if args.limit:
        items = items[: args.limit]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        rows = list(pool.map(lambda it: run_one(args.base, it, args.deadline, args.ask), items))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tokens = {r["drawing_id"]: r.pop("_token") for r in rows if r.get("_token")}
    tok_path = out.with_suffix(".tokens.json")
    tok_path.write_text(json.dumps(tokens))
    os.chmod(tok_path, 0o600)

    cols = ["folder", "ext", "file", "bytes", "result", "status", "stage", "ready_s", "final_s", "pages", "counts",
            "ingest_calls", "ingest_cost_usd", "error", "message"]
    if args.ask:
        cols += ["q_value", "q_method", "q_route", "q_calls", "q_s"]
    lines = [f"# Upload matrix ({len(rows)} files, {round(time.time() - t0)} s wall clock)", "",
             "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(
            (json.dumps(r[c]) if isinstance(r.get(c), dict) else str(r.get(c, "") if r.get(c) is not None else ""))
            .replace("|", "/") for c in cols) + " |")
    calls = sum(r.get("ingest_calls") or 0 for r in rows)
    cost = sum(r.get("ingest_cost_usd") or 0 for r in rows)
    by: dict[str, int] = {}
    for r in rows:
        by[r["result"]] = by.get(r["result"], 0) + 1
    lines += ["", f"Results: {json.dumps(by)}", f"Ingest model calls reported: {calls}; cost reported: ${cost:.4f}"]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(rows)} rows): {by}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
