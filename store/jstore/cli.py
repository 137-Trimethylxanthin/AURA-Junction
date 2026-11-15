"""Command line.

    python -m jstore.cli init                         create or update the schema
    python -m jstore.cli ingest <folder or file> [--base DIR] [--only dxf,ifc] [--limit N] [--workers N]
                                [--no-interpret] [--redo]
    python -m jstore.cli interpret [--drawing KEY]    (re)build Layer 2
    python -m jstore.cli status                       what is in the store
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import pipeline
from .db import DB, apply_schema


def status() -> dict:
    with DB() as db:
        files = db.run("""select format, status, count(*) n from jstore.source_file group by 1, 2 order by 1, 2""")
        counts = {}
        for t in ("project", "source_file", "page", "frame", "cad_entity", "pdf_path", "pdf_text", "raster_word",
                  "raster_detection", "ifc_element", "ifc_property", "ifc_relation", "ifc_geometry", "definition",
                  "entity", "entity_source", "claim", "issue"):
            counts[t] = db.one(f"select count(*) n from jstore.{t}")["n"]
        kinds = db.run("select kind, count(*) n from jstore.entity group by 1 order by 2 desc")
        failed = db.run("select path, left(error, 160) error from jstore.source_file where status = 'failed' limit 10")
    return dict(files=files, rows=counts, entities=kinds, failed=failed)


def main() -> None:
    ap = argparse.ArgumentParser(prog="jstore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    i = sub.add_parser("ingest")
    i.add_argument("root")
    i.add_argument("--base", help="folder that stored paths are relative to (default: root)")
    i.add_argument("--only", help="comma-separated formats")
    i.add_argument("--limit", type=int)
    i.add_argument("--workers", type=int, default=4)
    i.add_argument("--no-interpret", action="store_true")
    i.add_argument("--redo", action="store_true")
    t = sub.add_parser("interpret")
    t.add_argument("--drawing")
    t.add_argument("--workers", type=int, default=4)
    t.add_argument("--all", action="store_true", help="also drawings that are already interpreted")
    g = sub.add_parser("prune-spaces", help="take enclosed spaces without a label that are not rooms out of the views")
    g.add_argument("--drawing")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--restore", action="store_true", help="put them back")
    u = sub.add_parser("dedupe-detections", help="count a door, window or fixture detected twice on a page image once")
    u.add_argument("--drawing")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--restore", action="store_true", help="put them back")
    rl = sub.add_parser("relabel-rooms", help="re-type labelled rooms after the room vocabulary changed")
    rl.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.cmd == "init":
        apply_schema()
        print("schema applied")
    elif a.cmd == "status":
        print(json.dumps(status(), indent=1, default=str))
    elif a.cmd == "ingest":
        apply_schema()
        out = pipeline.ingest(Path(a.root), Path(a.base) if a.base else None,
                              set(a.only.split(",")) if a.only else None, a.limit, a.workers,
                              not a.no_interpret, a.redo)
        print(json.dumps(out, indent=1))
    elif a.cmd == "prune-spaces":
        from . import space_guard
        with DB() as db:
            if a.restore:
                print(json.dumps(dict(restored=space_guard.restore(db, a.drawing))))
                return
            keys = [a.drawing] if a.drawing else [r["k"] for r in db.run(
                """select distinct drawing_key as k from jstore.entity where kind = 'room' and status = 'active'
                   and attrs->>'by' = 'jstore.interpret.raster_plan' order by 1""")]
            out = {k: n for k in keys if (n := len(space_guard.prune(db, k, a.dry_run)))}
        print(json.dumps(dict(drawings=len(out), spaces=sum(out.values()), dry_run=a.dry_run, by_drawing=out), indent=1))
    elif a.cmd == "relabel-rooms":
        from .interpret import vocab
        with DB() as db:
            rows = db.run("""select entity_id, label, label_norm from jstore.entity
                             where kind = 'room' and status = 'active' and label is not null""")
            change = [(r["entity_id"], t) for r in rows
                      if (t := vocab.room_type(r["label"])) is not None and t != r["label_norm"]
                      and r["label_norm"] in (None, "room", "laundry", "storage")]   # only fills in or sharpens a type
            if not a.dry_run:
                for eid, t in change:
                    db.run("update jstore.entity set label_norm = :t where entity_id = :e", t=t, e=eid)
        by: dict = {}
        for _, t in change:
            by[t] = by.get(t, 0) + 1
        print(json.dumps(dict(rooms=len(change), by_type=by, dry_run=a.dry_run), indent=1))
    elif a.cmd == "dedupe-detections":
        from . import detection_dupes
        with DB() as db:
            if a.restore:
                print(json.dumps(dict(restored=detection_dupes.restore(db, a.drawing))))
                return
            out, kinds = {}, {}
            for k in ([a.drawing] if a.drawing else detection_dupes.keys(db)):
                drop = detection_dupes.prune(db, k, a.dry_run)
                if drop:
                    out[k] = len(drop)
                    for r in db.run("select kind, count(*) as n from jstore.entity where entity_id = any(cast(:i as bigint[])) group by 1",
                                    i=list(drop)):
                        kinds[r["kind"]] = kinds.get(r["kind"], 0) + r["n"]
        print(json.dumps(dict(drawings=len(out), duplicates=sum(out.values()), by_kind=kinds, dry_run=a.dry_run,
                              by_drawing=out), indent=1))
    elif a.cmd == "interpret":
        with DB() as db:
            keys = [a.drawing] if a.drawing else [r["drawing_key"] for r in db.run(
                "select distinct drawing_key from jstore.source_file where status in ('parsed', 'interpreted')"
                + ("" if a.all else " except select drawing_key from jstore.source_file where status = 'interpreted'")
                + " order by 1")]
            # drawings with CAD, PDF or model data first; scans on their own are slow and add the least
            rich = {r["drawing_key"] for r in db.run(
                "select distinct drawing_key from jstore.source_file where format not in ('png', 'jpg')")}
            keys.sort(key=lambda k: (k not in rich, k))
        print(json.dumps(pipeline.interpret_all(keys, a.workers), indent=1))

if __name__ == "__main__":
    main()
