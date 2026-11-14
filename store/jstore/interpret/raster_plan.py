"""A vector plan for drawings that exist only as an image: walls, room polygons, doors and windows on the wall line.

    interpret(db, f)        called by jstore.interpret.run for every PNG / JPG after raster_rooms and symbols
    plan(db, drawing_key)   everything needed to draw the plan (page pixels and metres), see below

What it adds to what raster_rooms (rooms) and symbols (doors, windows, fixtures) wrote for the file:

  1. scale    A sheet without a trustworthy scale still gets a frame, so that every room has a polygon geometry:
              a NOMINAL scale estimated from the sizes of things drawn on it (estimate_scale): the boxes of the
              detected doors (a door symbol with its swing is about 0.965 m long), toilets (0.68 m), washbasins
              (0.59 m) and bathtubs (1.55 m) each vote once and the median vote is taken; without any of them the
              median room is taken as 3.15 m x 3.15 m; without rooms the long side of the image as 30 m.
              Measured against 93 scans of the live store whose scale is known (scale bar or printed areas):
              median error 5.1 % with doors and fixtures (p80 13 %), 6.7 % with doors alone (p80 19 %), 4.4 % with
              fixtures alone (p80 10 %), 19 % with the room-size prior (p80 42 %).  The frame has method
              'nominal_scale'; rooms get attrs.scale = 'nominal' and claims net_area_m2 / perimeter_m with method
              'nominal_outline' (low confidence, ranked below every printed or measured value by reconcile):
              approximate values, never to be presented as measured.  The definition row (concept 'units', method
              'nominal_scale') says which evidence was used and the uncertainty.
  2. walls    Two ways, both computed, one kept per drawing:
              pixel_walls  the thick dark ink near the rooms (opening with a disc as wide as the thinnest wall),
                           blobs that are short or lie inside a room dropped, contours as straight-edged polygons.
                           Exact where walls are drawn filled; finds nothing on walls drawn as two thin lines.
              room_gaps    walls derived from the room polygons: the band between neighbouring rooms and a band
                           around the indoor rooms as thick as the usual gap between two rooms.
              pixel_walls is kept when it lies under at least COVERAGE_MIN of the room outlines (outline points
              within COVER_TOL_M of wall ink), else room_gaps.  Both coverages are in the definition row
              (concept 'wall', method = the one kept).  Walls are entities of kind 'wall' (polygon, metres, same
              frame as the rooms), each sourced to a raster_detection row of class 'wall' (polygon in page pixels).
  3. openings Every detected door and window gets a rectangle on the wall line (attrs.opening_px, entity geom):
              along the long side of an elongated box, else along the side of the box that has most wall under
              it.  Door openings are cut out of the walls; windows are not (a window sits in its wall).

Coordinates: "page pixels" are the pixels of the page image (jstore.page.image, x right, y down) - the coordinates
of raster_detection.  Metres = frame.m applied to page pixels (y up).

plan(db, drawing_key) ->
    {"drawing", "file_id", "page_no", "image", "image_w", "image_h",
     "frame": {"m": [a, b, c, d, e, f], "method", "metres_per_pixel", "nominal": bool, "source", "uncertainty"},
     "wall_method": "pixel_walls" | "room_gaps" | None,
     "walls" | "rooms" | "doors" | "windows" | "fixtures":
         [{"entity_id", "kind", "number", "label", "attrs", "px": [ring, ...], "m": [ring, ...]}]}
ring = [[x, y], ...] closed; the first ring of a polygon is its outline, the others are holes; a MultiPolygon is
given as one item per part.  None when the drawing has no image file with rooms.
"""
from __future__ import annotations

import math
import os
import time

import cv2
import numpy as np

from ..db import DB, js
from . import define, merge

BY = "jstore.interpret.raster_plan"
VERSION = "rp-1"
RASTER = ("png", "jpg")
NOMINAL = "nominal_scale"
NOMINAL_CLAIM = "nominal_outline"
FIXTURES = ("toilet", "washbasin", "sink", "bathtub", "shower")
# long side of the detected box in metres (median over 93 scans with a known scale); doors: boxes that are nearly square
REF_M = {"door": 0.965, "toilet": 0.68, "washbasin": 0.59, "bathtub": 1.55}
ROOM_SIDE_M = 3.15          # square root of the median room area
IMAGE_SIDE_M = 30.0
UNCERTAINTY = {"doors and fixtures": 0.13, "fixtures": 0.10, "doors": 0.19, "room size": 0.45, "image size": 1.0}
WALL_MIN_M = 0.06           # thinnest wall drawn filled
BLOB_MIN_M = 0.55           # a piece of thick ink shorter than this is text or a symbol
ZONE_M = 1.0                # walls are looked for this far around the rooms
COVER_TOL_M = 0.22          # an outline point is on a wall when wall ink is this near
COVERAGE_MIN = 0.6            # filled walls measured 0.76-0.87, walls drawn as two lines 0.41-0.52
GAP_MAX_M = 0.6             # two rooms closer than this share a wall
MAX_WORK_PX = 3000
DARK = 200


# ------------------------------------------------------------------------------------------------ nominal scale
def estimate_scale(boxes: list, room_px: list, W: int, H: int) -> dict:
    """Metres per pixel from the sizes of things on the sheet.  boxes = [(class, x0, y0, x1, y1)] (kept detections),
    room_px = areas of the room outlines in pixels.  -> {mpp, source, uncertainty, votes: {class: {mpp, n}}}"""
    votes: dict = {}
    for cls, ref in REF_M.items():
        b = np.array([[min(x[3] - x[1], x[4] - x[2]), max(x[3] - x[1], x[4] - x[2])] for x in boxes if x[0] == cls
                      and x[3] > x[1] and x[4] > x[2]], float).reshape(-1, 2)
        if cls == "door":
            sq = b[b[:, 0] / b[:, 1] > 0.6] if len(b) else b      # a leaf with its swing; sliding doors are longer
            b = sq if len(sq) >= 2 else b
        if len(b):
            votes[cls] = dict(mpp=ref / float(np.median(b[:, 1])), n=int(len(b)), box_m=ref)
    if votes:
        mpp = float(np.exp(np.median([math.log(v["mpp"]) for v in votes.values()])))
        fx = [c for c in votes if c != "door"]
        source = "doors and fixtures" if fx and "door" in votes else "fixtures" if fx else "doors"
    elif len(room_px) >= 1:
        mpp, source = ROOM_SIDE_M / math.sqrt(float(np.median(room_px))), "room size"
    else:
        mpp, source = IMAGE_SIDE_M / max(W, H), "image size"
    return dict(mpp=mpp, source=source, uncertainty=UNCERTAINTY[source],
                votes={c: dict(v, mpp=round(v["mpp"], 6)) for c, v in votes.items()})


# ------------------------------------------------------------------------------------------------------- walls
def _open_disc(mask: np.ndarray, r: float) -> np.ndarray:
    """Morphological opening with a Euclidean disc of radius r (sub-pixel radii allowed)."""
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    core = (dt >= r).astype(np.uint8)
    if not core.any():
        return np.zeros_like(mask)
    back = cv2.distanceTransform(1 - core, cv2.DIST_L2, 5)
    return ((back < r) & (mask > 0)).astype(np.uint8)


def _fill(shape_hw, geoms, s: float = 1.0) -> np.ndarray:
    """Raster mask of shapely polygons given in pixels, drawn at scale s."""
    out = np.zeros(shape_hw, np.uint8)
    for g in geoms:
        for p in getattr(g, "geoms", [g]):
            if p.is_empty or p.geom_type != "Polygon":
                continue
            cv2.fillPoly(out, [np.round(np.array(p.exterior.coords) * s).astype(np.int32)], 1)
            for h in p.interiors:
                cv2.fillPoly(out, [np.round(np.array(h.coords) * s).astype(np.int32)], 0)
    return out


def _parts(g) -> list:
    return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]


def pixel_walls(bgr: np.ndarray, rooms: list, mpp: float) -> tuple[list, np.ndarray, float]:
    """Walls drawn as thick dark ink.  bgr = page image, rooms = shapely polygons in its pixels.
    -> (polygons in page pixels, wall mask at working scale, working scale)"""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    H, W = bgr.shape[:2]
    s = min(1.0, MAX_WORK_PX / max(W, H))
    img = bgr if s == 1.0 else cv2.resize(bgr, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA)
    k = mpp / s                                             # metres per working pixel
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    b = img.astype(np.int16)
    sat = (b.max(axis=2) - b.min(axis=2)) > 60
    dark = g < DARK
    grey = (dark & ~sat).astype(np.uint8)
    r = max(1.6, 0.5 * WALL_MIN_M / k + 0.6)
    w = _open_disc(grey, r)
    if w.sum() < 0.002 * w.size and sat.sum() > 3 * grey.sum():      # a plan drawn in colour
        w = _open_disc((dark | sat).astype(np.uint8), r)
    union = unary_union(rooms)
    w &= _fill(w.shape, [union.buffer(ZONE_M / mpp)], s)
    inside = _fill(w.shape, [p.buffer(-max(2.0 / s, 0.12 / mpp)) for p in rooms], s)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(w, connectivity=8)
    if n > 1:
        area = stats[:, cv2.CC_STAT_AREA].astype(float)
        within = np.bincount(lab[inside > 0], minlength=n) / np.maximum(area, 1)
        long_side = np.maximum(stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT])
        keep = (long_side >= BLOB_MIN_M / k) & (within <= 0.5)
        keep[0] = False
        w = keep[lab].astype(np.uint8)
    polys = []
    cs, hier = cv2.findContours(w, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    eps = max(1.0, 0.03 / k)
    for i, c in enumerate(cs):
        if hier[0][i][3] != -1 or len(c) < 3:
            continue
        ext = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(ext) < 3:
            continue
        holes, j = [], hier[0][i][2]
        while j != -1:
            h = cv2.approxPolyDP(cs[j], eps, True).reshape(-1, 2)
            if len(h) >= 3 and cv2.contourArea(cs[j]) * k * k > 0.02:
                holes.append((h + 0.5) / s)
            j = hier[0][j][0]
        p = Polygon((ext + 0.5) / s, holes).buffer(0)
        polys += [q for q in _parts(p) if q.area * mpp * mpp >= 0.03]
    return polys, w, s


def gap_width(rooms: list, mpp: float) -> float:
    """The usual distance between two neighbouring rooms in pixels = thickness of the walls between them."""
    from shapely import STRtree
    from shapely.geometry import Point
    if len(rooms) < 2:
        return 0.15 / mpp
    tree = STRtree(rooms)
    gaps = []
    step = 0.3 / mpp
    for i, p in enumerate(rooms):
        ring = p.exterior
        for d in np.arange(0, ring.length, step):
            pt = ring.interpolate(float(d))
            near = [j for j in tree.query(pt.buffer(GAP_MAX_M / mpp)) if j != i]
            if near:
                dist = min(rooms[j].distance(pt) for j in near)
                if 0 < dist <= GAP_MAX_M / mpp:
                    gaps.append(dist)
    if len(gaps) < 5:
        return 0.15 / mpp
    return float(np.clip(np.median(gaps), 0.08 / mpp, 0.45 / mpp))


def room_gap_walls(rooms: list, indoor: list, mpp: float) -> tuple[list, float]:
    """Walls derived from the room polygons: what lies between neighbouring rooms, and a band around the indoor
    rooms.  -> (polygons in pixels, wall thickness in pixels)"""
    from shapely.ops import unary_union
    t = gap_width(rooms, mpp)
    core = [p for p, inn in zip(rooms, indoor) if inn] or rooms
    c = max(t, 0.35 / mpp) * 0.55                             # gaps up to about 2 c are filled
    closed = unary_union([p.buffer(c, join_style=2, mitre_limit=2.0) for p in core]).buffer(-c, join_style=2, mitre_limit=2.0)
    outer = closed.buffer(float(np.clip(1.4 * t, 0.2 / mpp, 0.45 / mpp)), join_style=2, mitre_limit=2.0)
    walls = outer.difference(unary_union([p.buffer(0.01 / mpp) for p in rooms]))
    return [p for p in _parts(walls.buffer(0)) if p.area * mpp * mpp >= 0.03], t


def coverage(rooms: list, mask: np.ndarray, s: float, mpp: float) -> float:
    """Share of the room outlines that has wall (mask, at scale s) within COVER_TOL_M."""
    if not mask.any():
        return 0.0
    dt = cv2.distanceTransform(1 - mask, cv2.DIST_L2, 5)
    tol = COVER_TOL_M / mpp * s
    hit = n = 0
    for p in rooms:
        ring = p.exterior
        for d in np.arange(0, ring.length, max(1.0, 0.15 / mpp)):
            q = ring.interpolate(float(d))
            x, y = int(round(q.x * s)), int(round(q.y * s))
            if 0 <= y < dt.shape[0] and 0 <= x < dt.shape[1]:
                n += 1
                hit += dt[y, x] <= tol
    return hit / n if n else 0.0


def opening(b, cls: str, ref, t: float):
    """The rectangle a door or window takes on the wall line.  b = its box in pixels, ref = wall geometry to look
    for the wall in (shapely), t = wall thickness in pixels.  -> (shapely box, 'h' | 'v')"""
    from shapely.geometry import box
    x0, y0, x1, y1 = b
    w, h = x1 - x0, y1 - y0
    if max(w, h) >= 1.6 * min(w, h):                         # elongated: lies along the wall, on its centre line
        if w >= h:
            cy = (y0 + y1) / 2
            return box(x0, cy - t / 2, x1, cy + t / 2), "h"
        cx = (x0 + x1) / 2
        return box(cx - t / 2, y0, cx + t / 2, y1), "v"
    trim = 0.06 * max(w, h) if cls == "door" else 0.0         # jambs
    cands = []
    for side, strip in (("top", box(x0, y0 - t, x1, y0 + t)), ("bottom", box(x0, y1 - t, x1, y1 + t)),
                        ("left", box(x0 - t, y0, x0 + t, y1)), ("right", box(x1 - t, y0, x1 + t, y1))):
        hit = ref.intersection(strip) if ref is not None else None
        cands.append((hit.area if hit is not None else 0.0, side, hit))
    a, side, hit = max(cands, key=lambda c: c[0])
    if a <= 0:                                               # no wall near the box: the longer axis through the middle
        side, hit = ("top" if w >= h else "left"), None
        c = (y0 + y1) / 2 if w >= h else (x0 + x1) / 2
    else:
        c = hit.centroid.y if side in ("top", "bottom") else hit.centroid.x
    if side in ("top", "bottom"):
        return box(x0 + trim, c - t / 2, x1 - trim, c + t / 2), "h"
    return box(c - t / 2, y0 + trim, c + t / 2, y1 - trim), "v"


def build(bgr: np.ndarray, rooms: list, indoor: list, symbols: list, mpp: float) -> dict:
    """The vector plan of one page image, in page pixels.  rooms = shapely polygons, indoor = [bool], symbols =
    [(class, x0, y0, x1, y1)].  -> {"method", "walls": [Polygon], "thickness_px", "coverage": {method: share},
    "candidates": {method: [Polygon]}, "openings": [(Polygon, 'h'|'v') | None per symbol]}"""
    from shapely.ops import unary_union
    px, mask, s = pixel_walls(bgr, rooms, mpp)
    gap, t = room_gap_walls(rooms, indoor, mpp)
    cov = dict(pixel_walls=round(coverage(rooms, mask, s, mpp), 3),
               room_gaps=round(coverage(rooms, _fill(mask.shape, gap, s), s, mpp), 3))
    method = "pixel_walls" if cov["pixel_walls"] >= COVERAGE_MIN else "room_gaps"
    ref = unary_union(gap + px) if gap or px else None
    opens = [opening(b[1:], b[0], ref, t) if b[0] in ("door", "window") else None for b in symbols]
    walls = px if method == "pixel_walls" else gap
    doors = [o[0] for o, b in zip(opens, symbols) if o and b[0] == "door"]
    if doors and walls:
        cut = unary_union(walls).difference(unary_union(doors))
        walls = [p for p in _parts(cut) if p.area * mpp * mpp >= 0.02]
    return dict(method=method, walls=walls, thickness_px=t, coverage=cov, candidates=dict(pixel_walls=px, room_gaps=gap),
                openings=opens)


# ------------------------------------------------------------------------------------------------------ Layer 2
def _apply(m, g):
    from shapely.affinity import affine_transform
    return affine_transform(g, [m[0], m[1], m[3], m[4], m[2], m[5]])


def _invert(m):
    a, b, c, d, e, f = m
    det = a * e - b * d
    return [e / det, -b / det, (b * f - c * e) / det, -d / det, a / det, (c * d - a * f) / det]


def _room_rows(db: DB, f: dict) -> list[dict]:
    """Rooms read from this file with the outline raster_rooms kept in page pixels."""
    from shapely import from_wkb
    rows = db.run("""select e.entity_id, e.attrs, e.geom is not null as placed, s.id as source_id,
                            encode(st_asbinary(d.geom), 'hex') as wkb
                     from jstore.entity e
                     join jstore.entity_source s on s.entity_id = e.entity_id and s.file_id = :f and s.page_no = 0
                          and s.role = 'outline'
                     join jstore.raster_detection d on d.class = 'room_outline' and d.file_id = :f and d.id =
                          case when s.ref_table = 'raster_detection' then cast(s.ref_key as bigint)
                               else cast(e.attrs->>'outline_detection' as bigint) end
                     where e.drawing_key = :d and e.kind = 'room' and e.status = 'active' order by e.entity_id""",
                  f=f["file_id"], d=f["drawing_key"])
    out = []
    for r in rows:
        p = from_wkb(r["wkb"]).buffer(0)
        p = max(_parts(p), key=lambda q: q.area, default=None)
        if p is not None and p.area > 4:
            out.append(dict(r, poly=p, indoor=(r["attrs"] or {}).get("indoor", True) is not False))
    return out


def _clean(db: DB, f: dict) -> None:
    db.run("""delete from jstore.entity where drawing_key = :d and kind = 'wall' and attrs->>'by' = :by
              and entity_id in (select entity_id from jstore.entity_source where file_id = :f)""",
           d=f["drawing_key"], by=BY, f=f["file_id"])
    db.run("delete from jstore.raster_detection where file_id = :f and detector like 'raster_plan %'", f=f["file_id"])
    db.run("delete from jstore.claim where file_id = :f and method = :m", f=f["file_id"], m=NOMINAL_CLAIM)
    db.run("delete from jstore.definition where file_id = :f and created_by = :by", f=f["file_id"], by=BY)


def drop_nominal_frame(db: DB, file_id: str) -> None:
    """Called by raster_rooms before it looks for a scale: an estimate of an earlier run is not a stated scale."""
    db.run("delete from jstore.frame where file_id = :f and method = :m", f=file_id, m=NOMINAL)


def interpret(db: DB, f: dict) -> dict:
    """Frame (nominal when nothing better), walls and openings for one raster file.  -> counts"""
    from shapely.geometry import box
    from ..pipeline import store_dir
    t0 = time.time()
    _clean(db, f)
    if os.environ.get("JSTORE_RASTER_PLAN", "1") == "0":
        return dict(walls=0, skipped="JSTORE_RASTER_PLAN is off")
    rooms = _room_rows(db, f)
    if not rooms:
        return dict(walls=0, skipped="no room outlines were read from this file")
    page = db.one("select image, image_w, image_h from jstore.page where file_id = :f and page_no = 0", f=f["file_id"])
    path = next((p for p in ([store_dir() / page["image"]] if page and page.get("image") else []) +
                 [os.path.join(os.environ.get("JSTORE_DATASET_DIR", ""), f["path"])] if os.path.exists(p)), None)
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR) if path else None
    if bgr is None:
        return dict(walls=0, skipped="page image not found")
    H, W = bgr.shape[:2]
    syms = db.run("""select d.id, d.class, d.x0, d.y0, d.x1, d.y1, s.entity_id
                     from jstore.raster_detection d
                     join jstore.entity_source s on s.ref_table = 'raster_detection' and s.ref_key = cast(d.id as text)
                          and s.file_id = d.file_id and s.role = 'symbol'
                     join jstore.entity e on e.entity_id = s.entity_id and e.status = 'active'
                     where d.file_id = :f and d.page_no = 0 order by d.id""", f=f["file_id"])
    boxes = [(r["class"], r["x0"], r["y0"], r["x1"], r["y1"]) for r in syms]

    fr = db.one("select m, method from jstore.frame where file_id = :f and page_no = 0", f=f["file_id"])
    nominal = None
    if not fr or fr["method"] == NOMINAL:                 # (an estimate of an earlier run is made again)
        nominal = estimate_scale(boxes, [r["poly"].area for r in rooms], W, H)
        mpp = nominal["mpp"]
        m = [mpp, 0.0, 0.0, 0.0, -mpp, mpp * H]
        db.run("""insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, cast(:m as double precision[]), :me)
                  on conflict (file_id, page_no) do update set m = excluded.m, method = excluded.method""",
               f=f["file_id"], m="{" + ",".join(repr(float(v)) for v in m) + "}", me=NOMINAL)
        define(db, f["file_id"], "units", NOMINAL, dict(
            metres_per_pixel=round(mpp, 6), estimated_from=nominal["source"], votes=nominal["votes"],
            uncertainty=nominal["uncertainty"], version=VERSION,
            rule="the sheet states no usable scale; a pixel is sized by the things drawn on it: the median long side "
                 "of the detected door, toilet, washbasin and bathtub boxes against their usual size (one vote per "
                 "class, median vote), else a median room of 3.15 m x 3.15 m, else the image as 30 m long.  "
                 "Geometry, areas and lengths under this frame are approximate (uncertainty = the 80th percentile "
                 "of the error on scans with a known scale); areas scale with its square"), 0.2, BY)
    else:
        m = list(fr["m"])
        mpp = math.sqrt(abs(m[0] * m[4] - m[1] * m[3]))
    is_nominal = nominal is not None

    # rooms: geometry and approximate measures where raster_rooms could not place them
    placed = 0
    for r in rooms:
        if r["placed"] and nominal is None:
            continue
        p = r["poly"]
        conf = round(max(0.05, 0.3 * (1 - nominal["uncertainty"])), 2)
        attrs = dict(scale="nominal", scale_source=nominal["source"], scale_uncertainty=nominal["uncertainty"],
                     geom="outline of the room placed with an estimated scale (frame method nominal_scale): approximate")
        db.run("""update jstore.entity set geom = st_geomfromtext(:w), attrs = attrs || cast(:a as jsonb)
                  where entity_id = :e""", w=_apply(m, p).wkt, a=js(attrs), e=r["entity_id"])
        for prop, val, unit in (("net_area_m2", round(p.area * mpp * mpp, 2), "m2"), ("perimeter_m", round(p.length * mpp, 2), "m")):
            db.run("""insert into jstore.claim (entity_id, property, value_num, unit, file_id, method, source_id, confidence)
                      values (:e, :p, :v, :u, :f, :m, :s, :c)""",
                   e=r["entity_id"], p=prop, v=val, u=unit, f=f["file_id"], m=NOMINAL_CLAIM, s=r["source_id"], c=conf)
        placed += 1

    plan_ = build(bgr, [r["poly"] for r in rooms], [r["indoor"] for r in rooms], boxes, mpp)
    t = plan_["thickness_px"]

    # doors and windows on the wall line; fixtures keep (or get) their box
    for r, o in zip(syms, plan_["openings"]):
        b = box(r["x0"], r["y0"], r["x1"], r["y1"])
        if o is None:
            db.run("update jstore.entity set geom = st_geomfromtext(:w) where entity_id = :e and geom is null",
                   w=_apply(m, b).wkt, e=r["entity_id"])
            continue
        g, axis = o
        x0, y0, x1, y1 = g.bounds
        attrs = dict(opening_px=[round(v, 1) for v in g.bounds], wall_axis=axis,
                     opening_width_m=round(max(x1 - x0, y1 - y0) * mpp, 2), wall_thickness_m=round(t * mpp, 2),
                     geom="rectangle on the wall line (jstore.interpret.raster_plan); the detected box is attrs.box_px")
        if is_nominal:
            attrs["scale"] = "nominal"
        db.run("""update jstore.entity set geom = st_geomfromtext(:w), attrs = attrs || cast(:a as jsonb)
                  where entity_id = :e""", w=_apply(m, g).wkt, a=js(attrs), e=r["entity_id"])

    spec = dict(version=VERSION, method=plan_["method"], coverage_of_room_outlines=plan_["coverage"],
                chosen_by=f"pixel_walls when it lies under at least {COVERAGE_MIN} of the room outlines, else room_gaps",
                pieces={k: len(v) for k, v in plan_["candidates"].items()}, wall_thickness_m=round(t * mpp, 3),
                metres_per_pixel=round(mpp, 6), scale="nominal" if is_nominal else "stated",
                door_openings_cut=sum(1 for b in boxes if b[0] == "door"),
                rule=dict(pixel_walls="thick dark ink near the rooms, vectorised to straight-edged polygons",
                          room_gaps="the band between neighbouring room polygons and around the indoor rooms, as "
                                    "thick as the usual gap between two rooms")[plan_["method"]],
                seconds=round(time.time() - t0, 1))
    conf = 0.2 if is_nominal else 0.6 if plan_["method"] == "pixel_walls" else 0.4
    d_wall = define(db, f["file_id"], "wall", plan_["method"], spec, conf, BY)
    recs = []
    detector = f"raster_plan {VERSION} {plan_['method']}"
    for p in plan_["walls"]:
        b = p.bounds
        did = db.one("""insert into jstore.raster_detection (file_id, page_no, class, x0, y0, x1, y1, detector, attrs, geom)
                        values (:f, 0, 'wall', cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8),
                                cast(:y1 as float8), :det, cast(:a as jsonb), st_geomfromtext(:w)) returning id""",
                     f=f["file_id"], x0=b[0], y0=b[1], x1=b[2], y1=b[3], det=detector,
                     a=js(dict(by=BY, method=plan_["method"])), w=p.wkt)["id"]
        attrs = dict(by=BY, method=plan_["method"], thickness_m=round(t * mpp, 3), scale="nominal" if is_nominal else "stated")
        recs.append(dict(kind="wall", ifc_class="IfcWall", number=None, label=None, geom=_apply(m, p), attrs=attrs,
                         sources=[("raster_detection", str(did), "outline", [round(v, 1) for v in b], d_wall)], claims=[]))
    out = merge(db, f, recs, registered=False) if recs else dict(new=0)
    return dict(walls=out["new"], method=plan_["method"], coverage=plan_["coverage"], rooms_placed=placed,
                nominal_scale=round(mpp, 6) if nominal else None, scale_from=nominal["source"] if nominal else None,
                seconds=round(time.time() - t0, 1))


# ------------------------------------------------------------------------------------------------ for consumers
def _rings(g) -> list:
    return [[[[round(x, 3), round(y, 3)] for x, y in ring.coords] for ring in [p.exterior, *p.interiors]] for p in _parts(g)]


def plan(db: DB, drawing_key: str) -> dict | None:
    """The vector plan of an image drawing: see the module text.  Reads only."""
    from shapely import from_wkb
    f = db.one("""select f.file_id, p.page_no, p.image, p.image_w, p.image_h, fr.m, fr.method
                  from jstore.source_file f
                  join jstore.page p on p.file_id = f.file_id and p.page_no = 0
                  join jstore.frame fr on fr.file_id = f.file_id and fr.page_no = 0
                  where f.drawing_key = :d and f.format = any(cast(:r as text[]))
                    and exists (select from jstore.entity_source s join jstore.entity e using (entity_id)
                                where s.file_id = f.file_id and e.kind = 'room' and e.status = 'active' and e.geom is not null)
                  order by f.path limit 1""", d=drawing_key, r=list(RASTER))
    if not f:
        return None
    m = list(f["m"])
    inv = _invert(m)
    units = db.one("""select method, spec from jstore.definition where file_id = :f and concept = 'units'
                      order by (method = :n) desc, def_id desc limit 1""", f=f["file_id"], n=NOMINAL) or {}
    nominal = f["method"] == NOMINAL
    spec = units.get("spec") or {}
    out = dict(drawing=drawing_key, file_id=f["file_id"], page_no=0, image=f["image"], image_w=f["image_w"],
               image_h=f["image_h"], wall_method=None,
               frame=dict(m=m, method=f["method"], metres_per_pixel=math.sqrt(abs(m[0] * m[4] - m[1] * m[3])),
                          nominal=nominal, source=spec.get("estimated_from") if nominal else spec.get("how"),
                          uncertainty=spec.get("uncertainty") if nominal else None),
               walls=[], rooms=[], doors=[], windows=[], fixtures=[])
    rows = db.run("""select e.entity_id, e.kind, e.number, e.label, e.attrs, encode(st_asbinary(e.geom), 'hex') as wkb
                     from jstore.entity e
                     where e.drawing_key = :d and e.status = 'active' and e.geom is not null
                       and exists (select from jstore.entity_source s where s.entity_id = e.entity_id and s.file_id = :f)
                     order by e.entity_id""", d=drawing_key, f=f["file_id"])
    for r in rows:
        g = from_wkb(r["wkb"])
        key = {"wall": "walls", "room": "rooms", "door": "doors", "window": "windows"}.get(r["kind"]) or \
            ("fixtures" if r["kind"] in FIXTURES else None)
        if key is None:
            continue
        if key == "walls":
            out["wall_method"] = (r["attrs"] or {}).get("method")
        for part_m, part_px in zip(_rings(g), _rings(_apply(inv, g))):
            out[key].append(dict(entity_id=r["entity_id"], kind=r["kind"], number=r["number"], label=r["label"],
                                 attrs=r["attrs"], px=part_px, m=part_m))
    return out
