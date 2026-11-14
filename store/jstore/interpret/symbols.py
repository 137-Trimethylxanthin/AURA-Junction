"""Doors, windows and sanitary fixtures of a plan that exists only as PDF or image, detected on the page image.

    interpret(db, f) -> {"new": n, "matched": n, "unlinked": n, ...}      f = source_file row of a PDF / PNG / JPG

Called by interpret.run() for every PDF and raster file, after the room readers.  It adds nothing when
  - the drawing has a CAD or IFC file (those give doors, windows and fixtures exactly),
  - another file of the drawing already gave them (the PDF and the PNG of one sheet: the first one read wins),
  - the page is not a floor plan (one cheap classification request per page), or
  - there is no GEMINI_API_KEY, or JSTORE_SYMBOLS=0.

Layer 1: every candidate box of every page goes to jstore.raster_detection (pixels of the page image), kept or
not, with the views that saw it, the verifier's probabilities and the reason - so the result can be inspected.
Layer 2: one entity per kept door / window / toilet / washbasin / sink / bathtub / shower, pointing at its
detection row (entity_source.ref_table = 'raster_detection', bbox in the file's own coordinates: pixels for an
image, points for a PDF).  Stairs are detected and stored in Layer 1 only.  With a frame (a trusted scale) the
entity has a geometry in metres (and a window a width claim); with rooms in the store it gets its host room (`host` = the room number;
attrs.room_id / room_label name the room entity also when rooms carry no number).

The detector itself (triage, layout, tiling, Carbon per tile, merging, verification, cache) is
jstore.gemini_detect; this module is its database side.

Settings: JSTORE_SYMBOLS (default on when a key is set), JSTORE_SYMBOLS_BUDGET_S (110), JSTORE_SYMBOLS_MAX_PAGES (8),
JSTORE_SYMBOLS_MIN_SCORE (0.0), and those of jstore.gemini_detect.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import signal
import time

from ..db import DB, js
from . import define, issue, merge

BY = "jstore.interpret.symbols"
METHOD = "gemini_tiled_detection"
SOLID = ("dxf", "dwg", "ifc")                       # formats that give symbols exactly
IFC_CLASS = {"door": "IfcDoor", "window": "IfcWindow"}
ENTITY_KINDS = ("door", "window", "toilet", "washbasin", "sink", "bathtub", "shower")
LAYER1_ONLY = ("stair",)


def _off() -> bool:
    return os.environ.get("JSTORE_SYMBOLS", "1").strip().lower() in ("0", "off", "false", "no")


def _budget() -> float:
    """Seconds this file may take: the setting, and never past the pipeline's alarm for the drawing."""
    b = float(os.environ.get("JSTORE_SYMBOLS_BUDGET_S", "110"))
    try:
        left = signal.getitimer(signal.ITIMER_REAL)[0]
    except (AttributeError, ValueError, OSError):
        left = 0
    if left > 0:
        b = min(b, left - 20)
    return b


def _invert(m):
    """Inverse of an affine [a, b, c, d, e, f]."""
    a, b, c, d, e, f = m
    det = a * e - b * d
    if abs(det) < 1e-12:
        return None
    return [e / det, -b / det, (b * f - c * e) / det, -d / det, a / det, (c * d - a * f) / det]


def _box(m, b):
    """Bounding box of box b after the affine m."""
    xs = [m[0] * x + m[1] * y + m[2] for x, y in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]))]
    ys = [m[3] * x + m[4] * y + m[5] for x, y in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]))]
    return [min(xs), min(ys), max(xs), max(ys)]


def _rooms(db: DB, f: dict, page_no: int, framed: bool) -> list[tuple]:
    """Rooms a symbol can sit in: [(shape, number, apartment, entity_id, label)], shapes in metres when the page has a frame,
    else the boxes the rooms were read from on this page, in file coordinates."""
    from shapely import from_wkb
    from shapely.geometry import box
    if framed:
        rows = db.run("""select entity_id, number, label, zone, encode(st_asbinary(geom), 'hex') as wkb
                         from jstore.entity e
                         where drawing_key = :d and kind = 'room' and status = 'active' and geom is not null
                           and exists (select from jstore.entity_source s where s.entity_id = e.entity_id
                                       and s.file_id = :f and s.page_no = :p)""",
                      d=f["drawing_key"], f=f["file_id"], p=page_no)
        return [(from_wkb(r["wkb"]), r["number"], r["zone"], r["entity_id"], r["label"]) for r in rows]
    rows = db.run("""select e.entity_id, e.number, e.label, e.zone, s.bbox
                     from jstore.entity e join jstore.entity_source s using (entity_id)
                     where e.drawing_key = :d and e.kind = 'room' and e.status = 'active'
                       and s.file_id = :f and s.page_no = :p and s.role = 'outline' and s.bbox is not null""",
                  d=f["drawing_key"], f=f["file_id"], p=page_no)
    return [(box(*r["bbox"]), r["number"], r["zone"], r["entity_id"], r["label"]) for r in rows if len(r["bbox"]) == 4]


def _host(rooms: list[tuple], g, reach: float):
    """(room, rooms touched): the room that holds most of the symbol grown by `reach`, as a tuple of _rooms."""
    if not rooms:
        return None, []
    zone = g.buffer(reach)
    hit = sorted(((r[0].intersection(zone).area, r[0].area, i) for i, r in enumerate(rooms) if r[0].intersects(zone)),
                 key=lambda h: (-h[0], h[1]))
    hit = [h for h in hit if h[0] > 0]
    if not hit:
        return None, []
    return rooms[hit[0][2]], [rooms[i] for _, _, i in hit]


def _clean(db: DB, file_id: str) -> None:
    db.run("delete from jstore.raster_detection where file_id = :f and attrs->>'by' = :by", f=file_id, by=BY)


def _skip(db: DB, f: dict, reason: str) -> dict:
    define(db, f["file_id"], "symbols", METHOD, dict(created=0, reason=reason), 0.0, BY)
    return dict(new=0, matched=0, unlinked=0, skipped=reason)


def interpret(db: DB, f: dict) -> dict:
    """Doors, windows and fixtures of one PDF or raster file.  -> merge() counts"""
    from shapely.geometry import box
    from .. import gemini_detect as G
    from ..pipeline import store_dir
    t0 = time.time()
    _clean(db, f["file_id"])                                   # idempotent: nothing of an earlier run stays
    if _off():
        return dict(new=0, matched=0, unlinked=0, skipped="JSTORE_SYMBOLS is off")
    solid = db.one("""select count(*) as n from jstore.source_file
                      where drawing_key = :d and format = any(cast(:s as text[]))""", d=f["drawing_key"], s=list(SOLID))["n"]
    if solid:
        return dict(new=0, matched=0, unlinked=0, skipped="the drawing has a CAD or IFC file")
    have = db.one("""select count(*) as n from jstore.entity where drawing_key = :d and status = 'active'
                     and kind = any(cast(:k as text[]))""", d=f["drawing_key"], k=list(ENTITY_KINDS))["n"]
    if have:
        return _skip(db, f, f"another file of the drawing already gave {have} doors, windows or fixtures")
    if not os.environ.get("GEMINI_API_KEY"):
        return _skip(db, f, "GEMINI_API_KEY is not set")
    pages = db.run("""select page_no, image, image_w, image_h, to_image from jstore.page
                      where file_id = :f and image is not null order by page_no""", f=f["file_id"])
    pages = [p for p in pages if (store_dir() / p["image"]).exists()]
    if not pages:
        return _skip(db, f, "no page image to look at")
    max_pages = int(os.environ.get("JSTORE_SYMBOLS_MAX_PAGES", "8"))
    left_out = [p["page_no"] for p in pages[max_pages:]]
    pages = pages[:max_pages]
    budget = _budget()
    if budget < 20:
        return _skip(db, f, "no time left for this drawing")

    def one(p):
        try:
            return G.detect(store_dir() / p["image"], budget_s=budget - (time.time() - t0))
        except Exception as e:  # noqa: BLE001 - one bad page must not lose the others
            return dict(plan=None, candidates=[], objects=[], errors=[f"page {p['page_no']}: {e!r}"[:300]], calls=0,
                        cached=0, areas=[], views=[], triage=None, seconds=0, detector=G.MODEL)

    with cf.ThreadPoolExecutor(min(4, len(pages))) as ex:
        results = list(ex.map(one, pages))

    min_score = float(os.environ.get("JSTORE_SYMBOLS_MIN_SCORE", "0"))
    detector = f"{G.MODEL} + {G.DECISION_MODEL}, {G.PROMPT_VERSION}"
    recs, n_cand, errors, page_specs = [], 0, [], []
    counts: dict[str, int] = {}
    for p, res in zip(pages, results):
        errors += [f"page {p['page_no']}: {e}" for e in res.get("errors") or []]
        page_specs.append(dict(page=p["page_no"], floor_plan=res.get("plan"), triage=res.get("triage"),
                               areas=[dict(rect=a["rect"], tile_px=a.get("tile"), tiles=a.get("tiles"),
                                           door_px=a.get("door_px"), how=a.get("how")) for a in res.get("areas") or []],
                               views=len(res.get("views") or []), candidates=len(res["candidates"]),
                               kept=len(res["objects"]), requests=res.get("calls"), cached=res.get("cached"),
                               seconds=res.get("seconds")))
        cands = [c for c in res["candidates"] if c["box"][2] > c["box"][0] and c["box"][3] > c["box"][1]]
        if not cands:
            continue
        # the page image may have been scaled since the detector read it: boxes are in ITS pixels
        iw, ih = (res.get("size") or [p["image_w"], p["image_h"]])
        sx, sy = (p["image_w"] or iw) / iw, (p["image_h"] or ih) / ih
        ids = [r["id"] for r in db.run("select nextval('jstore.raster_detection_id_seq') as id from generate_series(1, :n)",
                                       n=len(cands))]
        rows = []
        for did, c in zip(ids, cands):
            b = [c["box"][0] * sx, c["box"][1] * sy, c["box"][2] * sx, c["box"][3] * sy]
            c["_id"], c["_px"] = did, b
            if c["kept"] and c["score"] < min_score:
                c["kept"], c["why"] = False, f"score below JSTORE_SYMBOLS_MIN_SCORE ({min_score})"
            attrs = dict(by=BY, kept=bool(c["kept"]), why=c.get("why"), views=c.get("views"), votes=c.get("votes"),
                         view=c.get("view"), verify=c.get("verify"))
            if c.get("relabelled_from"):
                attrs["relabelled_from"] = c["relabelled_from"]
            rows.append((did, f["file_id"], p["page_no"], c["label"], c.get("score"), *b, detector, js(attrs),
                         box(*b).wkb_hex))
        db.copy("jstore.raster_detection", ["id", "file_id", "page_no", "class", "score", "x0", "y0", "x1", "y1",
                                            "detector", "attrs", "geom"], rows)
        n_cand += len(rows)
        # Layer 2 records
        to_file = _invert(p["to_image"]) if p.get("to_image") and len(p["to_image"]) == 6 else [1, 0, 0, 0, 1, 0]
        frame = db.one("select m, method from jstore.frame where file_id = :f and page_no = :p",
                       f=f["file_id"], p=p["page_no"])
        m = list(frame["m"]) if frame else None
        rooms = _rooms(db, f, p["page_no"], m is not None)
        k = (abs(m[0] * m[4] - m[1] * m[3]) ** 0.5) if m else None      # metres per file unit
        for c in cands:
            if not c["kept"] or c["label"] not in ENTITY_KINDS:
                continue
            fb = _box(to_file, c["_px"]) if to_file else c["_px"]
            g = box(*_box(m, fb)) if m else None
            if rooms:
                shape = g if m else box(*fb)
                reach = 0.25 if m else 0.25 * max(fb[2] - fb[0], fb[3] - fb[1])
                room, touched = _host(rooms, shape, reach)
            else:
                room, touched = None, []
            claims = []
            # a window's box runs along the wall, so its long side is the width; a door's box holds leaf, arc and
            # jambs and is 30-50 % wider than the opening, so no width is claimed for doors
            if k and c["label"] == "window":
                width = max(fb[2] - fb[0], fb[3] - fb[1]) * k
                if 0.3 <= width <= 12:
                    claims.append(("width_m", round(width, 2), None, "m", "detected", 0, 0.4))
            attrs = dict(via="detected on the page image", score=c["score"], votes=c["votes"], page_no=p["page_no"],
                         box_px=[round(v, 1) for v in c["_px"]])
            if room:                                            # rooms without a number are named by id and label
                attrs.update(room_id=room[3], room_label=room[4], room_ids=[t[3] for t in touched],
                             rooms=sorted({t[1] for t in touched if t[1]}))
            recs.append(dict(kind=c["label"], ifc_class=IFC_CLASS.get(c["label"], "IfcSanitaryTerminal"), number=None,
                             host=room[1] if room else None, zone=room[2] if room else None, geom=g, attrs=attrs, page_no=p["page_no"],
                             sources=[("raster_detection", str(c["_id"]), "symbol", [round(v, 3) for v in fb], None)],
                             claims=claims, _conf=c["score"]))
            counts[c["label"]] = counts.get(c["label"], 0) + 1

    plan_pages = [s["page"] for s in page_specs if s["floor_plan"]]
    spec = dict(detector=detector, models=dict(detect=G.MODEL, verify=G.DECISION_MODEL, thinking=G.THINKING),
                prompt_version=G.PROMPT_VERSION,
                rule="plan areas from a layout request, overlapping tiles sized by the drawn door width, boxes per "
                     "class from every tile, merged across tiles, each candidate checked on its own crop",
                tiling=dict(view_px=G.VIEW_SIDE, door_px_in_view=G.DOOR_PX, overlap=G.OVERLAP, max_objects=G.MAX_OBJECTS),
                pages=page_specs, plan_pages=plan_pages, candidates=n_cand, created=len(recs), counts=counts,
                seconds=round(time.time() - t0, 1))
    if left_out:
        spec["pages_not_read"] = left_out
    if errors:
        spec["errors"] = errors[:20]
    if not plan_pages:
        spec["reason"] = "no page of the file is a floor plan" if all(s["floor_plan"] is False for s in page_specs) \
            else "the detector gave no answer"
    conf = round(sum(r["_conf"] for r in recs) / len(recs), 2) if recs else 0.0
    def_ids = {}
    for kind in sorted({r["kind"] for r in recs}) or ["symbols"]:
        n = counts.get(kind, 0)
        def_ids[kind] = define(db, f["file_id"], kind, METHOD, dict(spec, created=n) if kind != "symbols" else spec,
                               round(min(conf, 0.85), 2), BY)
    for r in recs:
        r.pop("_conf")
        r["sources"] = [(t, key, role, bb, def_ids[r["kind"]]) for t, key, role, bb, _ in r["sources"]]
    out = merge(db, f, recs, registered=False)
    if errors:
        issue(db, f, "detection_incomplete", f"symbol detection on {f['path']}: {len(errors)} request(s) failed or "
              f"ran out of time, so doors, windows or fixtures may be missing: " + "; ".join(errors[:5]))
    with_symbols = sorted({r["page_no"] for r in recs})
    if len(with_symbols) > 1:
        issue(db, f, "repeated_sheets", f"doors, windows and fixtures of {f['path']} were detected on pages "
              f"{with_symbols} separately; sheets that show the same storey (plan, ceiling plan, services) repeat "
              f"the same things, so count per page (attrs.page_no) rather than over the file")
    if left_out:
        issue(db, f, "detection_incomplete", f"symbol detection read the first {max_pages} pages of {f['path']} only; "
              f"pages {left_out[:20]} were not looked at")
    return dict(out, candidates=n_cand, **counts)
