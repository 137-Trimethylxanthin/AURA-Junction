"""Layer 0: which files exist, and which belong together.

Grouping uses only file names and folders (inventory heuristics, kept out of every reader):
  * same stem, other extension            -> the same drawing in another format
  * `<model>_plan-03-level-1.pdf`         -> a view of `<model>.ifc` on storey "level-1"
  * `<name>_model.ifc` / `<name>_plan.png`-> model and plan of one building
  * `site12_bldg34_floor56.*`             -> project site12/bldg34, storey floor56
  * `<set>_sheet_0003.jpg`                -> one sheet of a set
  * a project folder below the source     -> every file in it is one project
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from .db import DB, js

FORMATS = {".ifc": "ifc", ".dxf": "dxf", ".dwg": "dwg", ".pdf": "pdf", ".png": "png", ".jpg": "jpg",
           ".jpeg": "jpg", ".svg": "svg"}
DISCIPLINE = r"(arc|str|mep|electrical|facade|kitchen|plumbing|ventilation|heating)"


def classify(rel: str) -> dict:
    """Relative path -> project, drawing key and storey."""
    p = PurePosixPath(rel.replace("\\", "/"))
    parts = p.parts
    i = 1 if parts and parts[0] == "data" else 0
    source = parts[i] if len(parts) > i + 1 else "files"
    inner = parts[i + 1:] if len(parts) > i + 1 else parts[-1:]
    stem = p.stem
    storey = view = None
    if m := re.fullmatch(r"(site\d+)_(bldg\d+)_(floor\d+)", stem):
        project, drawing, storey = f"{source}/{m[1]}/{m[2]}", stem, m[3]
    elif m := re.fullmatch(r"(.+?)_(plan|elevation|section)(?:-(\d+))?(?:-(.+))?", stem):
        project, drawing, view = f"{source}/{m[1]}", m[1], m[2]
        storey = m[4] if m[2] == "plan" else None
    elif m := re.fullmatch(r"(.+?)_model", stem):
        project, drawing = f"{source}/{m[1]}", m[1]
    elif m := re.fullmatch(r"(.+?)_sheet_(\d+)", stem):
        project, drawing = f"{source}/{m[1]}", stem
    elif m := re.fullmatch(rf"(.+?)_{DISCIPLINE}", stem):
        project, drawing = f"{source}/{m[1]}", stem
    elif len(inner) > 1:
        project, drawing = f"{source}/{inner[0]}", stem
    else:
        project, drawing = f"{source}/{stem}", stem
    return dict(project_id=project, drawing_key=drawing, storey=storey, source=source, view=view, title=stem)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_files(root: Path) -> list[Path]:
    """Every drawing file below root (or root itself), sorted."""
    if root.is_file():
        return [root]
    out = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if Path(f).suffix.lower() in FORMATS:
                out.append(Path(dp) / f)
    return sorted(out)


def register(db: DB, path: Path, rel: str, overrides: dict | None = None) -> dict:
    """Insert (or return) the project and source_file rows for one file.  -> the source_file row."""
    fmt = FORMATS[path.suffix.lower()]
    digest = sha256_of(path)
    file_id = digest[:16]
    c = classify(rel)
    c.update(overrides or {})
    db.run("insert into jstore.project (project_id, title, source) values (:p, :t, :s) on conflict do nothing",
           p=c["project_id"], t=c["project_id"].split("/", 1)[-1], s=c["source"])
    row = db.one("select * from jstore.source_file where file_id = :f", f=file_id)
    if row:  # same bytes seen before (dated copies in project folders): remember the other path
        if row["path"] != rel:
            aliases = sorted(set(row["meta"].get("aliases", [])) | {rel})
            db.run("update jstore.source_file set meta = meta || cast(:m as jsonb) where file_id = :f",
                   m=js({"aliases": aliases}), f=file_id)
        return row
    meta = {"view": c["view"]} if c.get("view") else {}
    db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, sha256, drawing_key, storey, meta)
              values (:f, :p, :path, :fmt, :b, :h, :d, :st, cast(:m as jsonb))""",
           f=file_id, p=c["project_id"], path=rel, fmt=fmt, b=path.stat().st_size, h=digest,
           d=c["drawing_key"], st=c["storey"], m=js(meta))
    return db.one("select * from jstore.source_file where file_id = :f", f=file_id)
