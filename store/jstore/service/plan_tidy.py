"""Finish of an image plan's vector drawing, applied when it is drawn (nothing here changes the store).

  pole            where a room's label goes: the point of the room farthest from its edge
  place_labels    labels sized to their rooms, shortened when they do not fit, never over each other
  tidy_outline    a ragged room outline (free space traced around fixtures) straightened
  clean_walls     wall fill without loose fragments and without the openings of placed doors and windows
  same_opening    two door symbols that draw the same opening
  duplicate_boxes boxes that cover the same thing (two detections of one window)

Everything is in one isotropic unit (the drawing's units, or pixels); `u` is metres per unit (0: unknown).
"""
from __future__ import annotations

import math

import shapely
from shapely.geometry import LineString, Polygon

CHAR_W = 0.56            # width of a character in font sizes (Helvetica, average)
LINE_H = 1.2             # line pitch in font sizes
CLOSE_M = 0.45           # notches up to twice this wide are filled (a fixture carved out of the room)
STUB_AREA_M2 = 0.3       # a loose wall piece under this area ...
STUB_THIN_M = 0.12       # ... that is this thin,
STUB_LONG_M = 1.0        # ... or shorter than this and slimmer than a column, is a fragment
COLUMN_M = 0.18
NEAR_ROOM_M = 0.6        # wall fill farther than this from every room is not part of the building


def _polys(g) -> list:
    return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]


def _valid(g):
    return g if g.is_valid else g.buffer(0)


def pole(poly) -> tuple[float, float, float]:
    """(x, y, radius) of the largest circle inside the polygon: the pole of inaccessibility."""
    poly = _valid(poly)
    if poly.geom_type != "Polygon":
        parts = _polys(poly)
        if not parts:
            c = poly.centroid
            return c.x, c.y, 0.0
        poly = max(parts, key=lambda p: p.area)
    try:
        x0, y0, x1, y1 = poly.bounds
        ray = shapely.maximum_inscribed_circle(poly, tolerance=max(x1 - x0, y1 - y0, 1e-6) / 200)
        (cx, cy), (bx, by) = ray.coords[0], ray.coords[-1]
        r = math.hypot(bx - cx, by - cy)
        if math.isfinite(cx) and math.isfinite(cy) and r > 0:
            return cx, cy, r
    except Exception:  # noqa: BLE001 - a degenerate polygon: any point inside will do
        pass
    c = poly.representative_point()
    return c.x, c.y, 0.0


def place_labels(rooms: list[dict], smin: float, smax: float, pad: float = 1.5) -> list[dict | None]:
    """A label for each room, or None when nothing fits.  rooms: [{poly, lines}] with `lines` in the order they are
    drawn and the least important last but one ... : the last line (the area) is dropped first, then all but the
    first.  Larger rooms choose first; a label lies inside its room (grown by `pad`) and clear of every label placed
    before it.  -> [{x, y, size, lines}] in the order of `rooms`; (x, y) is the centre of the label."""
    out: list[dict | None] = [None] * len(rooms)
    placed: list = []
    order = sorted(range(len(rooms)), key=lambda i: -rooms[i]["poly"].area)
    for i in order:
        poly, lines = _valid(rooms[i]["poly"]), [t for t in rooms[i]["lines"] if t]
        if poly.geom_type != "Polygon":                      # an outline that crosses itself: its largest piece
            parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon" and not g.is_empty]
            poly = max(parts, key=lambda g: g.area) if parts else shapely.Polygon()
        if not lines or poly.is_empty:
            continue
        px_, py_, r = pole(poly)
        spots = [(px_, py_)]
        c = poly.centroid                                    # a long room has no single widest place: its middle is one
        if r > 0 and poly.contains(c) and poly.exterior.distance(c) >= 0.8 * r:
            spots.insert(0, (c.x, c.y))
        room = poly.buffer(pad)
        variants = [lines] + ([lines[:-1]] if len(lines) > 1 else []) + ([lines[:1]] if len(lines) > 2 else [])
        done = False
        for var in variants:
            longest = max(len(t) for t in var)
            fit = min(smax, 2 * (r + pad) / LINE_H / len(var) * 0.98)     # no taller than the room at its pole
            size = max(smin, fit)
            while not done:
                w, h = CHAR_W * longest * size + 0.6 * size, LINE_H * size * len(var)
                for cx, cy in spots:
                    box = shapely.box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                    if room.contains(box) and not any(box.intersects(o) for o in placed):
                        placed.append(box.buffer(0.15 * size))
                        out[i] = dict(x=cx, y=cy, size=size, lines=var)
                        done = True
                        break
                if done or size <= smin:
                    break
                size = max(smin, size * 0.88)
            if done:
                break
    return out


def _snap_orthogonal(poly, tol: float, angle_deg: float = 12.0):
    """Edges that are nearly horizontal or vertical made exactly so (each vertex takes x from its vertical edge and
    y from its horizontal one); the polygon is returned unchanged when that would break it or move its area."""
    pts = list(poly.exterior.coords[:-1])
    n = len(pts)
    if n < 4:
        return poly
    k = math.tan(math.radians(angle_deg))
    xs, ys = [[] for _ in range(n)], [[] for _ in range(n)]
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
        dx, dy = abs(bx - ax), abs(by - ay)
        if dy <= k * dx and dy <= tol * 2:                   # horizontal: one y for both ends
            y = (ay + by) / 2
            ys[i].append(y), ys[(i + 1) % n].append(y)
        elif dx <= k * dy and dx <= tol * 2:
            x = (ax + bx) / 2
            xs[i].append(x), xs[(i + 1) % n].append(x)
    new = [((sum(xs[i]) / len(xs[i])) if xs[i] else pts[i][0], (sum(ys[i]) / len(ys[i])) if ys[i] else pts[i][1])
           for i in range(n)]
    cand = Polygon(new, [list(r.coords) for r in poly.interiors])
    if not cand.is_valid or cand.is_empty or abs(cand.area - poly.area) > 0.05 * poly.area:
        return poly
    return cand


def tidy_outline(poly, u: float, walls=None, others=None):
    """A ragged outline straightened: notches up to 2 x CLOSE_M wide are filled, never into wall fill or another
    room, thin spikes are removed, the corners are squared.  An outline whose scale is unknown, or that would lose
    much of itself, comes back as it is (the same object)."""
    if u <= 0 or poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return poly
    d = CLOSE_M / u
    src = _valid(poly)
    closed = src.buffer(d, join_style="mitre", mitre_limit=2.0).buffer(-d, join_style="mitre", mitre_limit=2.0)
    if closed.is_empty:
        return poly
    closed = closed.union(shapely.box(*src.bounds))         # the cell it stands in: its extent, up to the walls around
    for block in (walls, others):
        if block is not None and not block.is_empty:
            closed = closed.difference(block)
    closed = closed.union(src) if others is None or others.is_empty else closed.union(src.difference(others))
    e = 0.08 / u                                             # slivers along walls and spikes go
    opened = closed.buffer(-e, join_style="mitre").buffer(e, join_style="mitre")
    parts = _polys(opened)
    if not parts:
        return poly
    best = max(parts, key=lambda p: p.intersection(src).area)
    best = Polygon(best.exterior, [h for h in best.interiors if Polygon(h).area * u * u > 0.5])
    tol = 0.06 / u
    simple = best.simplify(tol)
    simple = _snap_orthogonal(simple, 0.15 / u) if simple.is_valid and simple.geom_type == "Polygon" else best
    simple = _valid(simple)
    if simple.geom_type != "Polygon" or simple.is_empty or not 0.8 * src.area <= simple.area <= 2.5 * src.area:
        return poly
    return simple


def clean_walls(walls: list, u: float, rooms=None, openings=None) -> list:
    """Wall fill as it is drawn: the openings of placed doors and windows cut out, loose fragments (thin slivers and
    stubs that are too small to be a wall or a column) and fill away from every room dropped.  -> polygons."""
    if not walls:
        return []
    solid = shapely.union_all([_valid(g) for g in walls])
    if openings is not None and not openings.is_empty:
        solid = solid.difference(openings)
    whole = _polys(shapely.union_all([_valid(g) for g in walls]))
    keep = []
    near = rooms.buffer(NEAR_ROOM_M / u) if rooms is not None and not rooms.is_empty and u > 0 else None
    loose = []                                               # the components of the fill before cutting
    for c in whole:
        if u > 0 and _fragment(c, u):
            loose.append(c)
            continue
        if near is not None and not c.intersects(near):
            loose.append(c)
    if loose:
        solid = solid.difference(shapely.union_all(loose).buffer(1e-6))
    for p in _polys(solid):                                  # (a sliver left over by an opening is a fragment too)
        if p.area > 0 and not (u > 0 and _fragment(p, u)):
            keep.append(p)
    return keep


def _fragment(c, u: float) -> bool:
    """A piece of wall fill too small to be a wall or a column."""
    a, per = c.area * u * u, c.length * u
    if a >= STUB_AREA_M2:
        return False
    thick = 2 * a / per if per else 0.0
    rect = c.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return True
    xs, ys = rect.exterior.coords.xy
    long = max(math.hypot(xs[1] - xs[0], ys[1] - ys[0]), math.hypot(xs[2] - xs[1], ys[2] - ys[1])) * u
    return thick < STUB_THIN_M or (long < STUB_LONG_M and thick < COLUMN_M)


def same_opening(a: dict, b: dict, u: float) -> bool:
    """Two door symbols on the same opening: parallel, on one wall line, and one covers most of the other."""
    la, lb = LineString([a["a"], a["b"]]), LineString([b["a"], b["b"]])
    if la.length <= 0 or lb.length <= 0:
        return False
    da = ((a["b"][0] - a["a"][0]) / la.length, (a["b"][1] - a["a"][1]) / la.length)
    db = ((b["b"][0] - b["a"][0]) / lb.length, (b["b"][1] - b["a"][1]) / lb.length)
    if abs(da[0] * db[0] + da[1] * db[1]) < 0.8:
        return False
    reach = (0.35 / u) if u > 0 else 0.35 * min(la.length, lb.length)
    short, long = (la, lb) if la.length <= lb.length else (lb, la)
    return short.intersection(long.buffer(reach, cap_style="flat")).length >= 0.5 * short.length


def door_rank(sym: dict) -> tuple:
    """Higher is the better-supported symbol of an opening: on a wall, with a leaf, not oversized, then the shorter."""
    length = math.hypot(sym["b"][0] - sym["a"][0], sym["b"][1] - sym["a"][1])
    return (bool(sym.get("anchored")), sym["mode"] != "marker", not sym.get("oversized"), -length)


def duplicate_boxes(boxes: list[tuple[float, float, float, float]], share: float = 0.5) -> set[int]:
    """Indexes of boxes (x, y, w, h) that mostly lie over a larger box kept before them."""
    order = sorted(range(len(boxes)), key=lambda i: -(boxes[i][2] * boxes[i][3]))
    kept, drop = [], set()
    for i in order:
        x, y, w, h = boxes[i]
        dup = False
        for j in kept:
            ox, oy, ow, oh = boxes[j]
            ix = min(x + w, ox + ow) - max(x, ox)
            iy = min(y + h, oy + oh) - max(y, oy)
            if ix > 0 and iy > 0 and ix * iy >= share * max(w * h, 1e-9):
                dup = True
                break
        (drop.add(i) if dup else kept.append(i))
    return drop
