"""Layer 2: turn the Layer 1 mirror of one drawing into entities, claims and issues.

    run(db, drawing_key) -> {"room": n, "door": n, ..., "issues": n, "failed": [...]}

Order: CAD files (plan.interpret), IFC models (ifc.run, when that module exists), PDF twins (plan.interpret_pdf),
raster twins (raster.interpret), then reconcile.  Every reader hands over plain records; `merge` below is the only
place that writes entity / entity_source / claim, so observations of a second source land on the entities of the
first (matched by number, then by position) instead of creating the same room twice.

A record: {"kind", "number", "label", "label_norm", "zone", "host", "geom" (shapely, shared frame, metres),
           "attrs", "ifc_class", "duplicate" (index of the first identical record of the same batch),
           "sources": [(ref_table, ref_key, role, bbox, def_id)],
           "claims":  [(property, value_num, value_text, unit, method, source index or None, confidence)]}
"""
from __future__ import annotations

import importlib
import os
import traceback

from ..db import DB, arr, geom, js

CAD = ("dxf", "dwg")
RASTER = ("png", "jpg")
ORDER = {"dxf": 0, "dwg": 1, "ifc": 2, "pdf": 3, "png": 4, "jpg": 5, "svg": 6}


def define(db: DB, file_id: str, concept: str, method: str, spec: dict, confidence: float | None,
           created_by: str = "jstore.interpret.plan") -> int:
    """Record how a concept is derived for one file.  -> def_id"""
    return db.one("""insert into jstore.definition (file_id, concept, method, spec, confidence, created_by)
                     values (:f, :c, :m, cast(:s as jsonb), :conf, :by) returning def_id""",
                  f=file_id, c=concept, m=method, s=js(spec), conf=confidence, by=created_by)["def_id"]


def issue(db: DB, f: dict, kind: str, detail: str, entity_id: int | None = None, prop: str | None = None) -> None:
    db.run("""insert into jstore.issue (project_id, drawing_key, entity_id, property, kind, detail)
              values (:p, :d, :e, :prop, :k, :t)""",
           p=f["project_id"], d=f["drawing_key"], e=entity_id, prop=prop, k=kind, t=detail[:2000])


def _ids(db: DB, seq: str, n: int) -> list[int]:
    if not n:
        return []
    return [r["id"] for r in db.run(f"select nextval('{seq}') as id from generate_series(1, :n)", n=n)]


def merge(db: DB, f: dict, recs: list[dict], registered: bool = False) -> dict:
    """Write the records of one source file.  -> {"new": n, "matched": n, "unlinked": n}

    A record joins an existing entity of the drawing (read from another file) when the room number / apartment id
    is the same, or - with `registered` (the file has a real frame) - when it lies on it.  Records that match
    nothing become new entities, unless another file already supplied entities of that kind and the record cannot
    be placed: creating it would count the thing twice, so it is reported as an issue instead.
    """
    old = db.run("""select entity_id, kind, number, encode(st_asbinary(geom), 'hex') as wkb from jstore.entity
                    where drawing_key = :d and status = 'active' order by entity_id""", d=f["drawing_key"])
    by_no = {(o["kind"], o["number"]): o["entity_id"] for o in reversed(old) if o["number"]}
    kinds_before = {o["kind"] for o in old}
    shapes = None
    if registered and old:
        from shapely import from_wkb
        shapes = [(o, from_wkb(o["wkb"])) for o in old if o["wkb"]]
    target: list[int | None] = []
    unlinked: dict[str, int] = {}
    taken: set[int] = set()
    for r in recs:
        eid = None
        if r["kind"] in ("room", "apartment") and r.get("number"):
            eid = by_no.get((r["kind"], r["number"]))
        if eid is None and shapes and r.get("geom") is not None:
            p = r["geom"].representative_point()
            near = [(g.distance(p), o["entity_id"]) for o, g in shapes
                    if o["kind"] == r["kind"] and o["entity_id"] not in taken and g.distance(p) < 0.3]
            if near:
                eid = min(near)[1]
        if eid is None and r["kind"] in kinds_before:
            unlinked[r["kind"]] = unlinked.get(r["kind"], 0) + 1
            target.append(-1)
            continue
        if eid is not None:
            taken.add(eid)
        target.append(eid)
    new = [i for i, t in enumerate(target) if t is None]
    for i, eid in zip(new, _ids(db, "jstore.entity_entity_id_seq", len(new))):
        target[i] = eid
    db.copy("jstore.entity", ["entity_id", "project_id", "drawing_key", "storey", "ifc_class", "kind", "number", "label",
                              "label_norm", "zone", "host", "geom", "attrs", "duplicate_of"],
            [(target[i], f["project_id"], f["drawing_key"], f.get("storey"), r.get("ifc_class"), r["kind"],
              r.get("number"), r.get("label"), r.get("label_norm"), r.get("zone"), r.get("host"), geom(r.get("geom")),
              js(r.get("attrs") or {}), target[r["duplicate"]] if r.get("duplicate") is not None else None)
             for i, r in ((i, recs[i]) for i in new)])
    kept = [(r, t) for r, t in zip(recs, target) if t != -1]
    sids = iter(_ids(db, "jstore.entity_source_id_seq", sum(len(r["sources"]) for r, _ in kept)))
    srows, crows = [], []
    for r, eid in kept:
        mine = []
        for table, key, role, bbox, def_id in r["sources"]:
            sid = next(sids)
            mine.append(sid)
            srows.append((sid, eid, f["file_id"], r.get("page_no", 0), table, key, role, arr(bbox), def_id))
        for prop, num, text, unit, method, si, conf in r.get("claims", ()):
            crows.append((eid, prop, num, text, unit, f["file_id"], method, mine[si] if si is not None else None, conf))
    db.copy("jstore.entity_source", ["id", "entity_id", "file_id", "page_no", "ref_table", "ref_key", "role", "bbox",
                                     "def_id"], srows)
    db.copy("jstore.claim", ["entity_id", "property", "value_num", "value_text", "unit", "file_id", "method",
                             "source_id", "confidence"], crows)
    for kind, n in unlinked.items():
        issue(db, f, "unlinked", f"{n} {kind} record(s) read from {f['path']} could not be matched to the entities "
                                 f"of the other source files (no number in common, no registration) and were not stored")
    return dict(new=len(new), matched=len(kept) - len(new), unlinked=sum(unlinked.values()))


def clear(db: DB, drawing_key: str) -> None:
    """Remove everything Layer 2 holds for a drawing."""
    db.run("delete from jstore.entity where drawing_key = :d", d=drawing_key)      # cascades to sources and claims
    db.run("delete from jstore.issue where drawing_key = :d", d=drawing_key)
    db.run("""delete from jstore.definition where file_id in
              (select file_id from jstore.source_file where drawing_key = :d)""", d=drawing_key)


def _has(db: DB, table: str, file_id: str) -> bool:
    return db.one(f"select exists (select from jstore.{table} where file_id = :f) as x", f=file_id)["x"]


def run(db: DB, drawing_key: str) -> dict:
    """(Re)build Layer 2 for one drawing from its Layer 1 rows.  Idempotent; one failing source does not stop the rest."""
    from . import plan, reconcile
    files = db.run("select * from jstore.source_file where drawing_key = :d", d=drawing_key)
    files.sort(key=lambda f: (ORDER.get(f["format"], 9), f["path"]))
    clear(db, drawing_key)
    failed = []

    def attempt(f, what, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - keep the other sources of the drawing
            failed.append(f"{f['path'] if f else what}: {e!r}"[:300])
            if f:
                issue(db, f, "interpret_failed", f"{what}: {e!r}\n{traceback.format_exc()[-1200:]}")

    for f in files:
        if f["format"] in CAD and _has(db, "cad_entity", f["file_id"]):
            attempt(f, "cad", lambda f=f: plan.interpret(db, f))
    ifcs = [f for f in files if f["format"] == "ifc"]
    if ifcs:
        try:
            ifc = importlib.import_module("jstore.interpret.ifc")
        except ModuleNotFoundError as e:
            if e.name != "jstore.interpret.ifc":      # the module exists but one of its imports does not
                failed.append(f"ifc: {e!r}")
            ifc = None
        if ifc is not None:
            attempt(None, "ifc", lambda: ifc.run(db, drawing_key, ifcs))
    for f in files:
        if f["format"] == "pdf" and _has(db, "pdf_text", f["file_id"]) and _has(db, "pdf_path", f["file_id"]):
            attempt(f, "pdf", lambda f=f: plan.interpret_pdf(db, f))
    for f in files:
        # The first raster reader (tuned on rendered CAD plans; slow, adds nothing next to a CAD or PDF twin) is
        # opt-in: JSTORE_RASTER_LEGACY=1.  raster_rooms below is the default reader for scans.
        if os.environ.get("JSTORE_RASTER_LEGACY") == "1" and f["format"] in RASTER \
                and _has(db, "raster_word", f["file_id"]):
            from . import raster
            attempt(f, "raster", lambda f=f: raster.interpret(db, f))
    # Readers for drawings that have no CAD or model twin; each is its own optional module:
    #   pdf_rooms.interpret(db, f)     rooms from the vector linework and texts of a PDF page
    #   raster_rooms.interpret(db, f)  rooms from the pixels and OCR of a scan
    #   symbols.interpret(db, f)       doors, windows and fixtures detected on the page image of a PDF or scan
    #   raster_plan.interpret(db, f)   walls, a nominal scale where the sheet states none, openings on the wall line
    # They decide themselves whether there is something to add (e.g. nothing when the twin already gave it).
    #   dimensions.interpret(db, f)    dimension lines, room sizes, areas and levels printed on the sheet
    #   raster_plan.interpret(db, f)   vector plan of an image-only drawing (nominal scale, walls, openings)
    for name, formats in (("pdf_rooms", ("pdf",)), ("raster_rooms", RASTER), ("symbols", ("pdf",) + RASTER),
                          ("dimensions", CAD + ("pdf",) + RASTER), ("raster_plan", RASTER)):
        try:
            mod = importlib.import_module(f"jstore.interpret.{name}")
        except ModuleNotFoundError as e:
            if e.name != f"jstore.interpret.{name}":
                failed.append(f"{name}: {e!r}")
            continue
        for f in files:
            if f["format"] in formats:
                attempt(f, name, lambda f=f, mod=mod: mod.interpret(db, f))
    if files:
        attempt(files[0], "reconcile", lambda: reconcile.run(db, drawing_key))
    out = {r["kind"]: r["n"] for r in db.run(
        "select kind, count(*) as n from jstore.entity where drawing_key = :d group by 1", d=drawing_key)}
    out["issues"] = db.one("select count(*) as n from jstore.issue where drawing_key = :d", d=drawing_key)["n"]
    if failed:
        out["failed"] = failed
    return out
