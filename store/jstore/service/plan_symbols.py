"""Door symbols of an image plan, from a detection box and the plan's walls.

A detection box of a door is either the opening alone (a thin box in the wall) or the opening with its swing (a
squarish box beside the wall). The symbol is anchored to the wall line, its leaf is held to a plausible door width
at the drawing's scale, and a door that cannot be placed with confidence is drawn as a plain opening marker: a
symbol never dominates the plan. Everything here is in drawing units; `u` is metres per unit.
"""
from __future__ import annotations

import shapely
from shapely.geometry import LineString

SINGLE_MAX_M = 1.25     # a single leaf: 0.6-1.2 m, with a little tolerance for the detection box
SINGLE_MIN_M = 0.55
DOUBLE_MAX_M = 2.1      # a double door: up to 2.0 m
MARKER_M = 0.9          # the opening drawn for a door of unknown width
THIN = 0.45             # a box this flat is the opening alone


def _cover(solid, p, q, t: float) -> float:
    """Length of wall along the segment p-q (within t either side of it)."""
    if solid is None or p == q:
        return 0.0
    strip = LineString([p, q]).buffer(t, cap_style="flat")
    return float(strip.intersection(solid).area) / (2 * t)


def _gap(solid, a, b, t: float) -> tuple[tuple, tuple] | None:
    """The longest stretch of the segment a-b that is not wall."""
    line = LineString([a, b])
    free = line.difference(solid.buffer(t * 0.25)) if solid is not None else line
    parts = [g for g in getattr(free, "geoms", [free]) if g.geom_type == "LineString" and g.length > 0]
    if not parts:
        return None
    g = max(parts, key=lambda s: s.length)
    return tuple(g.coords[0]), tuple(g.coords[-1])


def door_symbol(x: float, y: float, w: float, h: float, solid, u: float) -> dict:
    """How to draw the door detected in the box (x, y, w, h): `mode` single | double | marker, the opening `a`-`b`
    on the wall line, the unit normal `n` the leaf swings to, and `anchored`: the opening lies on a wall."""
    if max(w, h) <= 0 or u <= 0:
        return dict(mode="marker", a=(x, y + h / 2), b=(x + w, y + h / 2), n=(0.0, 1.0), anchored=False)
    t = 0.15 / u
    local = None
    if solid is not None and not solid.is_empty:
        pad = max(w, h)
        local = solid.intersection(shapely.box(x - pad, y - pad, x + w + pad, y + h + pad))
        local = None if local.is_empty else local
    long, short = max(w, h), min(w, h)
    thin = short / long < THIN or (short * u < SINGLE_MIN_M and short < long)   # too flat or too shallow for a swing
    if thin:                                # the opening alone: its long axis is the wall line
        a, b = ((x, y + h / 2), (x + w, y + h / 2)) if w >= h else ((x + w / 2, y), (x + w / 2, y + h))
        base = (0.0, 1.0) if w >= h else (1.0, 0.0)
        n = base
        for sign in (1, -1):                               # the leaf swings to the side that is not wall
            tip = (a[0] + sign * base[0] * (long + short / 2), a[1] + sign * base[1] * (long + short / 2))
            n = (sign * base[0], sign * base[1])
            if local is None or not shapely.contains_xy(local, *tip):
                break
        length, depth, anchored = long, long, local is not None and _cover(local, _ext(a, b, -0.5), _ext(b, a, -0.5), t) > 0
    else:                                                  # opening + swing: one edge of the box is the wall line
        edges = [((x, y), (x + w, y), (0.0, 1.0), w, h), ((x, y + h), (x + w, y + h), (0.0, -1.0), w, h),
                 ((x, y), (x, y + h), (1.0, 0.0), h, w), ((x + w, y), (x + w, y + h), (-1.0, 0.0), h, w)]
        best, score = None, 0.0
        for e in edges:
            p, q, _, ln, _ = e
            s = (_cover(local, _ext(p, q, -0.5), p, t) + _cover(local, q, _ext(q, p, -0.5), t)
                 - 0.5 * _cover(local, p, q, t)) / ln
            if best is None or s > score:
                best, score = e, s
        if score < 0.2:                                    # no wall beside the box: where the door stands is unknown
            a, b = ((x, y + h / 2), (x + w, y + h / 2)) if w >= h else ((x + w / 2, y), (x + w / 2, y + h))
            return _marker(a, b, (0.0, 1.0) if w >= h else (1.0, 0.0), u, anchored=False)
        a, b, n, length, depth = best
        anchored = True
    metres = length * u
    if metres <= SINGLE_MAX_M:
        return dict(mode="single", a=a, b=b, n=n, anchored=anchored)
    if metres <= DOUBLE_MAX_M and anchored and (depth <= 0.7 * length or thin):
        return dict(mode="double", a=a, b=b, n=n, anchored=anchored)
    gap = _gap(local, a, b, t) if anchored and local is not None else None
    if gap is not None:
        glen = LineString(gap).length * u
        if SINGLE_MIN_M <= glen <= SINGLE_MAX_M:           # the wall itself shows the opening inside an oversized box
            return dict(mode="single", a=gap[0], b=gap[1], n=n, anchored=True)
    if thin and (anchored or metres <= DOUBLE_MAX_M):
        return dict(mode="marker", a=a, b=b, n=n, anchored=anchored, oversized=True)   # a wide opening, as detected
    return _marker(a, b, n, u, anchored=anchored, oversized=True)


def _ext(p, q, f: float) -> tuple[float, float]:
    """The point p + f * (q - p)."""
    return p[0] + f * (q[0] - p[0]), p[1] + f * (q[1] - p[1])


def _marker(a, b, n, u: float, **kw) -> dict:
    """An opening of a plausible width in the middle of a-b."""
    length = LineString([a, b]).length
    keep = min(1.0, (MARKER_M / u) / length) if length else 1.0
    return dict(mode="marker", a=_ext(a, b, 0.5 - keep / 2), b=_ext(a, b, 0.5 + keep / 2), n=n, **kw)


def door_paths(sym: dict, stroke: str) -> list[str]:
    """SVG of a door symbol: the opening, the leaf standing open at the hinge and the arc of its swing; a marker is
    the opening with its two jambs only."""
    a, b, n = sym["a"], sym["b"], sym["n"]
    length = LineString([a, b]).length
    if length <= 0:
        return []
    d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    out = [f'<path d="M{a[0]:.1f},{a[1]:.1f}L{b[0]:.1f},{b[1]:.1f}" fill="none" stroke="{stroke}" stroke-width="0.5"/>']
    if sym["mode"] == "marker":
        k = min(length * 0.12, 4.0)
        for p in (a, b):
            out.append(f'<path d="M{p[0] - n[0] * k:.1f},{p[1] - n[1] * k:.1f}L{p[0] + n[0] * k:.1f},{p[1] + n[1] * k:.1f}" '
                       f'fill="none" stroke="{stroke}" stroke-width="1"/>')
        return out
    leaves = [(a, d, length)] if sym["mode"] == "single" else [(a, d, length / 2), (b, (-d[0], -d[1]), length / 2)]
    for hinge, along, r in leaves:
        tip = (hinge[0] + n[0] * r, hinge[1] + n[1] * r)
        end = (hinge[0] + along[0] * r, hinge[1] + along[1] * r)
        sweep = 1 if n[0] * along[1] - n[1] * along[0] > 0 else 0
        out.append(f'<path d="M{hinge[0]:.1f},{hinge[1]:.1f}L{tip[0]:.1f},{tip[1]:.1f}" fill="none" stroke="{stroke}" stroke-width="1"/>')
        out.append(f'<path d="M{tip[0]:.1f},{tip[1]:.1f}A{r:.1f},{r:.1f} 0 0 {sweep} {end[0]:.1f},{end[1]:.1f}" fill="none" '
                   f'stroke="{stroke}" stroke-width="0.5"/>')
    return out


def opening_cut(sym: dict, solid, u: float):
    """The piece of wall a placed door opens: the wall within the opening, across its thickness."""
    if solid is None or sym["mode"] == "marker" or not sym.get("anchored") or u <= 0:
        return None
    cut = LineString([sym["a"], sym["b"]]).buffer(0.3 / u, cap_style="flat").intersection(solid)
    return None if cut.is_empty else cut
