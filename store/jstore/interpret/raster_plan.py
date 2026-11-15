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
VERSION = "rp-2"
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
# Usual floor area in m2 of rooms by type (median of 6,000 rooms with a printed or CAD area) and how far the
# 10th-90th percentile lies from it as a factor on the SIDE of the room (square root of the area ratio).
ROOM_AREA_M2 = {"bedroom": (12.5, 1.15), "room": (15.4, 1.21), "living_room": (20.0, 1.35), "living_dining": (26.8, 1.27),
                "kitchen": (7.9, 1.37), "bathroom": (3.9, 1.4), "toilet": (2.5, 1.26), "elevator": (2.8, 1.25),
                "sauna": (3.5, 1.45), "laundry": (7.3, 1.36), "dining_room": (9.6, 1.7)}
# Smallest plausible area by type: an estimate that makes the median room of a type smaller than this is doubted.
ROOM_MIN_M2 = {"bedroom": 7.0, "room": 7.0, "living_room": 12.0, "living_dining": 14.0, "kitchen": 3.5}
# Spread (sigma of the log error) of each estimator, measured on scans with a known scale; weight = 1 / sigma^2.
SIGMA = {"printed_areas": 0.04, "dimensions": 0.05, "door": 0.22, "toilet": 0.12, "washbasin": 0.15, "bathtub": 0.11,
         "room_types": 0.25}
OUTLIER = 1.3               # an estimator this factor away from the weighted median is dropped


def _wmedian(logs: list, weights: list) -> float:
    o = np.argsort(logs)
    lg, w = np.array(logs, float)[o], np.array(weights, float)[o]
    c = np.cumsum(w)
    i = int(np.searchsorted(c, 0.5 * c[-1]))              # the value at which half of the weight is reached
    if i + 1 < len(lg) and abs(c[i] - 0.5 * c[-1]) < 1e-9:
        return float(0.5 * (lg[i] + lg[i + 1]))
    return float(lg[i])


def estimate_scale(boxes: list, room_px: list, W: int, H: int, dims: list | None = None) -> dict:
    """Metres per pixel from everything on the sheet that has a known or usual size.
    boxes = [(class, x0, y0, x1, y1)] (kept detections); room_px = one item per room outline: its area in pixels, or
    {"px": area in pixels, "type": room type or None, "printed_m2": area printed in the room or None};
    dims = [(length of a dimension line in pixels, printed value in metres)].
    Estimators (each one value of metres per pixel):
      printed_areas  rooms with a printed area: sqrt(printed m2 / outline pixels), median over the rooms
      dimensions     printed value over the length of its dimension line, median over the lines
      door, toilet, washbasin, bathtub   median long side of the detected boxes against the usual size (REF_M)
      room_types     the outline of each typed room against the usual area of such a room (ROOM_AREA_M2), median
    They are combined by the weighted median of their logarithms (weight 1 / sigma^2, SIGMA); estimators more than
    OUTLIER away from it are dropped and the median is taken again.  Without any estimator the median room is taken
    as 3.15 m x 3.15 m, without rooms the long side of the image as 30 m.
    -> {mpp, source, uncertainty, votes: {estimator: {mpp, n, weight, used}}, agreement, checks}"""
    rooms = [r if isinstance(r, dict) else dict(px=float(r)) for r in room_px]
    rooms = [r for r in rooms if r.get("px") and r["px"] > 0]
    votes: dict = {}
    pa = [math.sqrt(r["printed_m2"] / r["px"]) for r in rooms if r.get("printed_m2") and 0.5 <= r["printed_m2"] <= 2000]
    if pa:
        votes["printed_areas"] = dict(mpp=float(np.median(pa)), n=len(pa),
                                      sigma=SIGMA["printed_areas"] * (2.0 if len(pa) == 1 else 1.0))
    dv = [v / d for d, v in (dims or []) if d and d > 3 and v and v > 0]
    if len(dv) >= 2:
        med = float(np.median(dv))
        near = [x for x in dv if abs(math.log(x / med)) < 0.1]
        if len(near) >= max(2, 0.5 * len(dv)):            # lines that agree; a sheet of stray numbers does not vote
            votes["dimensions"] = dict(mpp=float(np.median(near)), n=len(near), sigma=SIGMA["dimensions"])
    for cls, ref in REF_M.items():
        b = np.array([[min(x[3] - x[1], x[4] - x[2]), max(x[3] - x[1], x[4] - x[2])] for x in boxes if x[0] == cls
                      and x[3] > x[1] and x[4] > x[2]], float).reshape(-1, 2)
        if cls == "door":
            sq = b[b[:, 0] / b[:, 1] > 0.6] if len(b) else b      # a leaf with its swing; sliding doors are longer
            b = sq if len(sq) >= 2 else b
        if len(b):
            votes[cls] = dict(mpp=ref / float(np.median(b[:, 1])), n=int(len(b)), box_m=ref,
                              sigma=SIGMA[cls] * (1.5 if len(b) == 1 else 1.0))
    rt = [math.sqrt(ROOM_AREA_M2[r["type"]][0] / r["px"]) for r in rooms if r.get("type") in ROOM_AREA_M2]
    if rt:
        spread = float(np.median([math.log(ROOM_AREA_M2[r["type"]][1]) for r in rooms if r.get("type") in ROOM_AREA_M2]))
        votes["room_types"] = dict(mpp=float(np.median(rt)), n=len(rt),
                                   sigma=max(SIGMA["room_types"], spread / math.sqrt(len(rt)) * 1.3))
    checks: dict = {}
    if votes:
        names = list(votes)
        logs = [math.log(votes[c]["mpp"]) for c in names]
        wts = [1.0 / votes[c]["sigma"] ** 2 for c in names]
        mid = _wmedian(logs, wts)
        keep = [abs(lg - mid) <= math.log(OUTLIER) for lg in logs]
        if any(keep) and not all(keep):
            mid = _wmedian([lg for lg, k in zip(logs, keep) if k], [w for w, k in zip(wts, keep) if k])
        mpp = math.exp(mid)
        used = [c for c, k in zip(names, keep) if k]
        for c, k, w in zip(names, keep, wts):
            votes[c].update(used=bool(k), weight=round(w, 1))
        # how well the estimators agree: weighted mean distance from the chosen value (all of them, dropped too)
        agreement = float(np.average([min(abs(lg - mid), 0.7) for lg in logs], weights=wts)) if len(names) > 1 else None
        own = 1.0 / math.sqrt(sum(w for w, k in zip(wts, keep) if k))
        unc = 1.3 * own + (agreement or 0.0)
        if len(names) == 1:
            unc = 1.5 * votes[names[0]]["sigma"]
        # sanity: typical rooms must not come out implausibly small
        for t, least in ROOM_MIN_M2.items():
            a = [r["px"] * mpp * mpp for r in rooms if r.get("type") == t]
            if a and float(np.median(a)) < least:
                checks[t] = dict(median_m2=round(float(np.median(a)), 1), least_plausible_m2=least)
        if checks:
            unc = max(unc, 0.35)
        fx = [c for c in used if c in ("toilet", "washbasin", "bathtub")]
        if "printed_areas" in used or "dimensions" in used:
            source = " and ".join(x for x in ("printed areas" if "printed_areas" in used else None,
                                              "printed dimensions" if "dimensions" in used else None) if x)
        elif fx or "door" in used:
            source = "doors and fixtures" if fx and "door" in used else "fixtures" if fx else "doors"
            if "room_types" in used:
                source += " and room sizes"
        else:
            source = "room sizes by type"
        unc = round(float(np.clip(unc, 0.04, 0.6)), 2)
    elif rooms:
        mpp, source = ROOM_SIDE_M / math.sqrt(float(np.median([r["px"] for r in rooms]))), "room size"
        unc, agreement = UNCERTAINTY[source], None
    else:
        mpp, source, unc, agreement = IMAGE_SIDE_M / max(W, H), "image size", UNCERTAINTY["image size"], None
    return dict(mpp=mpp, source=source, uncertainty=unc, checks=checks,
                agreement=None if agreement is None else round(agreement, 3),
                votes={c: dict({k: v[k] for k in v if k != "sigma"}, mpp=round(v["mpp"], 6)) for c, v in votes.items()})


# ------------------------------------------------------------------------------------- spaces without a label
SPACE_OPEN_M = 0.3          # radius of the disc that must fit: passages narrower than twice this close a space
SPACE_FILL_M = 0.9          # a space is flooded this far past thin lines (door swings, fixtures) up to the filled walls
SPACE_MIN_M2 = 1.2          # smallest enclosed space kept inside the building
SPACE_OUT_MIN_M2 = 1.5      # ... and beside it (balcony, terrace)
SPACE_OUT_MAX_M2 = 30.0
SPACE_REACH_M = 0.6         # a space belongs to the building when a room or wall is this near
WET = ("toilet", "bathtub", "shower")


def _space_type(kinds: list, area_m2: float, indoor: bool) -> tuple[str, str]:
    """Room type of a space without a label, and why.  kinds = classes of the fixtures detected inside."""
    if not indoor:
        return "balcony", "enclosed by thin lines on the outside of the building"
    ks = set(kinds)
    if ks & {"bathtub", "shower"}:
        return "bathroom", "a " + " and a ".join(sorted(ks & {"bathtub", "shower", "toilet"})) + " inside"
    if "toilet" in ks:
        return ("toilet", "a toilet inside") if area_m2 < 2.5 else ("bathroom", "a toilet inside")
    if "washbasin" in ks:
        return "bathroom", "a washbasin inside"
    return "room", "no fixture inside"


def unlabelled_spaces(bgr: np.ndarray, rooms: list, mpp: float, symbols: list) -> list[dict]:
    """Enclosed spaces of the plan that no room outline covers: rooms whose label was not read or never printed
    (bathroom cores, storage) and open-air spaces drawn beside the building (balconies).
    The free space of the sheet (no ink, no room outline) is opened with a disc of SPACE_OPEN_M, which closes door
    openings and narrow gaps; its connected pieces are candidates.  Kept: pieces that do not reach the border of the
    image (the sheet background), are not the open continuation of a labelled room (connected to its inside without
    a drawn line between), have a room or wall within SPACE_REACH_M, a plausible size, and are either held by filled
    walls and rooms along most of their outline (indoor) or have a compact rectangular shape (balcony, terrace).
    rooms = shapely polygons in page pixels, symbols = [(class, x0, y0, x1, y1)].
    -> [{"poly": Polygon (page pixels), "indoor": bool, "type", "why", "fixtures": [class], "area_m2"}]"""
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    from .raster_rooms import _ink
    if not rooms:
        return []
    H, W = bgr.shape[:2]
    s = min(1.0, MAX_WORK_PX / max(W, H), 0.04 / mpp)         # no finer than 4 cm per working pixel
    img = bgr if s == 1.0 else cv2.resize(bgr, (max(1, round(W * s)), max(1, round(H * s))), interpolation=cv2.INTER_AREA)
    k = mpp / s
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (_ink(g) | (g < 110)).astype(np.uint8)
    h, w = ink.shape
    thick = _open_disc(ink, max(1.6, 0.5 * WALL_MIN_M / k + 0.6))      # walls drawn filled
    for b in symbols:                                         # a fixture is furniture of the space, not its boundary
        if b[0] in FIXTURES:
            x0, y0, x1, y1 = (int(round(v * s)) for v in b[1:])
            ink[max(0, y0):y1 + 1, max(0, x0):x1 + 1] &= thick[max(0, y0):y1 + 1, max(0, x0):x1 + 1]
    r = SPACE_OPEN_M / k
    union = unary_union(rooms)
    roomm = _fill((h, w), [p.buffer(0.08 / mpp) for p in rooms], s)
    free0 = np.pad(1 - ink, 1, constant_values=1)             # (the padding joins everything that reaches the border)
    free = np.pad(((ink == 0) & (roomm == 0)).astype(np.uint8), 1, constant_values=1)
    core = (cv2.distanceTransform(free, cv2.DIST_L2, 5) >= r).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    if n <= 1:
        return []
    # pieces of the paper around and between things: the ones connected to the image border
    core0 = (cv2.distanceTransform(free0, cv2.DIST_L2, 5) >= r).astype(np.uint8)
    n0, lab0 = cv2.connectedComponents(core0, connectivity=8)
    inside = np.pad(_fill((h, w), [p.buffer(-(r + 2) * 1.0 / s) for p in rooms], s), 1)
    of_room = np.zeros(n0, bool)                              # pieces of the unmasked free space that hold a room
    of_room[np.unique(lab0[(inside > 0) & (lab0 > 0)])] = True
    near = union.buffer(SPACE_REACH_M / mpp)
    largest = max(p.area for p in rooms) * mpp * mpp
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(math.ceil(r)) + 3,) * 2)
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    # within 0.25 m of a filled wall or a room: what holds an indoor space
    hard = cv2.dilate(((thick > 0) | (roomm > 0)).astype(np.uint8), cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * int(math.ceil(0.25 / k)) + 1,) * 2))
    solid = np.pad(((thick == 0) & (roomm == 0)).astype(np.uint8), 1, constant_values=1)
    out = []
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        if x == 0 or y == 0 or x + bw >= w + 2 or y + bh >= h + 2:
            continue                                          # reaches the border: sheet background
        if a * k * k < 0.25:
            continue
        piece = (lab[y:y + bh, x:x + bw] == i)
        ids = np.unique(lab0[y:y + bh, x:x + bw][piece])
        if of_room[ids[ids > 0]].any() or lab0[0, 0] in ids:
            continue                                          # open to a labelled room, or to the background
        pad = int(math.ceil(r + SPACE_FILL_M / k)) + 2
        y0, y1, x0, x1 = max(0, y - pad), min(h + 2, y + bh + pad), max(0, x - pad), min(w + 2, x + bw + pad)
        grown = cv2.dilate((lab[y0:y1, x0:x1] == i).astype(np.uint8), disc) & free[y0:y1, x0:x1]
        # thin lines inside the space (door swings, furniture, a shower) are part of it: flood on from the piece
        # over everything that is neither a filled wall nor a room, SPACE_FILL_M far, and drop what is thin
        blob = grown.copy()
        sol = solid[y0:y1, x0:x1]
        for _ in range(int(math.ceil(SPACE_FILL_M / k))):
            grown = cv2.dilate(grown, cross) & sol
        fat = _open_disc(grown, max(1.5, 0.2 / k))
        nn, ll = cv2.connectedComponents(fat, connectivity=4)
        if nn > 2:
            seed = np.bincount(ll[lab[y0:y1, x0:x1] == i], minlength=nn)
            seed[0] = 0
            fat = (ll == int(np.argmax(seed))).astype(np.uint8)
        grown = fat
        def polygon(mask):
            cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cs:
                return None
            ring = cv2.approxPolyDP(max(cs, key=cv2.contourArea), max(1.0, 0.04 / k), True).reshape(-1, 2)
            if len(ring) < 3:
                return None
            q = Polygon((ring + [x0 - 1 + 0.5, y0 - 1 + 0.5]) / s).buffer(0)
            return max(_parts(q.difference(union)), key=lambda t: t.area, default=None)

        poly = polygon(blob)                                  # bounded by every drawn line
        if poly is None or not poly.intersects(near):
            continue
        def held(q) -> float:                                # share of the outline that runs along a filled wall or a room
            pts = [q.exterior.interpolate(float(d)) for d in np.arange(0, q.exterior.length, max(1.0, 0.1 / mpp))]
            return sum(1 for t in pts if hard[min(h - 1, max(0, int(t.y * s))), min(w - 1, max(0, int(t.x * s)))]) / max(1, len(pts))

        full = polygon(grown)                                 # bounded by filled walls and rooms
        if full is not None and full.area <= 3.0 * poly.area and held(full) >= 0.6:
            poly = full
        indoor = held(poly) >= 0.6
        if indoor:
            area = poly.area * mpp * mpp
            if not SPACE_MIN_M2 <= area <= max(40.0, 1.5 * largest):
                continue
        else:
            area = poly.area * mpp * mpp
            rect = poly.minimum_rotated_rectangle
            ex = np.array(rect.exterior.coords)
            a_, b_ = math.dist(ex[0], ex[1]) * mpp, math.dist(ex[1], ex[2]) * mpp
            touch = poly.buffer(SPACE_REACH_M / mpp).intersection(union).area * mpp * mpp
            if not (SPACE_OUT_MIN_M2 <= area <= SPACE_OUT_MAX_M2 and poly.area >= 0.75 * rect.area
                    and min(a_, b_) >= 0.9 and max(a_, b_) <= 8 * min(a_, b_) and touch >= 0.3):
                continue
        inside_k = [b[0] for b in symbols if b[0] in FIXTURES and poly.buffer(0.15 / mpp).contains(box(*b[1:]).centroid)]
        t, why = _space_type(inside_k, area, indoor)
        out.append(dict(poly=poly, indoor=indoor, type=t, why=why, fixtures=inside_k, area_m2=round(area, 2)))
    return out


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


def _across(b, ref, t: float):
    """An elongated door box that the wall crosses instead of following: the opening on that wall, or None.  Taken
    only on clear evidence: little wall along the box's long centre line, wall most of the way across it."""
    from shapely.geometry import box
    x0, y0, x1, y1 = b
    w, h = x1 - x0, y1 - y0
    if ref is None or min(w, h) <= 0 or t <= 0:
        return None
    hit = ref.intersection(box(x0, y0, x1, y1))
    if hit.is_empty or hit.area <= 0:
        return None
    if w >= h:
        along, c = box(x0, (y0 + y1) / 2 - t / 2, x1, (y0 + y1) / 2 + t / 2), hit.centroid.x
        cross, axis = box(c - t / 2, y0, c + t / 2, y1), "v"
    else:
        along, c = box((x0 + x1) / 2 - t / 2, y0, (x0 + x1) / 2 + t / 2, y1), hit.centroid.y
        cross, axis = box(x0, c - t / 2, x1, c + t / 2), "h"
    f_along, f_cross = ref.intersection(along).area / along.area, ref.intersection(cross).area / cross.area
    return (cross, axis) if f_along < 0.3 and f_cross >= 0.5 else None


def opening(b, cls: str, ref, t: float):
    """The rectangle a door or window takes on the wall line.  b = its box in pixels, ref = wall geometry to look
    for the wall in (shapely), t = wall thickness in pixels.  -> (shapely box, 'h' | 'v')"""
    from shapely.geometry import box
    x0, y0, x1, y1 = b
    w, h = x1 - x0, y1 - y0
    if max(w, h) >= 1.6 * min(w, h):                         # elongated: lies along the wall, on its centre line
        across = _across(b, ref, t) if cls == "door" else None
        if across is not None:                               # ... unless the wall crosses it: leaves drawn open to
            return across                                    # both sides make the box long across the wall
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
    rows = db.run("""select e.entity_id, e.attrs, e.geom is not null as placed, s.id as source_id, e.label_norm,
                            (select max(c.value_num) from jstore.claim c where c.entity_id = e.entity_id and c.file_id = :f
                              and c.property = 'net_area_m2' and c.method in ('printed', 'ocr')) as printed_m2,
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
    db.run("""delete from jstore.entity where drawing_key = :d and kind = 'room' and attrs->>'by' = :by
              and entity_id in (select entity_id from jstore.entity_source where file_id = :f)""",
           d=f["drawing_key"], by=BY, f=f["file_id"])        # spaces without a label of an earlier run
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
        dims = []
        for d in db.run("""select attrs from jstore.entity where drawing_key = :d and kind = 'dimension' and status = 'active'
                           and attrs->>'dim_kind' in ('linear', 'overall') and coalesce(attrs->>'page_no', '0') = '0'
                           and entity_id in (select entity_id from jstore.entity_source where file_id = :f)""",
                        d=f["drawing_key"], f=f["file_id"]):
            ln = ((d["attrs"] or {}).get("px") or {}).get("line")
            if ln and d["attrs"].get("value_m"):
                dims.append((math.dist(ln[0], ln[1]), float(d["attrs"]["value_m"])))
        nominal = estimate_scale(boxes, [dict(px=r["poly"].area, type=r["label_norm"], printed_m2=r["printed_m2"])
                                         for r in rooms], W, H, dims)
        mpp = nominal["mpp"]
        m = [mpp, 0.0, 0.0, 0.0, -mpp, mpp * H]
        db.run("""insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, cast(:m as double precision[]), :me)
                  on conflict (file_id, page_no) do update set m = excluded.m, method = excluded.method""",
               f=f["file_id"], m="{" + ",".join(repr(float(v)) for v in m) + "}", me=NOMINAL)
        define(db, f["file_id"], "units", NOMINAL, dict(
            metres_per_pixel=round(mpp, 6), estimated_from=nominal["source"], votes=nominal["votes"],
            uncertainty=nominal["uncertainty"], agreement=nominal["agreement"], implausible=nominal["checks"],
            version=VERSION,
            rule="the sheet states no usable scale; a pixel is sized by the things drawn on it, one estimator each "
                 "(votes): rooms with a printed area against their outline, printed dimensions against their lines, "
                 "the median long side of the detected door, toilet, washbasin and bathtub boxes against their usual "
                 "size, the outlines of typed rooms against the usual area of such a room.  Chosen: the weighted "
                 "median of their logarithms after dropping estimators more than 1.3 x away (used = false); else a "
                 "median room of 3.15 m x 3.15 m, else the image as 30 m long.  Geometry, areas and lengths under "
                 "this frame are approximate; the uncertainty grows when the estimators disagree (agreement) or a "
                 "usual room comes out implausibly small (implausible); areas scale with its square"),
            round(max(0.05, 0.3 * (1 - nominal["uncertainty"])), 2), BY)
    else:
        m = list(fr["m"])
        mpp = math.sqrt(abs(m[0] * m[4] - m[1] * m[3]))
    is_nominal = nominal is not None

    # spaces without a label: rooms of their own (kind room, label null, attrs.unlabelled)
    labelled = len(rooms)
    spaces = unlabelled_spaces(bgr, [r["poly"] for r in rooms], mpp, boxes) \
        if os.environ.get("JSTORE_UNLABELLED_SPACES", "1") != "0" else []
    if spaces:
        d_space = define(db, f["file_id"], "room", "unlabelled_space", dict(
            version=VERSION, spaces=len(spaces), types=sorted({x["type"] for x in spaces}),
            rule="an enclosed piece of the plan that no labelled room covers: free space (no ink, no room outline) "
                 f"opened with a disc of {SPACE_OPEN_M} m, not connected to the sheet background or to the inside of "
                 "a labelled room, next to the rooms, of a plausible size; flooded up to the filled walls.  The type "
                 "is taken from the fixtures detected inside (bath, shower, toilet, washbasin), else 'room'; a compact "
                 "space enclosed by thin lines rather than walls is taken as a balcony.  Its area is an estimate"), 0.3, BY)
        recs = []
        for x in spaces:
            p = x["poly"]
            did = db.one("""insert into jstore.raster_detection (file_id, page_no, class, x0, y0, x1, y1, detector, attrs, geom)
                            values (:f, 0, 'room_outline', cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8),
                                    cast(:y1 as float8), :det, cast(:a as jsonb), st_geomfromtext(:w)) returning id""",
                         f=f["file_id"], x0=p.bounds[0], y0=p.bounds[1], x1=p.bounds[2], y1=p.bounds[3],
                         det=f"raster_plan {VERSION} unlabelled_space", a=js(dict(by=BY, method="unlabelled_space")),
                         w=p.wkt)["id"]
            attrs = dict(by=BY, unlabelled=True, indoor=x["indoor"], outline="free_space", outline_px=round(p.area),
                         type_from=x["why"], fixtures_inside=x["fixtures"], area_estimate=True, outline_flags=["no_label"],
                         geom="outline of an enclosed space without a label (jstore.interpret.raster_plan): approximate")
            claims = [] if is_nominal else [
                ("net_area_m2", round(p.area * mpp * mpp, 2), None, "m2", "measured_outline", 0, 0.3),
                ("perimeter_m", round(p.length * mpp, 2), None, "m", "measured_outline", 0, 0.3)]
            recs.append(dict(kind="room", ifc_class="IfcSpace", number=None, label=None, label_norm=x["type"], zone=None,
                             geom=None if is_nominal else _apply(m, p), attrs=attrs, always_new=True, claims=claims,
                             sources=[("raster_detection", str(did), "outline", [round(v, 1) for v in p.bounds], d_space)]))
        merge(db, f, recs, registered=False)
        rooms = _room_rows(db, f)

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
    pruned = {}
    if os.environ.get("JSTORE_SPACE_GUARD", "1") != "0":
        try:                                             # spaces that walls do not bound are not rooms (space_guard)
            from .. import space_guard
            pruned = space_guard.prune(db, f["drawing_key"])
        except Exception:  # noqa: BLE001 - the guard never fails an interpretation
            pruned = {}
    gone = sum(1 for why in pruned.values() if not why.endswith("not a balcony"))
    return dict(walls=out["new"], method=plan_["method"], coverage=plan_["coverage"], rooms_placed=placed,
                unlabelled_spaces=len(rooms) - labelled - gone, spaces_not_rooms=gone,
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
