"""Ingestion: register files, parse each into Layer 1, then interpret each drawing into Layer 2.

Layer 1 readers:   jstore.ingest.<format>.ingest(db, file_id, path) -> meta dict
Layer 2:           jstore.interpret.run(db, drawing_key) -> summary dict
Both are idempotent: they delete what they wrote before for that file or drawing.
"""
from __future__ import annotations

import concurrent.futures as cf
import importlib
import os
import resource
import signal
import time
import traceback
from pathlib import Path

from .db import DB, js
from . import registry

READERS = {"dxf": "jstore.ingest.dxf", "dwg": "jstore.ingest.dxf", "ifc": "jstore.ingest.ifc",
           "pdf": "jstore.ingest.pdf", "png": "jstore.ingest.raster", "jpg": "jstore.ingest.raster",
           "svg": "jstore.ingest.svg"}


def store_dir() -> Path:
    """Where renders, tiles and uploads live."""
    d = Path(os.environ.get("JSTORE_DATA_DIR", Path(__file__).resolve().parents[1] / ".data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_file(file_id: str, path: str) -> dict:
    """Layer 1 for one registered file (runs in a worker process)."""
    t = time.time()
    with DB() as db:
        row = db.one("select format from jstore.source_file where file_id = :f", f=file_id)
        fmt = row["format"]
        try:
            mod = importlib.import_module(READERS[fmt])
        except (KeyError, ModuleNotFoundError):
            db.run("update jstore.source_file set status = 'registered', error = :e where file_id = :f",
                   e=f"no reader for {fmt} yet", f=file_id)
            return dict(file_id=file_id, format=fmt, status="skipped", seconds=0.0)
        try:
            meta = mod.ingest(db, file_id, Path(path)) or {}
            try:  # page images for viewing and evidence; a failed render must not lose the parsed data
                from . import render
                meta["render"] = render.render_file(db, file_id, Path(path))
            except Exception as e:  # noqa: BLE001
                meta["render"] = {"error": repr(e)[:300]}
            db.run("""update jstore.source_file set status = 'parsed', error = null, ingested_at = now(),
                      meta = meta || cast(:m as jsonb) where file_id = :f""", m=js(meta), f=file_id)
            status = "parsed"
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
            db.run("update jstore.source_file set status = 'failed', error = :e where file_id = :f",
                   e=(repr(e) + "\n" + traceback.format_exc())[-1500:], f=file_id)
            status = "failed"
    return dict(file_id=file_id, format=fmt, status=status, seconds=round(time.time() - t, 2))


class TimeLimit(BaseException):
    """Interpreting one drawing took longer than JSTORE_INTERPRET_TIMEOUT_S."""


def _limits() -> None:
    """Per worker: a time limit that raises inside the job, and a memory cap that turns a runaway job into a
    MemoryError instead of an out-of-memory kill of whatever process the kernel picks."""
    def stop(*_):
        raise TimeLimit("time limit")
    signal.signal(signal.SIGALRM, stop)
    cap = int(os.environ.get("JSTORE_INTERPRET_MEM_MB", "4096")) << 20
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (cap, cap))
    except (ValueError, OSError):
        pass


def interpret_drawing(drawing_key: str) -> dict:
    """Layer 2 for one drawing (all its source files together)."""
    t = time.time()
    try:
        mod = importlib.import_module("jstore.interpret")
        run = mod.run
    except (ModuleNotFoundError, AttributeError):
        return dict(drawing=drawing_key, status="skipped", seconds=0.0)
    _limits()
    with DB() as db:
        try:
            signal.alarm(int(os.environ.get("JSTORE_INTERPRET_TIMEOUT_S", "180")))
            try:  # place PDF, SVG and raster pages in the drawing's shared frame before reading them
                from . import frames
                frames.register_drawing(db, drawing_key)
            except Exception as e:  # noqa: BLE001
                frame_error = repr(e)[:300]
            else:
                frame_error = None
            out = run(db, drawing_key) or {}
            if frame_error:
                out["frame_error"] = frame_error
            db.run("update jstore.source_file set status = 'interpreted' where drawing_key = :d and status = 'parsed'",
                   d=drawing_key)
            return dict(drawing=drawing_key, status="interpreted", seconds=round(time.time() - t, 2), **out)
        except BaseException as e:  # noqa: BLE001 - also the time limit and MemoryError; Layer 1 stays usable
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                db.close()  # the connection may be mid-statement; the next job opens its own
            except Exception:  # noqa: BLE001
                pass
            return dict(drawing=drawing_key, status="failed", error=repr(e)[:300], seconds=round(time.time() - t, 2))
        finally:
            signal.alarm(0)



def run_pool(fn, jobs: list[tuple], workers: int, on_result) -> None:
    """Run fn(*job) for every job in worker processes.  A worker that dies (out of memory ...) breaks the whole
    pool, so the unfinished jobs are then run again one process at a time; a job that dies alone is reported as
    on_result(job, None)."""
    left = list(jobs)
    while left:  # a round ends when every job is done or the pool broke; go on while rounds make progress
        before = len(left)
        with cf.ProcessPoolExecutor(workers) as ex:
            futs = {ex.submit(fn, *j): j for j in left}
            for fu in cf.as_completed(futs):
                try:
                    r = fu.result()
                except cf.process.BrokenProcessPool:
                    continue
                except Exception:  # noqa: BLE001
                    r = None
                left.remove(futs[fu])
                on_result(futs[fu], r)
        if len(left) > before - max(1, workers):
            break
    for j in left:  # the pool broke: isolate each remaining job
        try:
            with cf.ProcessPoolExecutor(1) as ex:
                r = ex.submit(fn, *j).result()
        except Exception:  # noqa: BLE001
            r = None
        on_result(j, r)


def ingest(root: Path, rel_base: Path | None = None, only: set[str] | None = None, limit: int | None = None,
           workers: int = 4, interpret: bool = True, redo: bool = False, log=print) -> dict:
    """Register and parse every drawing file below `root`, then interpret every drawing touched."""
    files = registry.find_files(root)
    if only:
        files = [f for f in files if registry.FORMATS[f.suffix.lower()] in only]
    files = files[:limit]
    base = rel_base or (root if root.is_dir() else root.parent)
    todo, drawings = [], set()
    with DB() as db:
        for f in files:
            row = registry.register(db, f, str(f.relative_to(base)))
            drawings.add(row["drawing_key"])
            if redo or row["status"] in ("registered", "failed"):
                todo.append((row["file_id"], str(f)))
    log(f"{len(files)} files registered, {len(todo)} to parse, {len(drawings)} drawings")
    stats: dict = {"files": len(files), "parsed": 0, "failed": 0, "skipped": 0}
    t0 = time.time()
    if todo:
        n = [0]

        def parsed(job, r):
            r = r or dict(status="failed")
            if r["status"] == "failed" and "file_id" not in r:  # the worker died: record it on the file
                with DB() as db:
                    db.run("update jstore.source_file set status = 'failed', error = 'worker died' where file_id = :f",
                           f=job[0])
            stats[r["status"]] = stats.get(r["status"], 0) + 1
            n[0] += 1
            if n[0] % 25 == 0 or n[0] == len(todo):
                log(f"  parsed {n[0]}/{len(todo)}  ({time.time() - t0:.0f}s)  {stats}")

        run_pool(parse_file, todo, workers, parsed)
    if interpret:
        stats["drawings"] = interpret_all(sorted(drawings), workers, log)
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def interpret_all(keys: list[str], workers: int = 4, log=print) -> dict:
    """Layer 2 for many drawings.  -> counts by status, with the drawings that failed."""
    t1 = time.time()
    done: dict = {"interpreted": 0, "failed": 0, "skipped": 0, "failures": []}
    n = [0]

    def result(job, r):
        r = r or dict(status="failed", drawing=job[0], error="worker died")
        done[r["status"]] = done.get(r["status"], 0) + 1
        if r["status"] == "failed":
            done["failures"].append([r.get("drawing"), r.get("error")])
            log(f"  failed {r.get('drawing')}: {r.get('error')}")
        n[0] += 1
        if n[0] % 25 == 0 or n[0] == len(keys):
            log(f"  interpreted {n[0]}/{len(keys)}  ({time.time() - t1:.0f}s)  "
                f"{ {k: v for k, v in done.items() if k != 'failures'} }")

    run_pool(interpret_drawing, [(k,) for k in keys], workers, result)
    return done
