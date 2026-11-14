#!/usr/bin/env bash
# Parse (Layer 1 only, no interpretation, no model calls) the source files of the bundled drawings into the
# drawing store's Postgres (jstore_db_1, see scripts/store.sh), using jstore as a library from the backend's venv.
# Idempotent: files already parsed are skipped; `--redo` parses them again.
#   scripts/ingest-bundled.sh            # writes backend/build/store-map.json and prints timings / row counts
#   scripts/ingest-bundled.sh --redo
# Swiss drawings: the DXF plus its PDF and PNG twins; IFC drawings: the model file. Paths are stored relative to
# data/junction-dataset (the same convention as the store service's dataset mount). No secret is printed.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/store/.env"
[ -f "${ENV_FILE}" ] || { echo "store/.env missing: run scripts/store.sh start first" >&2; exit 1; }
podman ps --filter name=jstore_db_1 --filter status=running --format '{{.Names}}' | grep -q jstore_db_1 \
  || { echo "jstore_db_1 is not running (scripts/store.sh start, or podman start jstore_db_1)" >&2; exit 1; }

DB_PW="$(sed -n 's/^JSTORE_DB_PASSWORD=//p' "${ENV_FILE}" | tail -n1)"
export JSTORE_READER_PASSWORD="$(sed -n 's/^JSTORE_READER_PASSWORD=//p' "${ENV_FILE}" | tail -n1)"
export JSTORE_DSN="postgresql://postgres:${DB_PW}@127.0.0.1:54329/postgres"
unset DB_PW
export JSTORE_DATA_DIR="${AURA_STORE_DATA_DIR:-${ROOT}/backend/.data/store}"   # page renders; the backend's default
export JSTORE_DATASET_DIR="${ROOT}/data/junction-dataset"
export JSTORE_SHELL=off OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
export INGEST_ROOT="${ROOT}" INGEST_REDO="$([ "${1:-}" = "--redo" ] && echo 1 || echo 0)"
mkdir -p "${JSTORE_DATA_DIR}"

cd "${ROOT}/backend"
exec uv run --quiet python - <<'PY'
import json, os, time, urllib.request
from pathlib import Path

root = Path(os.environ["INGEST_ROOT"])
base = root / "data" / "junction-dataset"                 # stored paths are relative to this (data/<source>/...)
live = root / "backend" / "build" / "drawings-live"
redo = os.environ["INGEST_REDO"] == "1"

from jstore import pipeline, registry                      # after the environment is set
from jstore.db import DB
from jstore.service.store import COUNTS

# the bundled drawing ids: the backend's library if it answers, else the build folder
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/drawings", timeout=5) as f:
        ids = [d["id"] for d in json.load(f)["drawings"]]
except Exception:
    ids = sorted(p.name for p in live.iterdir() if (p / "drawing.json").exists())

mapping, unmapped = {}, []
for did in ids:
    files = []
    if did.startswith("swiss-"):
        floor = did.split("-", 1)[1]
        for ext in ("dxf", "pdf", "png"):
            files += [(p, ext) for p in sorted((base / "data" / "swiss_dwellings").glob(f"*_floor{floor}.{ext}"))]
    elif did.startswith("ifc-"):
        src = json.loads((live / did / "model.json").read_text())["source"]
        files += [(p, "ifc") for p in sorted((base / "data").glob(f"*/{src}"))]
    if not files:
        unmapped.append(did)
        continue
    mapping[did] = {"files": [{"path": str(p.relative_to(base)), "sha256_16": registry.sha256_of(p)[:16], "role": r}
                              for p, r in files]}

t0 = time.time()
todo, seen = [], set()
with DB() as db:
    for did, m in mapping.items():
        for f in m["files"]:
            row = registry.register(db, base / f["path"], f["path"])
            m["project"], f["drawing_key"] = row["project_id"], row["drawing_key"]
            if row["file_id"] not in seen and (redo or row["status"] in ("registered", "failed")):
                todo.append((row["file_id"], str(base / f["path"])))
            seen.add(row["file_id"])
print(f"{len(ids)} drawings, {len(mapping)} mapped, {len(seen)} files, {len(todo)} to parse"
      + (f", unmapped: {unmapped}" if unmapped else ""), flush=True)

results = {}
def done(job, r):
    r = r or dict(file_id=job[0], status="failed", seconds=None)
    results[job[0]] = r
    print(f"  {r.get('format', '?'):4} {job[0]} {r['status']:7} {r.get('seconds')} s  {Path(job[1]).name}", flush=True)

if todo:
    # parse_file only: Layer 1 (+ page renders); never interpret_drawing, so no model is called
    pipeline.run_pool(pipeline.parse_file, todo, int(os.environ.get("AURA_STORE_WORKERS", "2")), done)
wall = time.time() - t0

per_fmt = {}
with DB() as db:
    for did, m in mapping.items():
        for f in m["files"]:
            fid = f["sha256_16"]
            s = db.one("select status, format, error from jstore.source_file where file_id = :f", f=fid)
            c = db.one("select " + ", ".join(f"(select count(*) from jstore.{t} where file_id = :f) as {t}"
                                            for t in COUNTS), f=fid)
            f["status"], f["counts"] = s["status"], {k: v for k, v in c.items() if v}
            if s["error"]:
                f["error"] = s["error"].splitlines()[0][:200]
            if fid in results and results[fid].get("seconds") is not None:
                agg = per_fmt.setdefault(s["format"], [0, 0.0])
                agg[0] += 1; agg[1] += results[fid]["seconds"]

out = root / "backend" / "build" / "store-map.json"
out.write_text(json.dumps(mapping, indent=1) + "\n")
print(f"wall {wall:.1f} s for {len(todo)} parsed;  per format (files, summed worker seconds): "
      + json.dumps({k: [n, round(s, 1)] for k, (n, s) in per_fmt.items()}))
for did, m in mapping.items():
    for f in m["files"]:
        print(f"  {did:52} {f['role']:4} {f['status']:10} {json.dumps(f['counts'])}" + (f"  ERROR {f['error']}" if f.get("error") else ""))
print(f"mapping: {out}")
PY
