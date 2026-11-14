"""Uploads and registered files: where the bytes live, and the worker processes that ingest them."""
from __future__ import annotations

import asyncio
import concurrent.futures as cf
import multiprocessing
import re
import secrets
import shutil
from pathlib import Path

from .. import registry
from ..db import DB
from .config import ALLOWED_EXT, Settings

ID_RE = re.compile(r"^[0-9a-f]{16}$")


class NotFound(Exception):
    """Unknown id or a path that does not exist inside its container."""


class Forbidden(Exception):
    """A path that points outside the directory it must stay in."""


def new_id() -> str:
    return secrets.token_hex(8)


def check_id(value: str) -> str:
    """Ids become directory names and SQL literals, so anything that is not one of ours is simply unknown."""
    if not ID_RE.match(value or ""):
        raise NotFound(value)
    return value


def contained(root: Path, candidate: str | Path) -> Path:
    """Resolve `candidate` below `root` (symlinks first) and insist the result is inside `root`."""
    root = root.resolve()
    try:
        p = (root / candidate).resolve()
    except (OSError, ValueError, RuntimeError) as e:  # NUL bytes, symlink loops
        raise Forbidden(str(candidate)) from e
    if p != root and root not in p.parents:
        raise Forbidden(str(candidate))
    return p


def extension(name: str) -> str | None:
    ext = Path(name).suffix.lower().lstrip(".")
    return ext if ext in ALLOWED_EXT else None


def safe_name(name: str) -> str:
    """An upload's file name as a path component: no separators, no leading dot, no comma (scope lists are comma-separated)."""
    stem = re.sub(r"[^\w.\- ]+", "_", Path(name).stem, flags=re.UNICODE).strip(" .")[:80] or "upload"
    return f"{stem}{Path(name).suffix.lower()}"


def locate(s: Settings, path: str, meta: dict | None = None) -> Path | None:
    """The original bytes of a source_file row: uploads live below the store directory, everything else below
    the dataset folder (source_file.path is relative to one of them)."""
    for rel in [path, *((meta or {}).get("aliases") or [])]:
        for root in (s.data_dir, s.dataset_dir):
            try:
                p = contained(root, rel)
            except Forbidden:
                continue
            if p.is_file():
                return p
    return None


def ingest_job(file_id: str, path: str) -> dict:
    """Layer 1 for one file, then Layer 2 for its drawing (runs in a worker process)."""
    from .. import pipeline
    out = pipeline.parse_file(file_id, path)
    if out["status"] == "parsed":
        with DB() as db:
            key = db.one("select drawing_key from jstore.source_file where file_id = :f", f=file_id)["drawing_key"]
        out["interpret"] = pipeline.interpret_drawing(key)
    return out


class Ingestor:
    """Background ingestion in worker processes (parsers are CPU-bound and must not block the event loop)."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.pool: cf.ProcessPoolExecutor | None = None
        self.jobs: dict[str, cf.Future] = {}

    def submit(self, file_id: str, path: Path) -> None:
        job = self.jobs.get(file_id)
        if job is not None and not job.done():
            return
        if self.pool is None:  # spawn: a fork of a process with threads and an event loop is not safe
            self.pool = cf.ProcessPoolExecutor(self.s.ingest_workers, mp_context=multiprocessing.get_context("spawn"))
        self.jobs[file_id] = self.pool.submit(ingest_job, file_id, str(path))

    def pending(self, file_ids: list[str]) -> list[str]:
        return [f for f in file_ids if f in self.jobs and not self.jobs[f].done()]

    async def wait(self, file_ids: list[str], timeout: float) -> None:
        futs = [asyncio.wrap_future(self.jobs[f]) for f in self.pending(file_ids)]
        if futs:
            await asyncio.wait(futs, timeout=timeout)

    def ensure(self, row: dict) -> bool:
        """Start ingestion when the file has not been parsed yet (or failed before).  -> started"""
        if row["status"] not in ("registered", "failed"):
            return False
        path = locate(self.s, row["path"], row.get("meta"))
        if path is None:
            return False
        self.submit(row["file_id"], path)
        return True

    def shutdown(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)


def store_upload(s: Settings, tmp: Path, original_name: str) -> dict:
    """Move a finished upload into uploads/<file_id>/ and register it as its own project.  -> source_file row"""
    file_id = registry.sha256_of(tmp)[:16]
    name = safe_name(original_name)
    with DB() as db:
        row = db.one("select * from jstore.source_file where file_id = :f", f=file_id)
        if row is not None and locate(s, row["path"], row["meta"]) is not None:  # same bytes are in the store already
            tmp.unlink(missing_ok=True)
            return row
        dst = s.data_dir / "uploads" / file_id / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), dst)
        rel = str(dst.relative_to(s.data_dir))
        if row is not None:  # registered before, bytes gone: point the row at the new copy
            db.run("update jstore.source_file set path = :p where file_id = :f", p=rel, f=file_id)
            return db.one("select * from jstore.source_file where file_id = :f", f=file_id)
        # a drawing of its own: an upload must never join a dataset drawing just because the names match
        return registry.register(db, dst, rel, overrides=dict(
            project_id=f"upload/{file_id}", source="upload", drawing_key=f"{Path(name).stem}~{file_id[:8]}"))


def register_path(s: Settings, path: str) -> dict:
    """Register a file that already lies inside the dataset folder.  -> source_file row"""
    p = contained(s.dataset_dir, path)
    if not p.is_file():
        raise NotFound(path)
    if extension(p.name) is None:
        raise ValueError(f"extension not allowed: {p.suffix or '(none)'}")
    with DB() as db:
        return registry.register(db, p, str(p.relative_to(s.dataset_dir.resolve())))
