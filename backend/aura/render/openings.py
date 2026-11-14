"""Cut wall footprints at their windows and doors, the way a plan is drawn.

IFC keeps openings as separate voids, so a storey's wall footprints run straight through every window and
door, and the window/door footprints are often only a thin frame on one face of the wall. For drawing (2D
plan and the 3D view built from it), each opening cuts a gap across the full wall thickness, and the window
or door is redrawn to fill that gap. Counts and evidence boxes do not use this; it is presentation only.
"""
from __future__ import annotations

import math

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

Ring = list[tuple[float, float]]

#: How far across the wall line a cut reaches (m); more than any wall is thick, less than a room is deep.
CUT_DEPTH = 1.2
#: Openings narrower than this along the wall are left alone (m).
MIN_WIDTH = 0.3
#: A cut that removes less wall than this is not an opening in a wall (m²): keep the footprint as it is.
MIN_GAP_AREA = 0.01


def _poly(ring) -> Polygon | None:
    if not ring or len(ring) < 3:
        return None
    p = Polygon(ring)
    if not p.is_valid:
        p = p.buffer(0)
    return None if p.is_empty else p


def _parts(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [x for x in getattr(g, 'geoms', []) if isinstance(x, Polygon)]


def _ring(p: Polygon) -> Ring:
    return [(round(x, 4), round(y, 4)) for x, y in list(p.exterior.coords)[:-1]]


def _cutter(op: Polygon) -> Polygon | None:
    """A rectangle along the opening's long axis, as wide as the opening and CUT_DEPTH across the wall."""
    rect = op.minimum_rotated_rectangle
    if not isinstance(rect, Polygon):
        return None
    c = list(rect.exterior.coords)[:4]
    e0 = (c[1][0] - c[0][0], c[1][1] - c[0][1])
    e1 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    l0, l1 = math.hypot(*e0), math.hypot(*e1)
    (ax, ay), length = (e0, l0) if l0 >= l1 else (e1, l1)
    if length < MIN_WIDTH:
        return None
    ux, uy = ax / length, ay / length
    nx, ny = -uy, ux
    m = rect.centroid
    hl, hd = length / 2, CUT_DEPTH / 2
    return Polygon([
        (m.x + ux * hl + nx * hd, m.y + uy * hl + ny * hd),
        (m.x + ux * hl - nx * hd, m.y + uy * hl - ny * hd),
        (m.x - ux * hl - nx * hd, m.y - uy * hl - ny * hd),
        (m.x - ux * hl + nx * hd, m.y - uy * hl + ny * hd),
    ])


def cut_openings(walls: list[Ring], windows: list[Ring], doors: list[Ring]) -> tuple[list[Ring], list[Ring], list[Ring]]:
    """Walls with gaps at their openings, and windows/doors that fill those gaps. Inputs and outputs are rings
    in model metres. An opening that is not in a wall (curtain wall, free-standing) is returned unchanged."""
    wall_polys = [p for p in (_poly(w) for w in walls) if p is not None]
    wall_union = unary_union(wall_polys) if wall_polys else None

    cuts: list[Polygon] = []

    def fill(ring: Ring, keep_footprint: bool) -> list[Ring]:
        op = _poly(ring)
        if op is None:
            return []
        cut = _cutter(op)
        gap = cut.intersection(wall_union) if cut is not None and wall_union is not None else None
        if gap is None or gap.is_empty or gap.area < MIN_GAP_AREA:
            return [_ring(op)]
        cuts.extend(_parts(gap))
        shape = unary_union([op, gap]) if keep_footprint else gap
        return [_ring(p) for p in _parts(shape)]

    # A window fills its gap together with its own frame; a door is drawn as the frame in the gap (the leaf
    # and swing are not part of IFC plan footprints).
    win_out = [r for w in windows for r in fill(w, True)]
    door_out = [r for d in doors for r in fill(d, False)]

    if not cuts:
        return [_ring(p) for p in wall_polys], win_out, door_out
    cut_all = unary_union(cuts)
    # Each wall is cut on its own: merging walls first would turn a closed ring of walls into one filled shape.
    wall_out = [_ring(p) for w in wall_polys for p in _parts(w.difference(cut_all)) if p.area > 1e-4]
    return wall_out, win_out, door_out
