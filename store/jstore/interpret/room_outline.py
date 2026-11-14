"""Room outlines on a raster floor plan: Gemini draws the polygon of one room, the pixels put its edges on the lines.

    outline_rooms(im, rooms, m_per_px, budget_s, log) -> one result per room, in the order given

How one room is outlined (measured on 65 plans read without their CAD twin, 1,617 rooms with CAD truth: median
area error 0.5 %, 97 % of the rooms within 5 %, perimeter 0.3 %; wall segmentation alone: 5.5 %, 46 %, 3.2 %):
  1. crop     a cut-out around the room (around the hint polygon when the caller has one, else about 5 m around the
              label) with a margin, enlarged so that its long side is VIEW_SIDE pixels (at most MAX_UP times).  The
              label is marked with a dot; no proposal is drawn, so the answer is independent of the hint.
  2. polygon  MODEL returns the corner points of this one room.  A polygon that touches the border of the crop is
              asked again in a larger crop (REGROW times); one that does not hold the label (and does not overlap
              the hint) is asked again (RETRIES times); one that holds the label of another room is asked once
              more with those rooms named as not part of it.
  3. snap     jogs below SIMPLIFY_M are dropped, then every edge is moved along its normal onto the nearest drawn
              line within SNAP_M (the centre of a thin line, the room-side border of a thick one).  `support` is
              the share of the perimeter that found a line.
  4. compare  when all rooms are drawn: `overlap` = the largest share of the outline that lies inside another
              room's outline; `open_m` = metres of edges with no line under them and free floor beyond that no
              other outline holds (the room was cut where nothing on the plan ends it).
What the caller gets per room: {polygon [(x, y)] in image pixels or None, support, label_inside, others_inside,
touches_crop_border, overlap, open_m, calls, reason (when there is no polygon)}.  Confidence is the caller's
business: the best signals are the IoU with an independent outline, overlap and open_m (see raster_rooms).
m_per_px sizes the cut-out and the tolerances only; a rough value (within a factor of two) is enough.

Time: every request stops at the budget; rooms that are not answered by then come back without a polygon and with
reason 'budget'.  At most WORKERS requests run at once in one process (the pipeline interprets
JSTORE_INGEST_WORKERS drawings in parallel processes, each with its own limit).  Answers are cached under <store>/cache/gemini (key: the
bytes of the view, model, prompt, schema, VERSION), so a repeated run asks nothing.
Settings: JSTORE_ROOM_OUTLINE_MODEL (gemini-4-carbon), JSTORE_ROOM_OUTLINE_WORKERS (16, never more per process), and those of
jstore.gemini_detect (endpoint, concurrency).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Point, Polygon, box as sbox

from .. import gemini_detect as G

VERSION = "ro-1"
MODEL = os.environ.get("JSTORE_ROOM_OUTLINE_MODEL", "gemini-4-carbon")
THINKING = "low"
WORKERS = max(1, min(16, int(os.environ.get("JSTORE_ROOM_OUTLINE_WORKERS", "16"))))
CALL_TIMEOUT = 60.0
VIEW_SIDE = 1024          # long side of the crop sent, pixels
MAX_UP = 6.0              # most a crop is enlarged
MARGIN_M, MARGIN_FRAC = 1.2, 0.30      # margin around the hint: metres, or this share of its longer side
LABEL_ONLY_M = 2.5        # without a hint: this far around the label
SNAP_M = 0.10             # how far an edge may move onto a line
SIMPLIFY_M = 0.025        # jogs below this are dropped before snapping
RETRIES = 2               # more answers when the polygon is not on the room meant
REGROW = 3                # larger crops when the polygon touches the crop border
OPEN_MIN_M = 0.6          # an edge at least this long without a line under it may be an open end of the room ...
OPEN_BEYOND_M = 0.5       # ... when the floor is free this far beyond it and belongs to no other outline

SCHEMA = {"type": "object", "properties": {"polygon": {"type": "array", "items": {
    "type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}}}, "required": ["polygon"]}
PROMPT = (
    "The image is a part cut out of an architectural floor plan. {which}\n"
    "Return the outline of THIS ONE room as a polygon: its corner points [x, y] in order around the room, "
    "normalised to 0-1000 (x from the left edge to the right edge, y from the top edge to the bottom edge of the image).\n"
    "Rules:\n"
    "- Corners lie exactly on the INNER faces of the walls that enclose the room (the line where the room's floor "
    "meets the wall). Wall thickness is NOT part of the room.\n"
    "- Door openings and window openings are closed straight across, along the wall face; door swing arcs, window "
    "niches inside the wall and the wall itself are not part of the room.\n"
    "- Follow every jog, recess and shaft/column cut-out of the wall face with its own corners; do not simplify to a "
    "rectangle when the room is not one. Furniture, fixtures, stairs and floor hatching inside the room do not "
    "change the outline.\n"
    "- The room ends where a wall, a drawn line across an opening or another room's label area begins; do not "
    "include neighbouring rooms even when an opening connects them. If no line separates the room from the next "
    "space, cut straight across at the narrowest point of the opening.\n"
    "- For a balcony, loggia or terrace bounded by thin lines (railing, slab edge) instead of walls, follow those "
    "lines and the outer face of the building wall it adjoins.\n"
    "{extra}"
    "Give only the corners that are really there, at pixel precision."
)
EXTRA_OTHERS = ("- IMPORTANT: these other rooms have their own labels and are NOT part of this room: {names}. Leave their "
                "floor out. Where no wall separates one of them from this room, the boundary is the drawn line between "
                "them (edge of a stair flight, a floor-finish line, a threshold) or, without any line, a straight cut "
                "across the narrowest point between the two labels.\n")


def _name(room: dict) -> str:
    return " ".join(str(x) for x in (room.get("label"), room.get("number")) if x)


def _which(room: dict) -> str:
    name = _name(room)
    if name:
        return (f"The room meant carries the label '{name}'. A small magenta dot is drawn just above where that label "
                "was read; if the dot sits at a different label, go by the label text.")
    return "One room is meant: the one with the small magenta dot in it."


# ------------------------------------------------------------------ request
def generate(view: Image.Image, prompt: str, stats: G.Stats, salt: str = "") -> dict | None:
    """Structured answer to one view, from the cache or the API (gemini_detect's request helper: retries, the
    shared concurrency limit, stops at the deadline of `stats`).  None on failure."""
    png = G._png(view)
    key = hashlib.sha256(png + f"|{MODEL}|{THINKING}|{VERSION}|{prompt}|{json.dumps(SCHEMA, sort_keys=True)}|{salt}"
                         .encode()).hexdigest()
    hit = G._cached(key)
    if hit is not None:
        stats.count(MODEL, True)
        return hit["answer"]
    body = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}}, {"text": prompt}]}],
        "generationConfig": {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": SCHEMA}},
                             "thinkingConfig": {"thinkingLevel": THINKING}}}
    t = time.time()
    answer, why, js = None, "", {}
    for _ in range(2):                                   # an unreadable answer is asked once more
        status, js = G._post(f"/models/{MODEL}:generateContent", body, stats, CALL_TIMEOUT)
        if status != -2:
            stats.count(MODEL, False)
        if status != 200:
            why = G._err(status, js)
            break
        try:
            parts = js["candidates"][0]["content"]["parts"]
            answer = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            break
        except (KeyError, IndexError, ValueError, TypeError) as e:
            why = f"unreadable answer: {type(e).__name__}"
    if not isinstance(answer, dict):
        stats.error(f"room outline: {why or 'answer is not an object'}")
        return None
    G._store(key, dict(model=MODEL, version=VERSION, answer=answer, seconds=round(time.time() - t, 1),
                       usage=js.get("usageMetadata"), view_px=list(view.size)))
    return answer


# ------------------------------------------------------------------ the image
class Plan:
    """The page in colour (for the views) and its ink (for snapping)."""

    def __init__(self, im: Image.Image):
        self.im = im if im.mode == "RGB" else im.convert("RGB")
        gray = np.asarray(self.im.convert("L"))
        a = np.asarray(self.im)
        ink = (gray < 128) & ((a.max(axis=2) - a.min(axis=2)) < 70)      # dark and not coloured
        if ink.mean() < 0.002:                                            # a coloured drawing: every dark pixel
            ink = gray < 128
        self.ink = ink
        self.lw = self._line_width(ink)

    @staticmethod
    def _line_width(ink: np.ndarray) -> float:
        """Usual thin stroke: the median horizontal run of ink among runs shorter than 12 px."""
        rows = ink[:: max(1, ink.shape[0] // 400)].astype(np.int8)
        d = np.diff(np.pad(rows, ((0, 0), (1, 1))), axis=1)
        runs = np.nonzero(d == -1)[1] - np.nonzero(d == 1)[1]            # both in row order: the k-th end closes the k-th start
        runs = runs[runs < 12]
        return float(np.median(runs)) if runs.size else 2.0


def crop_box(room: dict, W: int, H: int, m_per_px: float, grow: float = 1.0) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in room.get("hint") or []], [p[1] for p in room.get("hint") or []]
    b = room["label_box"]
    xs += [b[0], b[2]]
    ys += [b[1], b[3]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if not room.get("hint"):
        r = LABEL_ONLY_M / m_per_px
        x0, x1, y0, y1 = x0 - r, x1 + r, y0 - r, y1 + r
    m = grow * max(MARGIN_M / m_per_px, MARGIN_FRAC * max(x1 - x0, y1 - y0))
    return max(0, int(x0 - m)), max(0, int(y0 - m)), min(W, int(x1 + m) + 1), min(H, int(y1 + m) + 1)


def make_view(plan: Plan, room: dict, box, side: float) -> Image.Image:
    c = plan.im.crop(box)
    s = min(MAX_UP, side / max(c.size))
    v = c.resize((max(8, round(c.width * s)), max(8, round(c.height * s))), Image.LANCZOS)
    b = room["label_box"]
    cx, cy = ((b[0] + b[2]) / 2 - box[0]) * s, (b[1] - box[1]) * s - 5
    ImageDraw.Draw(v).ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 0, 255))
    return v


def to_px(ans: dict, box) -> list | None:
    """Corner points of an answer (0-1000 of the view) in image pixels; None when it is not a polygon."""
    try:
        pts = [(box[0] + min(1000.0, max(0.0, float(x))) / 1000 * (box[2] - box[0]),         # never outside the view
                box[1] + min(1000.0, max(0.0, float(y))) / 1000 * (box[3] - box[1])) for x, y in ans["polygon"]]
    except (TypeError, ValueError, KeyError):
        return None
    out: list = []
    for p in pts:
        if not (math.isfinite(p[0]) and math.isfinite(p[1])):
            return None
        if not out or math.dist(p, out[-1]) > 0.5:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 0.5:
        out.pop()
    return out if len(out) >= 3 else None


def shape(poly) -> Polygon | None:
    """A valid polygon of the points (the largest part of a self-crossing ring), or None."""
    try:
        P = Polygon(poly)
        if not P.is_valid:
            P = P.buffer(0)
        if P.geom_type == "MultiPolygon":
            P = max(P.geoms, key=lambda g: g.area)
        return P if P.geom_type == "Polygon" and not P.is_empty and P.area > 0 else None
    except Exception:  # noqa: BLE001 - degenerate rings
        return None


def simplify(poly: list, tol: float) -> list:
    """Drop jogs smaller than tol (pixels); also makes the ring valid."""
    P = shape(poly)
    if P is None:
        return poly
    pts = list(P.simplify(tol, preserve_topology=True).exterior.coords)[:-1]
    return pts if len(pts) >= 3 else poly


def iou(p, q) -> float:
    """Intersection over union of two polygons (point lists or shapely polygons)."""
    A = p if isinstance(p, Polygon) else shape(p)
    B = q if isinstance(q, Polygon) else shape(q)
    if A is None or B is None:
        return 0.0
    try:
        u = A.union(B).area
        return A.intersection(B).area / u if u else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def label_inside(poly, room: dict) -> bool:
    P = shape(poly)
    L = sbox(*room["label_box"])
    return P is not None and L.area > 0 and P.intersection(L).area >= 0.5 * L.area


def others_inside(poly, room: dict, rooms) -> list[dict]:
    """Other rooms whose label lies inside the polygon: the outline swallowed (part of) a neighbour."""
    P = shape(poly)
    if P is None:
        return []
    out = []
    for r in rooms:
        if r is room:
            continue
        L = sbox(*r["label_box"])
        if L.area > 0 and P.intersection(L).area >= 0.8 * L.area:
            out.append(r)
    return out


# ------------------------------------------------------------------ snapping edges onto drawn lines
def _profile(ink: np.ndarray, a, b, n, offs: np.ndarray) -> np.ndarray:
    """Ink coverage along the segment a-b moved by each offset along the normal n."""
    k = max(2, int(math.dist(a, b)))
    t = (np.arange(k) + 0.5) / k
    px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
    X = np.rint(px[None, :] + n[0] * offs[:, None]).astype(int)
    Y = np.rint(py[None, :] + n[1] * offs[:, None]).astype(int)
    ok = (X >= 0) & (Y >= 0) & (X < ink.shape[1]) & (Y < ink.shape[0])
    v = np.zeros(X.shape, bool)
    v[ok] = ink[Y[ok], X[ok]]
    return v.mean(axis=1)


def snap(poly: list, plan: Plan, m_per_px: float) -> tuple[list, float]:
    """Move every edge along its normal onto the nearest drawn line (the centre of a thin line, half a thin line
    inside the room-side border of a thick one).  -> (polygon, share of the perimeter that found a line)."""
    P = Polygon(poly)
    if not P.is_valid or P.area <= 0:
        return poly, 0.0
    ccw = P.exterior.is_ccw
    R = max(3.0, SNAP_M / m_per_px)
    offs = np.arange(-math.ceil(R), math.ceil(R) + 1, dtype=float)
    n_pts = len(poly)
    lines, found, total = [], 0.0, 0.0
    for i in range(n_pts):
        a, b = poly[i], poly[(i + 1) % n_pts]
        L = math.dist(a, b)
        dx, dy = (b[0] - a[0]) / max(L, 1e-9), (b[1] - a[1]) / max(L, 1e-9)
        n = (dy, -dx) if ccw else (-dy, dx)                   # outward (image coordinates, y down)
        shift = 0.0
        total += L
        if L >= max(6.0, 0.25 / m_per_px):
            e = min(L * 0.15, plan.lw * 2 + 2)                # keep clear of the corners
            cov = _profile(plan.ink, (a[0] + dx * e, a[1] + dy * e), (b[0] - dx * e, b[1] - dy * e), n, offs)
            on = cov >= max(0.35, 0.6 * cov.max())
            best = None
            j = 0
            while j < len(on):
                if not on[j]:
                    j += 1
                    continue
                k = j
                while k + 1 < len(on) and on[k + 1]:
                    k += 1
                w = k - j + 1
                thick = w > plan.lw * 1.6
                face = offs[j] - 0.5 + min(w, plan.lw) / 2 if thick else (offs[j] + offs[k]) / 2
                if not (j == 0 and thick) and (best is None or abs(face) < abs(best)):
                    best = face                                # (a thick run cut by the window's start: border unknown)
                j = k + 1
            if best is not None and cov.max() >= 0.35:
                shift = best
                found += L
        lines.append(((a[0] + n[0] * shift, a[1] + n[1] * shift), (dx, dy)))
    out = []
    for i in range(n_pts):
        (p, d), (q, e) = lines[i - 1], lines[i]
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 0.2:                                    # nearly parallel neighbours: the vertex moves with both
            v = poly[i]
            n1 = (p[0] - poly[i - 1][0], p[1] - poly[i - 1][1])
            n2 = (q[0] - poly[i][0], q[1] - poly[i][1])
            out.append((v[0] + (n1[0] + n2[0]) / 2, v[1] + (n1[1] + n2[1]) / 2))
            continue
        t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
        out.append((p[0] + d[0] * t, p[1] + d[1] * t))
    Q = Polygon(out)
    if not Q.is_valid or Q.area < 0.7 * P.area or Q.area > 1.4 * P.area:
        return poly, 0.0
    return out, found / max(total, 1e-9)


def open_length(poly: list, plan: Plan, others: list, m_per_px: float) -> float:
    """Metres of the outline that lie on no drawn line and have free floor beyond them that no other outline holds:
    there the room was cut where nothing on the plan ends it (an open plan, or only a part of the room was drawn)."""
    P = Polygon(poly)
    if not P.is_valid or P.area <= 0:
        return 0.0
    ccw = P.exterior.is_ccw
    R = math.ceil(max(3.0, SNAP_M / m_per_px))
    near = np.arange(-R, R + 1, dtype=float)
    reach = max(R + 2.0, OPEN_BEYOND_M / m_per_px)
    far = np.arange(R + 1, reach + 1, dtype=float)
    open_px = 0.0
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        L = math.dist(a, b)
        if L < max(6.0, OPEN_MIN_M / m_per_px):
            continue
        dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        n = (dy, -dx) if ccw else (-dy, dx)
        e = min(L * 0.15, plan.lw * 2 + 2)
        a2, b2 = (a[0] + dx * e, a[1] + dy * e), (b[0] - dx * e, b[1] - dy * e)
        if _profile(plan.ink, a2, b2, n, near).max() >= 0.35:            # a line under the edge
            continue
        if _profile(plan.ink, a2, b2, n, far).mean() >= 0.08:             # a wall, hatching or fittings beyond it
            continue
        probes = [Point(a[0] + (b[0] - a[0]) * t + n[0] * reach, a[1] + (b[1] - a[1]) * t + n[1] * reach)
                  for t in (0.2, 0.35, 0.5, 0.65, 0.8)]
        free = sum(1 for q in probes if not P.contains(q) and not any(o.contains(q) for o in others))
        if free >= 3:
            open_px += L
    return open_px * m_per_px


def overlap_share(P: Polygon, others: list) -> float:
    """Largest share of the outline that lies inside the outline of another room."""
    worst = 0.0
    for o in others:
        try:
            if P.intersects(o):
                worst = max(worst, P.intersection(o).area / P.area)
        except Exception:  # noqa: BLE001
            pass
    return worst


# ------------------------------------------------------------------ one room
def ask_polygon(plan: Plan, room: dict, m_per_px: float, stats: G.Stats, salt: str = "", extra: str = ""):
    """-> (polygon in image pixels or None, requests made, the polygon still touches the border of the crop)."""
    W, H = plan.im.size
    grow, poly, calls, cut = 1.0, None, 0, False
    prompt = PROMPT.format(which=_which(room), extra=extra)
    for attempt in range(REGROW + 1):
        if stats.left() < 3:
            return (poly, calls, cut) if poly is not None else (None, calls, False)
        box = crop_box(room, W, H, m_per_px, grow)
        ans = generate(make_view(plan, room, box, VIEW_SIDE * (1 if attempt == 0 else 1.5)), prompt, stats, salt)
        calls += 1
        new = to_px(ans, box) if ans else None
        if new is None:
            if poly is None:
                return None, calls, False
            break                                             # keep the polygon of the smaller crop
        poly = new
        bw, bh, tol = box[2] - box[0], box[3] - box[1], 0.025
        touch = sum(1 for x, y in poly if (x - box[0] < tol * bw and box[0] > 0) or (box[2] - x < tol * bw and box[2] < W)
                    or (y - box[1] < tol * bh and box[1] > 0) or (box[3] - y < tol * bh and box[3] < H))
        cut = touch >= 2
        if not cut:
            break
        grow *= 2.2
    return poly, calls, cut


def one_room(plan: Plan, room: dict, rooms, m_per_px: float, stats: G.Stats) -> dict:
    calls = 0
    hint = shape(room["hint"]) if room.get("hint") else None

    def good(p) -> bool:                                      # the room meant: holds its label or overlaps the hint
        return p is not None and (label_inside(p, room) or (hint is not None and iou(p, hint) >= 0.3))

    def none(reason: str) -> dict:
        return dict(polygon=None, calls=calls, reason="budget" if stats.left() < 3 else reason)

    if stats.left() < 3:
        return none("budget")
    p, cut = None, False
    for retry in range(1 + RETRIES):
        p, c, cut = ask_polygon(plan, room, m_per_px, stats, salt="" if not retry else f"r{retry}")
        calls += c
        if good(p) or stats.left() < 3:
            break
    if not good(p):
        return none("no answer" if p is None else "the polygons returned do not hold the label")
    oth = others_inside(p, room, rooms)
    if oth:
        names = ", ".join(f"'{_name(r)}'" for r in oth[:6])
        p2, c, cut2 = ask_polygon(plan, room, m_per_px, stats, extra=EXTRA_OTHERS.format(names=names))
        calls += c
        if good(p2) and len(others_inside(p2, room, rooms)) < len(oth):
            p, cut = p2, cut2
    p = simplify(p, SIMPLIFY_M / m_per_px)
    p, support = snap(p, plan, m_per_px)
    if shape(p) is None:
        return none("the polygon returned is not a simple outline")
    return dict(polygon=[(round(float(x), 2), round(float(y), 2)) for x, y in p], support=round(float(support), 3),
                label_inside=label_inside(p, room), others_inside=len(others_inside(p, room, rooms)),
                touches_crop_border=bool(cut), calls=calls)


def outline_rooms(im: Image.Image, rooms: list[dict], m_per_px: float, budget_s: float, log: dict) -> list[dict]:
    """Outline every room.  rooms: [{label, number, label_box [x0, y0, x1, y1] in image pixels, hint: optional point
    list of a rough outline, used to place the crop and to tell the right room, never shown to the model}].
    -> per room {polygon or None, ...} (module docstring).  Never raises for a single room; `log` gets the counts."""
    t0 = time.time()
    stats = G.Stats(deadline=t0 + max(0.0, budget_s))
    plan = Plan(im)

    def safe(r):
        try:
            return one_room(plan, r, rooms, m_per_px, stats)
        except Exception as e:  # noqa: BLE001 - one odd room must not lose the others
            stats.error(f"room outline {_name(r)}: {e!r}")
            return dict(polygon=None, calls=0, reason=f"failed: {type(e).__name__}")
    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(safe, rooms))
    shapes = [shape(o["polygon"]) if o["polygon"] is not None else None for o in res]
    for i, o in enumerate(res):                               # each outline against the others
        if shapes[i] is not None:
            others = [s for j, s in enumerate(shapes) if j != i and s is not None]
            o.update(overlap=round(overlap_share(shapes[i], others), 3),
                     open_m=round(open_length(o["polygon"], plan, others, m_per_px), 2))
    late = sum(1 for o in res if o["polygon"] is None and o.get("reason") == "budget")
    log.update(model=MODEL, version=VERSION, rooms=len(rooms), polygons=sum(1 for o in res if o["polygon"] is not None),
               calls=stats.calls, cached=stats.cached, budget_s=round(budget_s, 1), seconds=round(time.time() - t0, 1),
               budget_ended=late > 0, not_answered_in_budget=late, errors=stats.errors[:5], n_errors=len(stats.errors))
    return res
