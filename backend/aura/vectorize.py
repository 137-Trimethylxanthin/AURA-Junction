"""Raster floor plan -> vector plan (walls, windows, doors, rooms) with classical computer vision, 0 model calls.

`vectorize_image(path)` returns, in the pixel frame of the given image:
    {"width": W, "height": H,
     "walls":   [{"outer": [[x, y], ...], "holes": [[[x, y], ...], ...]}, ...],   closed rings, no repeat point
     "windows": [[[x, y], ...], ...],                                             closed polygons (wall gaps with glazing)
     "doors":   [{"x", "y", "w", "h"}, ...],                                      empty wall gaps (openings)
     "rooms":   [{"points": [[x, y], ...], "area_px": float}, ...],               true polygons, largest first
     "wall_px": float,                                                            estimated wall thickness
     "style": "solid" | "double-line"}                                            how walls are drawn
`plan_svg(vec)` renders it in the styles frontend/src/features/viewer3d/svgPlan.ts parses.

Pipeline (all on a working copy at most WORK_SIDE px on its long side, coordinates scaled back):
 1. ink = dark, unsaturated pixels relative to the local paper colour (photos of grey paper work; coloured
    overlays such as red unit outlines are ignored).
 2. wall style: thick strokes (poché, hatching, solid fill) vs thin double lines (US-style, photos). The
    double-line style is recognised by thin strokes plus a dominant enclosed gap length (the cavity).
 3. walls. Solid: morphological opening with a disc a bit thinner than the walls (kills text, hatching,
    dimension lines, furniture). Double-line: long straight lines (axis-aligned by morphology, slanted by
    Hough pairs) whose cavity is filled; single lines such as dimension chains never become walls.
 4. barriers for rooms = walls + long straight thin lines anchored to walls (glazing, partitions, terrace
    edges), with openings bridged: collinear gaps up to a door width (patches one wall thick only) and
    facing wall ends (skeleton endpoints) joined. Free space outside the building footprint is outside.
 5. rooms = connected components of free space inside the footprint, above a minimum area, minus slivers and
    small narrow fixture outlines; polygons from contours, Douglas-Peucker, near-axis edges snapped exactly
    horizontal/vertical, rounding chamfers removed (diagonal walls stay diagonal).
 6. windows = bundles of parallel thin lines spanning between two wall pieces (glazing), plus bridged wall
    gaps with ink inside; empty bridged gaps are doors (openings).
"""

from __future__ import annotations

import html

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MIN_SIDE = 1400          # shorter scans are upscaled before tracing (up to 3x)
WORK_SIDE = 2400          # long side of the working copy (bounded memory / time)
MIN_CONTACTS = 1
WALL_MIN_LEN = 3.5       # wall pieces shorter than this many thicknesses are glyphs or symbols
SNAP_DEG = 8.0            # edges within this angle of an axis are snapped to it


def _load(path: Path) -> tuple[np.ndarray, int, int, float]:
    """The image at most WORK_SIDE px on its long side, as RGB on white. It is shrunk first (reduce, then
    resample) so a large upload never exists at full size in another mode: this runs in the API process."""
    with Image.open(path) as im:
        W, H = im.size
        k = min(1.0, WORK_SIDE / max(W, H))
        if max(W, H) < MIN_SIDE:  # small scans: walls a few px thick close up whole rooms; trace them larger
            k = min(3.0, MIN_SIDE / max(W, H))
            im = im.convert('RGBA' if 'A' in im.mode else 'RGB').resize((round(W * k), round(H * k)), Image.LANCZOS)
        elif k < 1.0:
            im.draft('RGB', (max(1, round(W * k)), max(1, round(H * k))))  # JPEG: decode at a lower scale
            f = int(1 / k)
            if f >= 2 and im.size == (W, H):
                im = im.reduce(f)
            im = im.resize((max(1, round(W * k)), max(1, round(H * k))), Image.LANCZOS)
        if im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info):
            im = im.convert('RGBA')
            bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        a = np.asarray(im.convert('RGB'))
    return a, W, H, k


def _ink(rgb: np.ndarray) -> np.ndarray:
    """Dark, unsaturated pixels relative to the local paper colour (photos: grey paper, uneven light)."""
    a = rgb.astype(np.int16)
    sat = a.max(2) - a.min(2)
    lum = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # paper estimate: max filter (removes strokes up to ~40 px) on a small copy, then blurred
    s = max(1, max(lum.shape) // 600)
    small = cv2.resize(lum, (lum.shape[1] // s, lum.shape[0] // s), interpolation=cv2.INTER_AREA)
    bg = cv2.dilate(small, np.ones((max(3, 41 // s),) * 2, np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), max(1.0, 20 / s))
    bg = cv2.resize(bg, (lum.shape[1], lum.shape[0]), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    norm = lum.astype(np.float32) / np.maximum(bg, 1.0)
    # sat relative to brightness: coloured overlays (red unit outlines) are not walls
    return ((norm < 0.80) & (sat < 60)).astype(np.uint8)


def _disc(d: int) -> np.ndarray:
    d = max(1, int(d))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


def _wall_thickness(ink: np.ndarray) -> float:
    """Stroke thickness of the walls: 2x the distance-transform value along stroke ridges, taking the
    upper part of the distribution (walls are the thickest strokes that cover a real length)."""
    dt = cv2.distanceTransform(ink, cv2.DIST_L2, 3)
    ridge = (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8))) & (dt >= 1.5)
    v = dt[ridge]
    if v.size < 50:
        return 4.0
    # Weight each ridge value by itself (thick strokes have fewer ridge pixels per area); take a high
    # quantile but not the max (junctions and blobs inflate the max).
    t = float(np.percentile(v, 75)) * 2.0
    return float(np.clip(t, 3.0, max(ink.shape) / 20))


def _fill_small_holes(mask: np.ndarray, max_area: float) -> np.ndarray:
    inv = (1 - mask).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    small = np.zeros(n, bool)
    small[1:] = st[1:, cv2.CC_STAT_AREA] <= max_area
    # never fill a component touching the border
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    small[edge] = False
    out = mask.copy()
    out[small[lab]] = 1
    return out


def _walls(ink: np.ndarray, t: float) -> np.ndarray:
    # Close hatching first (gray poché with thin diagonals is already ink; this joins cells), then open
    # with a disc ~0.6 of the wall thickness: strokes thinner than that disappear.
    m = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, _disc(max(3, round(t * 0.25))))
    m = _fill_small_holes(m, (t * 1.2) ** 2)
    k = max(3, round(t * 0.6))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, _disc(k))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    # drop blobs (bold glyphs, filled symbols): a wall piece is longer than a few thicknesses
    longest = np.maximum(st[:, cv2.CC_STAT_WIDTH], st[:, cv2.CC_STAT_HEIGHT])
    keep[1:] = (longest[1:] >= WALL_MIN_LEN * t) & (st[1:, cv2.CC_STAT_AREA] >= 8 * t * t)
    return keep[lab].astype(np.uint8)


def _gap_hist(ink: np.ndarray, maxg: int = 40) -> np.ndarray:
    """Histogram of background run lengths enclosed by ink on both sides (rows and columns)."""
    hist = np.zeros(maxg, np.int64)
    for m in (ink, ink.T):
        d = np.diff(m.astype(np.int8), axis=1)
        r_end, c_end = np.nonzero(d == -1)
        r_st, c_st = np.nonzero(d == 1)
        key_end = r_end.astype(np.int64) * (m.shape[1] + 1) + c_end
        key_st = r_st.astype(np.int64) * (m.shape[1] + 1) + c_st
        j = np.searchsorted(key_st, key_end)
        ok = j < len(key_st)
        same = ok.copy()
        same[ok] = r_st[j[ok]] == r_end[ok]
        g = c_st[j[same]] - c_end[same]
        np.add.at(hist, g[g < maxg], 1)
    return hist


def _hollow_gap(ink: np.ndarray, t0: float) -> int:
    """Walls drawn as two thin parallel lines (no poché): return the spacing between the lines, else 0.
    Thin strokes overall plus a dominant enclosed-gap length (the wall cavity) marks this style."""
    if t0 >= 6:
        return 0
    h = _gap_hist(ink)
    seg = h[3:21]
    g = int(np.argmax(seg)) + 3
    if seg.max() > 2.5 * np.median(seg) and h[g] >= h[2]:
        return g
    return 0


def _diagonal_pairs(ink: np.ndarray, g: int, L: int) -> np.ndarray:
    """Slanted double-line walls: pairs of parallel Hough segments about `g` px apart (axis-aligned ones are
    handled by the morphological pass), filled between their overlapping extents."""
    out = np.zeros_like(ink)
    segs = cv2.HoughLinesP(ink * 255, 1, np.pi / 360, threshold=L, minLineLength=L, maxLineGap=3)
    if segs is None:
        return out
    s = np.asarray(segs).reshape(-1, 4).astype(float)
    ang = np.degrees(np.arctan2(s[:, 3] - s[:, 1], s[:, 2] - s[:, 0])) % 180
    off = np.minimum(ang % 90, 90 - ang % 90)
    s, ang = s[off > 4], ang[off > 4]
    if len(s) < 2 or len(s) > 3000:
        return out
    for i in range(len(s)):
        a0, a1 = s[i, :2], s[i, 2:]
        u = (a1 - a0) / max(np.hypot(*(a1 - a0)), 1e-9)
        nrm = np.array([-u[1], u[0]])
        for j in range(i + 1, len(s)):
            da = abs(ang[i] - ang[j])
            if min(da, 180 - da) > 3:
                continue
            b0, b1 = s[j, :2], s[j, 2:]
            dist = abs(((b0 + b1) / 2 - a0) @ nrm)
            if not (g - 3 <= dist <= g + 4):
                continue
            ta = sorted([0.0, (a1 - a0) @ u])
            tb = sorted([(b0 - a0) @ u, (b1 - a0) @ u])
            lo, hi = max(ta[0], tb[0]), min(ta[1], tb[1])
            if hi - lo < L / 2:
                continue
            side = np.sign(((b0 + b1) / 2 - a0) @ nrm) * dist
            q = np.array([a0 + u * lo, a0 + u * hi, a0 + u * hi + nrm * side, a0 + u * lo + nrm * side])
            cv2.fillPoly(out, [np.round(q).astype(np.int32)], 1)
    return out


def _hollow_walls(ink: np.ndarray, g: int) -> np.ndarray:
    """Fill the cavity between parallel long straight lines `g` px apart; single lines (dimension chains,
    leaders), text and arcs drop out."""
    L = max(25, 5 * g)
    k = g + 3
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    v = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    hf = cv2.morphologyEx(h, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    vf = cv2.morphologyEx(v, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    # keep only the filled bands (a single line is thinner than the cavity): open across the band
    hf = cv2.morphologyEx(hf, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, g + 1)))
    vf = cv2.morphologyEx(vf, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (g + 1, 1)))
    m = hf | vf | _diagonal_pairs(ink, g, L)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))  # join corners / T-junctions
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    longest = np.maximum(st[:, cv2.CC_STAT_WIDTH], st[:, cv2.CC_STAT_HEIGHT])
    keep = np.zeros(n, bool)
    keep[1:] = longest[1:] >= 1.2 * L
    return keep[lab].astype(np.uint8)


def _long_lines(ink: np.ndarray, length: int) -> np.ndarray:
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1)))
    v = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, length)))
    return h | v


def _anchored(thin: np.ndarray, walls: np.ndarray, t: float, min_contacts: int = MIN_CONTACTS) -> np.ndarray:
    """Keep thin straight lines that touch walls in at least `min_contacts` separate places (glazing,
    partitions, terrace edges); free-floating ones (dimension lines, furniture, hatching) are not room
    boundaries."""
    n, lab = cv2.connectedComponents(thin, connectivity=8)
    if n <= 1:
        return thin
    near = cv2.dilate(walls, _disc(max(3, round(t * 0.5))))
    contact = (thin & near).astype(np.uint8)
    _nc, clab = cv2.connectedComponents(contact, connectivity=8)
    m = clab > 0
    pairs = np.unique(np.stack([lab[m], clab[m]], 1), axis=0)
    ids, counts = np.unique(pairs[:, 0], return_counts=True)
    keep = np.zeros(n, bool)
    keep[ids[counts >= min_contacts]] = True
    return keep[lab].astype(np.uint8)


def _bridge(mask: np.ndarray, gap: int, t: float) -> np.ndarray:
    """Close gaps of up to `gap` px along horizontal and vertical wall lines (door/window openings) without
    filling 2D space: a filled patch is kept only when it is about one wall thick across the gap."""
    out = mask.copy()
    for ksize, across in (((gap, 1), cv2.CC_STAT_HEIGHT), ((1, gap), cv2.CC_STAT_WIDTH)):
        c = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, ksize))
        add = (c & (1 - mask)).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(add, connectivity=4)
        keep = np.zeros(n, bool)
        keep[1:] = st[1:, across] <= 2.5 * t
        out |= keep[lab].astype(np.uint8)
    return out


def _glazing(ink: np.ndarray, walls: np.ndarray, t: float) -> np.ndarray:
    """Window glazing: bundles of 2+ long thin parallel lines closer together than a wall thickness, which
    fill up into a wall-thick band, touching walls (they sit in wall openings)."""
    L = max(15, round(t * 3))
    k = max(3, round(t * 0.9))
    free = (ink & (1 - walls)).astype(np.uint8)
    out = np.zeros_like(ink)
    for line, across in (((L, 1), (1, k)), ((1, L), (k, 1))):
        m = cv2.morphologyEx(free, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, line))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, across))
        thick = (max(3, k // 2 + 1), 1) if across[0] > 1 else (1, max(3, k // 2 + 1))
        out |= cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, thick))
    # a window sits between two wall pieces: it touches walls at (at least) both ends
    out = _anchored(out, walls, t, min_contacts=2)
    n, lab, st, _ = cv2.connectedComponentsWithStats(out, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = np.minimum(st[1:, cv2.CC_STAT_WIDTH], st[1:, cv2.CC_STAT_HEIGHT]) <= 3 * t
    return keep[lab].astype(np.uint8)


def _bridge_ends(walls: np.ndarray, gap: int, t: float) -> np.ndarray:
    """Join wall ends that face each other across an opening which is not collinear (a door between a wall
    end and a perpendicular wall's end): each skeleton endpoint joins its nearest endpoint when closer than
    `gap`, by a straight band of wall thickness, when the band runs through free space."""
    from skimage.morphology import skeletonize

    sk = skeletonize(walls.astype(bool)).astype(np.uint8)
    nb = cv2.filter2D(sk, -1, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT)
    ys, xs = np.nonzero((sk == 1) & (nb == 2))
    out = np.zeros_like(walls)
    if len(xs) < 2:
        return out
    _, comp = cv2.connectedComponents(walls, connectivity=8)
    pts = np.stack([xs, ys], 1).astype(float)
    if len(pts) > 6000:  # pathological input (noise): skip rather than spend O(n^2) time
        return out
    nn = np.empty(len(pts), np.int64)
    dn = np.empty(len(pts))
    for i in range(len(pts)):  # row by row: O(n) memory
        di = np.hypot(pts[:, 0] - pts[i, 0], pts[:, 1] - pts[i, 1])
        di[i] = np.inf
        nn[i] = int(di.argmin())
        dn[i] = di[nn[i]]
    th = max(2, round(t * 0.8))
    for i in range(len(pts)):
        k = int(nn[i])
        if (nn[k] == i and k < i) or not (t < dn[i] <= gap):  # mutual pairs once
            continue
        a, b = pts[i].astype(int), pts[k].astype(int)
        if comp[a[1], a[0]] == comp[b[1], b[0]] and dn[i] < 3 * t:
            continue  # both ends of one short piece
        x0, y0 = max(0, min(a[0], b[0]) - th), max(0, min(a[1], b[1]) - th)
        x1, y1 = max(a[0], b[0]) + th + 1, max(a[1], b[1]) + th + 1
        o = np.array([x0, y0])
        line_px = cv2.line(np.zeros((y1 - y0, x1 - x0), np.uint8), tuple(a - o), tuple(b - o), 1, 1)
        near = cv2.dilate(walls[y0:y1, x0:x1], np.ones((3, 3), np.uint8))
        if (line_px & (1 - near)).sum() < 0.6 * line_px.sum():
            continue  # runs along / through walls: not an opening
        cv2.line(out, tuple(a), tuple(b), 1, th)
    return out


def _snap(pts: np.ndarray, tol_deg: float = SNAP_DEG) -> np.ndarray:
    """Snap near-axis edges to exact horizontal/vertical, keeping diagonal edges."""
    p = pts.astype(float).copy()
    n = len(p)
    if n < 3:
        return p
    tan = math.tan(math.radians(tol_deg))
    for _ in range(2):
        for i in range(n):
            j = (i + 1) % n
            dx, dy = p[j, 0] - p[i, 0], p[j, 1] - p[i, 1]
            if abs(dy) <= abs(dx) * tan:
                y = (p[i, 1] + p[j, 1]) / 2
                p[i, 1] = p[j, 1] = y
            elif abs(dx) <= abs(dy) * tan:
                x = (p[i, 0] + p[j, 0]) / 2
                p[i, 0] = p[j, 0] = x
    return p


def _clean(p: np.ndarray, eps: float) -> np.ndarray:
    """Drop duplicate and collinear vertices."""
    changed = True
    while changed and len(p) > 3:
        changed = False
        n = len(p)
        for i in range(n):
            a, b, c = p[i - 1], p[i], p[(i + 1) % n]
            ab, bc = b - a, c - b
            if np.hypot(*ab) < eps * 0.5:
                p = np.delete(p, i, 0); changed = True; break
            cross = abs(ab[0] * bc[1] - ab[1] * bc[0])
            if cross <= eps * 0.5 * max(np.hypot(*ab) + np.hypot(*bc), 1e-9) and ab @ bc > 0:
                p = np.delete(p, i, 0); changed = True; break
    return p


def _axis(d: np.ndarray) -> int:
    """0 = horizontal, 1 = vertical, -1 = other."""
    return 0 if abs(d[1]) < 1e-6 and abs(d[0]) > 0 else 1 if abs(d[0]) < 1e-6 and abs(d[1]) > 0 else -1


def _unchamfer(p: np.ndarray, short: float) -> np.ndarray:
    """Replace a short diagonal edge between a horizontal and a vertical edge by their corner."""
    i = 0
    while len(p) > 4 and i < len(p):
        n = len(p)
        a, b, c, d = p[i - 1], p[i], p[(i + 1) % n], p[(i + 2) % n]
        e = c - b
        if _axis(e) == -1 and np.hypot(*e) <= short:
            ax0, ax1 = _axis(b - a), _axis(d - c)
            if {ax0, ax1} == {0, 1}:
                corner = np.array([b[0], c[1]]) if ax0 == 1 else np.array([c[0], b[1]])
                p = np.delete(p, (i + 1) % n, 0)
                p[i if (i + 1) % n != 0 else i - 1] = corner
                continue
        i += 1
    return p


def _poly(contour: np.ndarray, eps: float, short: float = 0.0) -> np.ndarray:
    c = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
    if len(c) < 3:
        return c.astype(float)
    p = _clean(_snap(c), eps)
    if short > 0:
        p = _clean(_unchamfer(p, short), eps)
    return p


def _ring(p: np.ndarray, inv_k: float) -> list[list[float]]:
    return [[round(float(x) * inv_k, 1), round(float(y) * inv_k, 1)] for x, y in p]


def _area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def vectorize_image(path: Path) -> dict:
    rgb, W, H, k = _load(Path(path))
    inv = 1.0 / k
    ink = _ink(rgb)
    t = _wall_thickness(ink)
    g = _hollow_gap(ink, t)
    if g:
        walls = _hollow_walls(ink, g)
        t = float(g + 2 * max(1.0, t / 2))
    else:
        walls = _walls(ink, t)
    eps = max(1.5, t * 0.25)

    # --- rooms ------------------------------------------------------------------------------------------
    line_len = max(15, round(t * 3))
    thin = _anchored(_long_lines(ink, line_len) & (1 - walls), walls, t)
    gap = max(9, round(t * (8 if g else 6)))
    glaze = _glazing(ink, walls, t)
    walls_b = _bridge(walls | glaze, int(gap * 1.6) if g else gap, t)  # double doors: collinear is safe
    walls_b |= _bridge_ends(walls, gap, t)
    barrier = walls_b | thin
    barrier = cv2.dilate(barrier, np.ones((3, 3), np.uint8))  # seal 1 px leaks of anti-aliased lines
    # Building footprint: walls closed over openings up to two door widths, holes filled. Free space
    # outside it is "outside", so a room whose exterior door/window gap was not bridged still closes.
    fp = cv2.morphologyEx(barrier, cv2.MORPH_CLOSE, np.ones((2 * gap + 1,) * 2, np.uint8))
    ff = np.pad(fp, 1).copy()
    cv2.floodFill(ff, None, (0, 0), 2)
    fp = (ff[1:-1, 1:-1] != 2).astype(np.uint8)
    free = ((1 - barrier) & fp).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    hgt, wid = free.shape
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])).tolist())
    min_area = max(16 * t * t, 0.0008 * hgt * wid)
    rooms = []
    for i in range(1, n):
        if i in edge or st[i, cv2.CC_STAT_AREA] < min_area:
            continue
        x0, y0, w0, h0 = st[i, :4]
        sub = (lab[y0:y0 + h0, x0:x0 + w0] == i).astype(np.uint8)
        # undo the barrier dilation so the room reaches the wall faces / lines
        # (and close the thin notches cut by dangling lines such as door leaves)
        pad = max(3, round(t))
        sub = cv2.dilate(np.pad(sub, pad), np.ones((3, 3), np.uint8))
        sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, np.ones((max(3, round(t * 0.8)),) * 2, np.uint8))
        cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cs:
            continue
        c = max(cs, key=cv2.contourArea) + np.array([x0 - pad, y0 - pad])
        p = _poly(c, eps, short=max(2 * eps, t))
        if len(p) < 3:
            continue
        # Reject thin slivers (wall cavities, gaps between double lines).
        rect = cv2.minAreaRect(p.astype(np.float32))
        if min(rect[1]) < 2.5 * t:
            continue
        rooms.append({'points': _ring(p, inv), 'area_px': round(_area(p) * inv * inv, 1),
                      '_short': min(rect[1])})
    # Fixture outlines (tubs, counters, closets) enclose small narrow regions: drop the ones that are both
    # tiny next to the largest room and only a few wall thicknesses wide.
    big = max((r['area_px'] for r in rooms), default=0.0)
    rooms = [r for r in rooms if not (r['area_px'] < 0.03 * big and r['_short'] < 6 * t)]
    for r in rooms:
        del r['_short']
    rooms.sort(key=lambda r: -r['area_px'])

    # --- walls ------------------------------------------------------------------------------------------
    wall_polys = []
    cs, hier = cv2.findContours(walls, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is not None:
        hier = hier[0]
        for i, c in enumerate(cs):
            if hier[i][3] != -1:
                continue
            outer = _poly(c, eps)
            if len(outer) < 3:
                continue
            holes = []
            j = hier[i][2]
            while j != -1:
                if cv2.contourArea(cs[j]) > t * t:
                    h = _poly(cs[j], eps)
                    if len(h) >= 3:
                        holes.append(_ring(h, inv))
                j = hier[j][0]
            wall_polys.append({'outer': _ring(outer, inv), 'holes': holes})

    # --- openings: gaps bridged along wall lines --------------------------------------------------------
    gaps = (walls_b & (1 - walls) & (1 - glaze)).astype(np.uint8)
    gaps = cv2.morphologyEx(gaps, cv2.MORPH_OPEN, _disc(max(3, round(t * 0.5))))
    ng, glab, gst, _ = cv2.connectedComponentsWithStats(gaps, connectivity=8)
    windows, doors = [], []
    for i in range(1, ng):
        x0, y0, w0, h0 = gst[i, :4]
        if max(w0, h0) < t * 1.5 or min(w0, h0) > t * 2.5 or max(w0, h0) > gap * (1.6 if g else 1) + 2:
            continue
        roi = glab[y0:y0 + h0, x0:x0 + w0] == i
        frac = float(ink[y0:y0 + h0, x0:x0 + w0][roi].mean())
        if frac > 0.12:
            windows.append(_ring(np.array([[x0, y0], [x0 + w0, y0], [x0 + w0, y0 + h0], [x0, y0 + h0]]), inv))
        else:
            doors.append({'x': round(x0 * inv, 1), 'y': round(y0 * inv, 1), 'w': round(w0 * inv, 1),
                          'h': round(h0 * inv, 1)})

    ng, glab, gst, _ = cv2.connectedComponentsWithStats(glaze, connectivity=8)
    for i in range(1, ng):
        x0, y0, w0, h0, _a0 = gst[i]
        if max(w0, h0) >= t * 2:
            windows.append(_ring(np.array([[x0, y0], [x0 + w0, y0], [x0 + w0, y0 + h0], [x0, y0 + h0]]), inv))

    return {'width': W, 'height': H, 'walls': wall_polys, 'windows': windows, 'doors': doors,
            'rooms': rooms, 'wall_px': round(t * inv, 2),
            'style': 'double-line' if g else 'solid'}


def _d(ring) -> str:
    return 'M' + ' L'.join(f'{x:g} {y:g}' for x, y in ring) + ' Z'


def plan_svg(vec: dict) -> str:
    W, H = vec['width'], vec['height']
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for r in vec.get('rooms', []):
        out.append(f'<path d="{_d(r["points"])}" fill="#eef0f3" stroke="none"/>')
    for r in vec.get('rooms', []):
        out.append(f'<path d="{_d(r["points"])}" fill="none" stroke="#c9ced6" stroke-width="0.8"/>')
    for w in vec.get('windows', []):
        out.append(f'<path d="{_d(w)}" fill="#ffffff" stroke="#374151" stroke-width="0.9"/>')
    for d in vec.get('doors', []):
        if isinstance(d, dict):
            x, y, w, h = d['x'], d['y'], d['w'], d['h']
            ring = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        else:
            ring = d
        out.append(f'<path d="{_d(ring)}" fill="none" stroke="#4b5563" stroke-width="0.9"/>')
    for w in vec.get('walls', []):
        d = ' '.join(_d(r) for r in [w['outer'], *w.get('holes', [])])
        out.append(f'<path d="{d}" fill="#2f343c" fill-rule="evenodd" stroke="#2f343c" stroke-width="0.6"/>')
    fs = round(max(10.0, min(W, H) * 0.022), 1)
    for lab in vec.get('labels', []):  # room names (from the drawing's rooms), centred on their label point
        text = html.escape(str(lab.get('text', ''))[:60])
        if text:
            out.append(f'<text x="{lab["x"]:g}" y="{lab["y"]:g}" font-family="Inter, Helvetica, Arial, sans-serif" '
                       f'font-size="{fs}" fill="#4b5563" text-anchor="middle" dominant-baseline="middle">{text}</text>')
    out.append('</svg>')
    return '\n'.join(out)
