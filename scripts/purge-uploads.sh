#!/usr/bin/env bash
# Delete every upload, in our backend (SQLite rows, upload folders, answers, caches, conversations) and in the
# drawing store (Postgres upload/<file_id> projects with their files, Layer-1 rows, entities, renders).
#   scripts/purge-uploads.sh          # DRY RUN: lists what would be deleted, changes nothing
#   scripts/purge-uploads.sh --yes    # deletes; the backend on :8000 must be stopped first (its in-memory
#                                     # caches and SQLite writer would otherwise outlive the rows)
# Deletion goes through the backend's own code path: aura.main._remove_upload (rows, folder, caches, and the
# ref-counted store delete DrawingStore.delete). Store projects no upload of ours references any more are then
# removed with the same DrawingStore.delete. Never touched: the dataset projects (backend/build/store-map.json),
# the scratch database dev_api and the old standalone API's volume jstore_store (both only listed).
# Secrets are read from files and never printed.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${ROOT}/backend/.data"
YES=0
case "${1:-}" in --yes) YES=1 ;; "") ;; *) echo "usage: $0 [--yes]" >&2; exit 2 ;; esac

if [ "${YES}" = 1 ] && curl -s -m 2 -o /dev/null http://127.0.0.1:8000/health; then
  echo "the backend answers on 127.0.0.1:8000: stop it first, then run --yes again" >&2; exit 1
fi

secret() {  # secret <file> <store/.env key>: the value, never printed
  if [ -s "$1" ]; then tr -d '\r\n' < "$1"
  elif [ -f "${ROOT}/store/.env" ]; then sed -n "s/^$2=//p" "${ROOT}/store/.env" | tail -n1; fi
}
PG_PW="$(secret "${DATA}/pg-password" JSTORE_DB_PASSWORD)"
if [ -n "${PG_PW}" ]; then
  export AURA_STORE_DSN="postgresql://postgres:${PG_PW}@127.0.0.1:${AURA_PG_PORT:-54329}/postgres"
  RP="$(secret "${DATA}/pg-reader-password" JSTORE_READER_PASSWORD)"
  [ -z "${RP}" ] || export AURA_STORE_READER_PASSWORD="${RP}"
  unset RP
fi
unset PG_PW
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}" PURGE_YES="${YES}" PURGE_ROOT="${ROOT}"

cd "${ROOT}/backend"
uv run --quiet python - <<'PY'
import asyncio, json, os, shutil, sqlite3, subprocess
from pathlib import Path

from aura.config import get_settings

YES = os.environ["PURGE_YES"] == "1"
root = Path(os.environ["PURGE_ROOT"])
s = get_settings()
dataset_ids = {f["sha256_16"] for d in json.loads((root / "backend/build/store-map.json").read_text()).values()
               for f in d["files"]} if (root / "backend/build/store-map.json").exists() else set()


def du(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def ours() -> dict:
    if not s.db_path.exists():
        return dict(rows=[], refs={}, dirs=[], size=0, derived={})
    c = sqlite3.connect(f"file:{s.db_path}?mode=ro", uri=True)
    rows = [r[0] for r in c.execute("SELECT id FROM drawings WHERE id LIKE 'up-%' ORDER BY t")]
    refs = {}
    for did, meta in c.execute("SELECT id, meta FROM drawings WHERE id LIKE 'up-%'"):
        try:
            fid = (json.loads(meta or "{}").get("store") or {}).get("file_id")
        except ValueError:
            fid = None
        if fid:
            refs.setdefault(fid, []).append(did)
    derived = {t: c.execute(f"SELECT count(*) FROM {t} WHERE drawing_id LIKE 'up-%'").fetchone()[0]
               for t in ("answers", "conversations", "check_cache", "answer_cache", "chat_sessions", "jobs")}
    derived["turns"] = c.execute("SELECT count(*) FROM turns WHERE conversation_id IN "
                                 "(SELECT id FROM conversations WHERE drawing_id LIKE 'up-%')").fetchone()[0]
    derived["projects(proj-*)"] = c.execute("SELECT count(*) FROM projects WHERE id LIKE 'proj-%'").fetchone()[0]
    c.close()
    dirs = sorted(p for p in s.uploads_dir.glob("up-*") if p.is_dir()) if s.uploads_dir.is_dir() else []
    return dict(rows=rows, refs=refs, dirs=dirs, size=sum(du(p) for p in dirs), derived=derived)


def store_side() -> dict | None:
    if s.store_dsn is None:
        return None
    from aura.drawing_store import configure_env
    configure_env(s)                      # the environment before the first jstore import
    from jstore.db import DB
    with DB() as db:
        projects = db.run("""select p.project_id, f.file_id, f.format, coalesce(f.bytes, 0) as bytes, f.drawing_key
                             from jstore.project p left join jstore.source_file f using (project_id)
                             where p.project_id like 'upload/%' order by p.project_id""")
        l1 = {t: db.one(f"""select count(*) n from jstore.{t} x join jstore.source_file f using (file_id)
                            where f.project_id like 'upload/%'""")["n"]
              for t in ("page", "cad_entity", "pdf_path", "pdf_text", "raster_word", "raster_detection", "ifc_element")}
        ents = db.one("""select count(*) n from jstore.entity e where e.drawing_key in
                         (select drawing_key from jstore.source_file where project_id like 'upload/%')""")["n"]
        dataset = db.one("select count(*) n from jstore.source_file where project_id not like 'upload/%'")["n"]
        dbs = [r["datname"] for r in db.run("select datname from pg_database where not datistemplate")]
    data = s.store_data_dir
    files = {}
    for r in projects:
        fid = r["file_id"] or r["project_id"].split("/", 1)[1]
        files[fid] = r
    disk = sum(du(data / "uploads" / f) + du(data / "renders" / f) for f in files)
    return dict(files=files, l1=l1, entities=ents, dataset_files=dataset, dbs=dbs,
                bytes=sum(r["bytes"] for r in files.values()), disk=disk)


def report(tag: str) -> tuple[dict, dict | None]:
    o, st = ours(), store_side()
    print(f"[{tag}] ours: {len(o['rows'])} upload rows, {len(o['dirs'])} folders ({mb(o['size'])}); derived rows "
          + json.dumps({k: v for k, v in o["derived"].items() if v}))
    if st is None:
        print(f"[{tag}] store: AURA_STORE_DSN unavailable (no pg-password / store/.env): store side skipped")
    else:
        print(f"[{tag}] store: {len(st['files'])} upload projects ({mb(st['bytes'])} source, {mb(st['disk'])} on disk "
              f"in {s.store_data_dir}); Layer-1 rows {json.dumps({k: v for k, v in st['l1'].items() if v})}; "
              f"entities {st['entities']}; dataset files kept {st['dataset_files']}")
    return o, st


o, st = report("before")
print("ours, upload ids: " + " ".join(o["rows"]))
orphan_dirs = [p.name for p in o["dirs"] if p.name not in set(o["rows"])]
if orphan_dirs:
    print("ours, folders without a row: " + " ".join(orphan_dirs))
if st is not None:
    referenced = set(o["refs"])
    print("store, upload file ids referenced by our uploads: "
          + " ".join(f"{f}<-{','.join(o['refs'][f])}" for f in sorted(set(st["files"]) & referenced)))
    unref = sorted(set(st["files"]) - referenced)
    print("store, upload projects no upload of ours references (removed too): " + " ".join(unref))
    clash = sorted(set(st["files"]) & dataset_ids)
    if clash:
        print("store, upload ids that are ALSO dataset files (skipped): " + " ".join(clash))
    print("not touched, listed only: databases " + ", ".join(st["dbs"])
          + ("  <- dev_api is the test scratch database; drop only on request" if "dev_api" in st["dbs"] else ""))
vol = subprocess.run(["podman", "volume", "inspect", "jstore_store", "--format", "{{.Mountpoint}}"],
                     capture_output=True, text=True)
if vol.returncode == 0:
    print("not touched, listed only: podman volume jstore_store (the stopped standalone API's uploads and renders)")

if not YES:
    print("DRY RUN: nothing was deleted; run with --yes to delete")
    raise SystemExit(0)


async def purge() -> None:
    from aura import tools
    from aura.cache import AnswerCache, CheckCache
    from aura.drawing_store import DrawingStore
    from aura.main import _remove_upload
    from aura.pipeline import Services
    from aura.store import Store, init_db
    from aura.writer import Writer

    init_db(s.db_path)
    store = Store(s.db_path)
    writer = Writer(s.db_path)
    await writer.start()
    ds = DrawingStore(s, writer, store)
    if s.store_dsn is not None:
        await ds.start()
    svc = Services(settings=s, budget=None, client=None,  # type: ignore[arg-type]
                   registry=tools.Registry(s.drawing_dirs(), {}, uploads_dir=s.uploads_dir), store=store,
                   writer=writer, answers=AnswerCache(ttl_s=s.upload_ttl_s, store=store, writer=writer),
                   checks=CheckCache(store, writer), agent=None)
    try:
        ids = [r["id"] for r in store.upload_rows() if r["id"].startswith("up-")]
        for did in ids:
            if store.drawing_row(did) is not None:   # a sibling page may already be gone with its primary
                await _remove_upload(svc, did, ds if ds.available() else None)
        # aura's removal does not cover chat_sessions (per-drawing chat history of the upload page)
        await writer.execute("DELETE FROM chat_sessions WHERE drawing_id LIKE 'up-%'")
        for p in s.uploads_dir.glob("up-*") if s.uploads_dir.is_dir() else []:
            shutil.rmtree(p, ignore_errors=True)      # folders without a row
        if ds.available():
            left = store_side() or {"files": {}}
            await ds.delete(sorted(set(left["files"]) - dataset_ids))
    finally:
        await ds.close()
        await writer.stop()


asyncio.run(purge())
report("after")
PY
