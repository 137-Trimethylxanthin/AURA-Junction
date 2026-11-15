"""Rooms of a vector PDF that has no CAD or model twin.

    interpret(db, f) -> merge() counts, or {"skipped": reason}

`plan.interpret_pdf` reads PDFs that follow one convention: a room number like 2.01 inside a closed outline.  This
reader covers the others (a name over a number, a name over a printed area in m² or square feet, rooms bounded by
wall linework only), on every page of the file.  It does nothing when the drawing already has rooms, whether from a
CAD / IFC twin or from `plan.interpret_pdf` on this same file.

Per page
  tags      text lines stacked on each other are read as one room tag: name line(s), an identifier ("54-1",
            "401", "2.01"), a printed area ("12.3 m²", "147 SF").  A tag is *strong* when it has a printed area;
            a number with or without a name, or a lone known room name, is *weak* and is kept only when an
            outline is found for it.  Pages drawn smaller than 1:300 are site plans and are left alone.
  outline   (a) closed paths: the smallest closed path around the tag that holds no other tag; the pen most of
                these share is the room pen, other pens are accepted with lower confidence;
            (b) for tags without one, the free space around the tag: linework is rasterised, a watershed over the
                distance to it is seeded at the tags and at every other open area, and areas are joined unless a
                constriction of door width separates them.  Which linework counts as walls is not known, so three
                choices are tried and the one that best reproduces the printed areas of the page is taken.  The result is
                used only when at least half of the page's printed areas are reproduced within 10 %; a page
                without printed areas gets no free-space outlines.
            (c) for tags still without one, the image path on the page render (image_outlines): the outline a
                scan's room gets, in page points; dropped when it contradicts the tag's printed area.
  scale     the frame of the page, else the page's trusted scale, else the scale (of those printed on the page, or
            free) that makes outline areas equal the printed areas.
  claims    net_area_m2 'printed' (square feet converted, the original kept in attrs) and 'measured_outline'.

Nothing here knows a file name or a data set; every choice made per file is stored in `definition`.
"""
from __future__ import annotations

import collections
import math
import os
import re
import time

import numpy as np
from shapely import from_wkb
from shapely.affinity import affine_transform
from shapely.geometry import Point, Polygon, box
from shapely.strtree import STRtree

from ..db import DB
from ..frames import PT_M, trusted_scale
from . import define, merge, vocab
from .plan import _ring

BY = "jstore.interpret.pdf_rooms"
TIME_BUDGET = 35.0                # seconds per file
FREE_SPACE_BUDGET = 12.0          # of which for the free-space search (b), over all pages
MAX_PAGES = 40
SQFT = 0.09290304
AREA_FT = re.compile(r"^(?:(?:AREA|A)\s*[:=]?\s*)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*"
                     r"(s\.?\s?f\.?|sq\.?\s?ft\.?|sqft|ft2|ft²|square feet)$", re.I)
IDENT = re.compile(r"^(?:(?:room|rm|rum|raum|zimmer|local|locale|huone|nr|no|n°|#)\.?:?\s*){0,2}"
                   r"((?:[A-Z]{1,2}[ -]?)?\d{1,4}(?:[.\-/]\d{1,3}){0,2}[A-Za-z]?)$", re.I)
LABEL = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'&+/\-]*(?:\s?#?\d{1,2})?\.?$")
ROOM_M2 = (0.3, 5000.0)           # plausible size of one room
DOOR_HALF = 0.62                  # half the widest opening that still separates two rooms, metres
CELL = 0.03                       # raster cell of the free-space search, metres
SITE_PLAN = 300.0                 # a page drawn smaller than 1:300 shows a site, not rooms
# abbreviations and words for outdoor spaces that vocab.OUTDOOR does not have (they belong there)
OUTDOOR_MORE = re.compile(r"\bbalc\b|\bterr\b|porch|\bdeck\b|courtyard|\byard\b|canopy|\bhof\b|\bcour\b|cortile")
KIND_MORE = [("balcony", re.compile(r"\bbalc\b")), ("terrace", re.compile(r"\bterr\b|porch|\bdeck\b")),
             ("garden", re.compile(r"courtyard|\byard\b|\bhof\b|\bcour\b|cortile"))]


def is_indoor(label: str | None) -> bool:
    return vocab.indoor(label) and not (label and OUTDOOR_MORE.search(vocab.plain(label)))


def room_kind(label: str | None) -> str | None:
    return vocab.room_type(label) or next((k for k, rx in KIND_MORE if label and rx.search(vocab.plain(label))), None)


# ------------------------------------------------------------------ texts
def text_kind(s: str) -> tuple[str, object]:
    """-> ('area', (m2, unit as printed, value as printed)) | ('num', id) | ('ident', id) | ('label', text) | ('other', s)"""
    s = s.strip()
    if m := AREA_FT.match(s):
        v = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        return "area", (round(v * SQFT, 3), "ft2", v)
    kind, val = vocab.classify(s)
    if kind == "area":
        return "area", (val, "m2", val)
    if kind == "num":
        return "num", s
    if kind == "tag":
        return "other", s
    if IDENT.match(s):
        return "ident", s                           # as printed, prefix included ("Rum nr. K01"): twins name it so
    if LABEL.match(s) and len(s) <= 40 and len(s.split()) <= 5 and sum(c.isalpha() for c in s) >= 2:
        return "label", s
    return "other", s


def text_items(words: list[dict]) -> list[dict]:
    """Words of one page -> printed lines (text, box, size, ids of the pdf_text rows), upright text only.  Words of
    one line are joined; a line that reads as nothing is split back into its words ("4.19 Korridor")."""
    lines = collections.defaultdict(list)
    for w in words:
        if (w["text"] or "").strip() and not (w.get("rot") or 0):
            size = w["size"] or (w["y1"] - w["y0"]) or 1.0
            key = w["line_no"] if w.get("line_no") is not None else ("y", round((w["y0"] + w["y1"]) / size))
            lines[key].append(w)
    out = []

    def emit(ws):
        t = " ".join(w["text"].strip() for w in ws)
        out.append(dict(text=t, x0=min(w["x0"] for w in ws), y0=min(w["y0"] for w in ws), x1=max(w["x1"] for w in ws),
                        y1=max(w["y1"] for w in ws), size=max((w["size"] or w["y1"] - w["y0"]) for w in ws),
                        ids=[w["id"] for w in ws], bold="bold" in (ws[0].get("font") or "").lower()))

    for ws in lines.values():
        ws.sort(key=lambda w: w["x0"])
        run = [ws[0]]
        runs = [run]
        for w in ws[1:]:
            size = w["size"] or (w["y1"] - w["y0"]) or 1.0
            if w["x0"] - run[-1]["x1"] > 1.5 * size:
                run = []
                runs.append(run)
            run.append(w)
        for run in runs:
            if 1 < len(run) <= 3 and text_kind(" ".join(w["text"].strip() for w in run))[0] == "other" and \
                    any(text_kind(w["text"])[0] in ("num", "area") for w in run):
                cur = []
                for w in run:                      # keep "12.3 m²" together, split the rest
                    if cur and text_kind(" ".join(x["text"].strip() for x in cur + [w]))[0] != "other":
                        cur.append(w)
                        continue
                    if cur:
                        emit(cur)
                    cur = [w]
                emit(cur)
            else:
                emit(run)
    for o in out:
        o["kind"], o["val"] = text_kind(o["text"])
        o["cx"], o["cy"], o["h"] = (o["x0"] + o["x1"]) / 2, (o["y0"] + o["y1"]) / 2, max(o["y1"] - o["y0"], 1e-3)
    return out


def room_tags(items: list[dict]) -> list[dict]:
    """Stacks of lines that read as a room tag.  -> [{label, number, area, lines, bbox, strong, x, y}]"""
    items = sorted(items, key=lambda o: (o["y0"], o["x0"]))
    ys = np.array([o["y0"] for o in items])
    below = {}
    used_as_below = set()
    for i, o in enumerate(items):
        if o["kind"] == "other":
            continue
        best = None
        for j in range(int(np.searchsorted(ys, o["y0"] + 0.4 * o["h"])), len(items)):
            q = items[j]
            gap = q["y0"] - o["y1"]
            if gap > 1.0 * o["h"]:
                break
            if q["kind"] == "other" or gap < -0.35 * o["h"] or q["h"] > 1.8 * o["h"] or o["h"] > 1.8 * q["h"]:
                continue
            if abs(q["cx"] - o["cx"]) <= 0.5 * max(q["x1"] - q["x0"], o["x1"] - o["x0"]) and \
                    (best is None or gap < best[0]) and j not in used_as_below:
                best = (gap, j)
        if best:
            below[i] = best[1]
            used_as_below.add(best[1])
    tags = []
    for i in range(len(items)):
        if i in used_as_below or items[i]["kind"] == "other":
            continue
        chain = [i]
        while chain[-1] in below and len(chain) < 6:
            chain.append(below[chain[-1]])
        lines = [items[j] for j in chain]
        kinds = [o["kind"] for o in lines]
        if len(lines) > 5 or kinds.count("area") > 1 or kinds.count("num") + kinds.count("ident") > 1 or \
                kinds.count("label") > 3:
            continue                                    # a note, a table column, a schedule
        labels = [o for o in lines if o["kind"] == "label"]
        num = next((o for o in lines if o["kind"] in ("num", "ident")), None)
        area = next((o for o in lines if o["kind"] == "area"), None)
        label = " ".join(o["text"].strip() for o in labels) or None
        known = vocab.room_type(label) is not None
        if area is not None and (labels or num):
            strong = True
        elif num is not None and (num["kind"] == "num" or (labels and len(lines) <= 3)):
            strong = False                              # could as well be a grid bubble, a level, a dimension
        elif labels and not num and known and len(labels) <= 2 and len(label) <= 30:
            strong = False
        else:
            continue
        bb = [min(o["x0"] for o in lines), min(o["y0"] for o in lines), max(o["x1"] for o in lines), max(o["y1"] for o in lines)]
        tags.append(dict(label=label, labels=labels, num=num, area=area, lines=lines, bbox=bb, strong=strong, known=known,
                         x=(bb[0] + bb[2]) / 2, y=(bb[1] + bb[3]) / 2,
                         number=num["val"].replace(",", ".") if num else None))
    return tags


# ------------------------------------------------------------------ (a) closed paths
def closed_outlines(paths: list[dict], geoms, page_area: float) -> list[tuple]:
    """(pen, Polygon, path id) for every closed path of a size a room can have on this page."""
    out = []
    for r, g in zip(paths, geoms):
        if g is None or g.geom_type not in ("Polygon", "LineString"):
            continue
        if g.geom_type == "LineString" and not r["closed"] and len(g.coords) < 5:
            continue
        pg = _ring(g, 2e-3)
        if pg is None or pg.area < 20 or pg.area > 0.6 * page_area:
            continue
        pen = (r["stroke"], round(r["width"] or 0, 2), r["dash"]) if r["stroke"] else ("fill", r["fill"], None)
        out.append((pen, pg, r["id"]))
    return out


def assign_outlines(tags: list[dict], outlines: list[tuple]) -> dict:
    """Give each tag the smallest closed path around it that holds no other tag (tags of rooms nested in it aside).
    -> {"pen": the commonest pen, "n": tags with an outline}"""
    for t in tags:
        t.update(poly=None, pen=None, path_id=None)
    if not outlines or not tags:
        return dict(pen=None, n=0)
    geoms = [o[1] for o in outlines]
    tree = STRtree(geoms)
    pts = [Point(t["x"], t["y"]) for t in tags]
    hit = tree.query(pts, predicate="within")
    inside = collections.defaultdict(list)          # outline -> tags
    around = collections.defaultdict(list)          # tag -> outlines
    for ti, pi in zip(*hit.tolist()):
        t = tags[ti]
        if geoms[pi].area >= 1.5 * (t["bbox"][2] - t["bbox"][0]) * (t["bbox"][3] - t["bbox"][1]):
            inside[pi].append(ti)
            around[ti].append(pi)
    single = {pi for pi, ts in inside.items() if len(ts) == 1}
    pens = collections.Counter()
    for ti, ps in around.items():
        own = [p for p in ps if p in single]
        if own:
            pens[outlines[min(own, key=lambda p: geoms[p].area)][0]] += 1
    if not pens:
        return dict(pen=None, n=0)
    pen = pens.most_common(1)[0][0]
    if pen[0] == "fill":            # fills of rooms differ by use; the family is "filled, not stroked"
        same = lambda q: q[0] == "fill"  # noqa: E731
    else:
        same = lambda q: q == pen  # noqa: E731
    # first the tags that are alone in their outline, smallest outlines first; then outlines whose other tags
    # already have a nested outline of their own
    order = sorted(around, key=lambda ti: min(geoms[p].area for p in around[ti]))
    for rnd in (0, 1):
        for ti in order:
            t = tags[ti]
            if t["poly"] is not None:
                continue
            cands = sorted(around[ti], key=lambda p: (not same(outlines[p][0]), geoms[p].area))
            for p in cands:
                others = [u for u in inside[p] if u != ti]
                if rnd == 0 and others:
                    continue
                if rnd == 1 and not all(tags[u]["poly"] is not None and tags[u]["poly"].area < geoms[p].area and
                                        geoms[p].buffer(0.5).contains(tags[u]["poly"]) for u in others):
                    continue
                if any(tags[u]["path_id"] == outlines[p][2] for u in others):
                    continue
                t.update(poly=geoms[p], pen=outlines[p][0], path_id=outlines[p][2], family=same(outlines[p][0]),
                         how="closed_path")
                break
    # a fill and its stroke are two paths of the same shape: prefer reporting the stroked one is not needed
    return dict(pen=pen, n=sum(t["poly"] is not None for t in tags))


# ------------------------------------------------------------------ (b) free space between linework
def _lum(c: str | None) -> float:
    if not c or len(c) != 7:
        return 1.0
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b


def _grey(c: str | None) -> bool:
    if not c or len(c) != 7:
        return False
    v = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
    return max(v) - min(v) <= 24


def free_space(tags: list[dict], todo: list[int], paths: list[dict], geoms, k: float, page: dict, variant: str):
    """Regions of open floor around the tags `todo`.  -> {tag index: (Polygon in points, area in points²)}

    variant: which paths are obstacles - 'poche' dark fills only; 'grey' dark fills and solid grey/black strokes;
    'all' dark fills and every solid stroke."""
    import cv2
    from scipy import ndimage as ndi
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed

    xs = [tags[i]["x"] for i in todo]
    ys = [tags[i]["y"] for i in todo]
    pad = max(15.0 / k, 0.25 * max(max(xs) - min(xs), max(ys) - min(ys)))          # 15 m around the tags
    x0, y0 = max(0.0, min(xs) - pad), max(0.0, min(ys) - pad)
    x1, y1 = min(page["width"], max(xs) + pad), min(page["height"], max(ys) + pad)
    s = k / CELL                                        # pixels per point
    s = min(s, 3600.0 / max(x1 - x0, y1 - y0, 1.0))
    W, H = int((x1 - x0) * s) + 2, int((y1 - y0) * s) + 2
    cell = k / s                                        # metres per pixel
    obst = np.zeros((H, W), np.uint8)
    view = box(x0, y0, x1, y1)

    def px(coords):
        a = np.asarray(coords, dtype=float)[:, :2]
        return np.round((a - (x0, y0)) * s).astype(np.int32)

    for r, g in zip(paths, geoms):
        if g is None or g.is_empty:
            continue
        b = g.bounds
        if b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1:
            continue
        if b[2] - b[0] > 0.9 * page["width"] or b[3] - b[1] > 0.9 * page["height"]:
            continue                                    # sheet border
        if r["fill"] and g.geom_type == "Polygon" and _lum(r["fill"]) < 0.55 and g.area < 0.25 * view.area:
            cv2.fillPoly(obst, [px(g.exterior.coords)], 1)
            continue
        if not r["stroke"] or r["dash"] or variant == "poche":
            continue
        if variant == "grey" and not _grey(r["stroke"]):
            continue
        if _lum(r["stroke"]) > 0.8:
            continue                                    # faint background linework
        line = g.exterior if g.geom_type == "Polygon" else g
        if line.geom_type != "LineString" and line.geom_type != "LinearRing":
            continue
        cv2.polylines(obst, [px(line.coords)], False, 1, max(1, int(round((r["width"] or 0) * s))))
    free = obst == 0
    dist = ndi.distance_transform_edt(free) * cell      # metres to the nearest linework
    markers = np.zeros((H, W), np.int32)
    markers[0, :] = markers[-1, :] = markers[:, 0] = markers[:, -1] = 1          # outside
    seed = {}
    for n, i in enumerate(todo):
        cx, cy = int((tags[i]["x"] - x0) * s), int((tags[i]["y"] - y0) * s)
        r = max(2, int(0.5 * (tags[i]["bbox"][3] - tags[i]["bbox"][1]) * s))
        patch = free[max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1]
        if not patch.any():
            continue
        m = markers[max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1]
        m[patch & (m == 0)] = n + 2
        seed[n + 2] = i
    n_tag = len(todo) + 2
    peaks = peak_local_max(dist, min_distance=max(3, int(0.5 / cell)), threshold_abs=0.35, exclude_border=False)
    nxt = n_tag
    for py, pxl in peaks:
        if markers[py, pxl] == 0:
            markers[py, pxl] = nxt
            nxt += 1
    lab = watershed(-dist, markers, mask=free)
    # saddles: the widest passage between two neighbouring basins
    peak = ndi.maximum(dist, lab, index=np.arange(nxt))
    pairs = {}
    for a, b, d in ((lab[:, :-1], lab[:, 1:], np.minimum(dist[:, :-1], dist[:, 1:])),
                    (lab[:-1, :], lab[1:, :], np.minimum(dist[:-1, :], dist[1:, :]))):
        m = (a != b) & (a > 0) & (b > 0)
        lo, hi, dv = np.minimum(a[m], b[m]).astype(np.int64), np.maximum(a[m], b[m]).astype(np.int64), d[m]
        key = lo * nxt + hi
        order = np.argsort(key)
        key, dv = key[order], dv[order]
        if len(key):
            starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
            for kk, v in zip(key[starts], np.maximum.reduceat(dv, starts)):
                pairs[int(kk)] = max(pairs.get(int(kk), 0.0), float(v))
    parent = list(range(nxt))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    named = lambda a: 2 <= a < n_tag  # noqa: E731
    for kk, sad in sorted(pairs.items(), key=lambda kv: -kv[1]):
        a, b = find(kk // nxt), find(kk % nxt)
        if a == b or (named(a) and named(b)) or (named(a) and b == 1) or (named(b) and a == 1):
            continue
        if sad <= DOOR_HALF and min(peak[a], peak[b]) > 1.25 * sad:
            continue                                    # an opening of door width between two wider spaces
        root, other = (a, b) if (named(a) or a == 1) else (b, a)
        parent[other] = root
        peak[root] = max(peak[a], peak[b])
    roots = np.array([find(a) for a in range(nxt)])
    lab = roots[lab]
    out = {}
    close = np.ones((2 * max(1, int(0.06 / cell)) + 1,) * 2, np.uint8)
    for a, i in seed.items():
        m = (lab == a)
        if not m.any() or m[0, :].any() or m[-1, :].any() or m[:, 0].any() or m[:, -1].any():
            continue
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, close)
        m = ndi.binary_fill_holes(m)
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea) if cs else None
        if c is None or len(c) < 4:
            continue
        pg = Polygon(c[:, 0, :] / s + (x0, y0)).buffer(0)
        if pg.geom_type == "MultiPolygon":
            pg = max(pg.geoms, key=lambda q: q.area)
        if pg.is_empty:
            continue
        out[i] = (pg.simplify(0.6 / s), float(m.sum()) / (s * s))
    return out


# ------------------------------------------------------------------ scale
def page_scale(db: DB, fid: str, page: dict) -> tuple[float | None, str, list[float]]:
    """(metres per point or None, how, every scale printed on the page as metres per point)"""
    frame = db.one("select m, method from jstore.frame where file_id = :f and page_no = :p", f=fid, p=page["page_no"])
    listed = sorted({PT_M * float(s_["ratio"]) for s_ in (page["meta"] or {}).get("scales", []) if s_.get("ratio")})
    if frame:
        m = frame["m"]
        return math.sqrt(abs(m[0] * m[4] - m[1] * m[3])), f"frame ({frame['method']})", listed
    t = trusted_scale((page["meta"] or {}).get("scales"))
    if t:
        return PT_M * float(t["ratio"]), f"scale text {t['text']}", listed
    return None, "none", listed


def scale_from_printed(pairs: list[tuple], listed: list[float]) -> tuple[float | None, str]:
    """pairs: (printed m2, outline area in points²).  The scale that makes them equal, when they agree on one."""
    ks = sorted(math.sqrt(p / a) for p, a in pairs if a > 0 and p > 0)
    if not ks:
        return None, "none"
    med = ks[len(ks) // 2]
    near = [x for x in listed if abs(x / med - 1) < 0.06]
    if near:
        return min(near, key=lambda x: abs(x / med - 1)), "scale text confirmed by printed areas"
    if len(ks) >= 3 and sum(abs(x / med - 1) < 0.03 for x in ks) >= 0.6 * len(ks):
        return med, "printed areas over outline areas"
    return None, "none"


# ------------------------------------------------------------------ (c) outlines read on the page render
IMAGE_MAX_ROOMS = 40              # tags outlined on the render, per page
IMAGE_M2 = (1.0, 120.0)           # the outline of a tag without a printed area outside this is not taken as a room
IMAGE_CONF = dict(agrees_with_printed=0.8, plain=0.6, flagged=0.45)


def image_budget() -> float:
    """Seconds the outlines on the page renders may take per file; 0 switches them off (JSTORE_PDF_ROOM_OUTLINE=0,
    or no Gemini key).  Never close to the pipeline's alarm for the drawing."""
    if os.environ.get("JSTORE_PDF_ROOM_OUTLINE", "1").strip().lower() in ("0", "off") or not os.environ.get("GEMINI_API_KEY"):
        return 0.0
    from .raster_rooms import _outline_budget
    return max(0.0, _outline_budget())


def image_todo(tags: list[dict], k: float | None) -> list[int]:
    """Tags without an outline that the render is asked about: every tag with a printed area; a name with a number
    or a known room name only on a page with a scale (their outline can then be checked for a plausible size)."""
    return [i for i, t in enumerate(tags) if t["poly"] is None and
            (t["strong"] or (k and t["label"] and (t["known"] or t["num"] is not None)))][:IMAGE_MAX_ROOMS]


def image_outlines(page: dict, tags: list[dict], info: dict, k: float | None, listed: list[float], budget_s: float,
                   outline=None) -> float | None:
    """The image path on a vector page: the page render is an image, so every tag without an outline is outlined on
    it as a scan's rooms are (room_outline: polygon from Gemini on a cut-out around the label, snapped to the drawn
    lines).  Polygons come back in page points.  Without a scale the printed areas choose one of the scales printed
    on the page (or agree on one); the outline of a tag whose printed area it contradicts is dropped.
    -> the scale (metres per point) after this step.  `outline` replaces room_outline.outline_rooms in tests."""
    todo = image_todo(tags, k)
    s = (page.get("to_image") or [0])[0]
    if not todo or not s or not page.get("image") or budget_s < 10:
        return k
    from PIL import Image
    from ..pipeline import store_dir
    from . import room_outline as RO
    path = store_dir() / page["image"]
    if outline is None and not path.exists():
        return k
    # the scale that sizes the cut-outs: the page's, else the largest plan scale printed on it, else a sheet of 40 m
    plan_scales = [x for x in listed if 20 <= x / PT_M <= SITE_PLAN]
    k_view = k or (min(plan_scales) if plan_scales else 40.0 / max(page["width"], page["height"]))
    asks = [dict(label=tags[i]["label"], number=tags[i]["number"], label_box=[v * s for v in tags[i]["bbox"]], hint=None)
            for i in todo]
    log: dict = {}
    if outline is None:
        Image.MAX_IMAGE_PIXELS = None
        res = RO.outline_rooms(Image.open(path), asks, k_view / s, budget_s, log)
    else:
        res = outline(asks, k_view / s)
    got = {}
    for i, o in zip(todo, res):
        pg = RO.shape(o["polygon"]) if o.get("polygon") is not None else None
        if pg is None or pg.area / (s * s) > 0.5 * page["width"] * page["height"]:
            tags[i]["rejected"] = "no outline on the page render: " + str(o.get("reason") or "polygon covers the sheet")
            continue
        got[i] = (Polygon([(x / s, y / s) for x, y in pg.exterior.coords]), o)
    kp, how = scale_from_printed([(tags[i]["area"]["val"][0], got[i][0].area) for i in got if tags[i]["area"]],
                                 listed if k is None else [])
    if kp and k is None:
        k, info["scale"] = kp, how.replace("outline areas", "outline areas on the render")
    elif kp and abs(kp / k - 1) > 0.06:   # a print that is not at its stated scale: the printed areas agree on another
        info["scale"] = f"printed areas over outline areas on the render; they contradict the {info.get('scale')} (a reduced print?)"
        info["scale_replaced"] = round(k, 6)
        k = kp
    kept = agree = 0
    for i, (pg, o) in got.items():
        t = tags[i]
        flagged = bool(o.get("others_inside") or o.get("touches_crop_border") or o.get("overlap", 0) > 0.05
                       or not o.get("label_inside", True) or o.get("support", 1) < 0.5)
        conf = IMAGE_CONF["flagged" if flagged else "plain"]
        if k:
            a = pg.area * k * k
            if t["area"]:
                ratio = a / t["area"]["val"][0]
                if not 0.6 <= ratio <= 1.6:
                    t["rejected"] = "outline on the page render of another size than printed"
                    continue
                if abs(ratio - 1) < 0.1:
                    conf, agree = IMAGE_CONF["agrees_with_printed"], agree + 1
                else:
                    conf = IMAGE_CONF["flagged"]
            elif not IMAGE_M2[0] <= a <= IMAGE_M2[1] or (not t["strong"] and flagged):
                t["rejected"] = "outline on the page render is not confirmed as this room"
                continue
        t.update(poly=pg, area_pt=pg.area, how="image_outline", family=False, conf=conf)
        t.pop("rejected", None)
        kept += 1
    info["image_outline"] = dict(asked=len(todo), polygons=len(got), kept=kept, within_10pct_of_printed=agree,
                                 calls=log.get("calls"), cached=log.get("cached"), seconds=log.get("seconds"),
                                 budget_ended=log.get("budget_ended"), errors=log.get("errors"))
    return k


# ------------------------------------------------------------------ one page
def read_page(db: DB, fid: str, page: dict, tags: list[dict]) -> dict:
    no = page["page_no"]
    paths = db.run("""select id, kind, stroke, fill, width, dash, closed, geom from jstore.pdf_path
                      where file_id = :f and page_no = :p and geom is not null order by seq""", f=fid, p=no)
    geoms = list(from_wkb([r["geom"] for r in paths])) if paths else []
    k, how, listed = page_scale(db, fid, page)
    info = dict(page=no, tags=len(tags), strong=sum(t["strong"] for t in tags), scale=how, paths=len(paths))
    fam = assign_outlines(tags, closed_outlines(paths, geoms, page["width"] * page["height"]))
    info.update(room_pen=str(fam["pen"]) if fam["pen"] else None, closed_path=fam["n"])
    pairs = [(t["area"]["val"][0], t["poly"].area) for t in tags if t["poly"] is not None and t["area"]]
    if pairs:
        kp, howp = scale_from_printed(pairs, listed)
        if k is None:
            k, info["scale"] = kp, howp
        elif kp and abs(kp / k - 1) > 0.06 and howp.startswith("printed"):
            k, info["scale"] = kp, f"{howp}; they contradict the {how} (a reduced print?)"
    if k and k / PT_M > SITE_PLAN:
        for t in tags:
            t["poly"] = None
        info.update(k=k, site_plan=True)
        return info
    if k:                                               # an outline that contradicts the printed area is not the room
        for t in tags:
            if t["poly"] is not None:
                a = t["poly"].area * k * k
                if (t["area"] and not 0.7 <= a / t["area"]["val"][0] <= 1.4) or not ROOM_M2[0] <= a <= ROOM_M2[1]:
                    t.update(poly=None, pen=None, path_id=None, rejected="closed path of another size than printed")
    # (b) free space, for strong tags and known room names without an outline
    cands = [k] if k else list(listed[:3])
    todo = [i for i, t in enumerate(tags) if t["poly"] is None and (t["strong"] or t["known"])]
    if todo and cands and any(tags[i]["strong"] for i in todo) and len(paths) >= 20 and time.time() < page["deadline"]:
        best = None
        for kc in cands:
            for variant in ("grey", "poche", "all"):
                try:
                    reg = free_space(tags, todo, paths, geoms, kc, page, variant)
                except Exception as e:  # noqa: BLE001 - a variant that fails is a variant that found nothing
                    info.setdefault("free_space_errors", []).append(f"{variant}: {e!r}"[:200])
                    continue
                # a print that is not at its stated scale shows as one common ratio of printed to measured areas
                kp, howp = scale_from_printed([(tags[i]["area"]["val"][0], reg[i][1]) for i in reg if tags[i]["area"]], [])
                if kp and abs(kp / kc - 1) > 0.06 and len(cands) < 4 and all(abs(kp / c - 1) > 0.03 for c in cands):
                    cands.append(kp)
                    info["scale_from_regions"] = round(kp, 5)
                ok = [i for i in reg if ROOM_M2[0] <= reg[i][1] * kc * kc <= ROOM_M2[1]]
                chk = [abs(reg[i][1] * kc * kc / tags[i]["area"]["val"][0] - 1) for i in ok if tags[i]["area"]]
                score = (sum(e < 0.1 for e in chk), len(ok) if not chk else 0, -float(np.mean(chk)) if chk else 0.0)
                if best is None or score > best[0]:
                    best = (score, kc, variant, reg, ok, chk)
                if time.time() > page["deadline"]:
                    break
            if time.time() > page["deadline"]:
                break
        if best and best[4]:
            _, kc, variant, reg, ok, chk = best
            share = sum(e < 0.1 for e in chk) / len(chk) if chk else None
            info["free_space"] = dict(obstacles=variant, regions=len(ok), checked_against_printed=len(chk),
                                      within_10pct=sum(e < 0.1 for e in chk),
                                      median_error=round(float(np.median(chk)), 3) if chk else None)
            if chk and share >= 0.5:      # regions that nothing on the page confirms are not used
                if k is None or abs(kc / k - 1) > 0.03:
                    info["scale"] = ("printed areas over free-space regions" + (f"; they contradict the {how} (a reduced print?)" if k else "")
                                     if kc not in listed else "scale text chosen by printed areas over free-space regions")
                    for t in tags:
                        if k and t["poly"] is not None:
                            t.update(poly=None, rejected="closed path measured with a scale the printed areas contradict")
                    k = kc
                conf = round(0.3 + 0.4 * share, 2)
                for i in ok:
                    t = tags[i]
                    a = reg[i][1] * kc * kc
                    if t["area"] and not 0.6 <= a / t["area"]["val"][0] <= 1.6:
                        t["rejected"] = "free-space region of another size than printed"
                        continue
                    t.update(poly=reg[i][0], area_pt=reg[i][1], how="free_space", family=False, conf=conf)
            else:
                info["free_space"]["used"] = False
    info["k"] = k
    return info


# ------------------------------------------------------------------ the file
def interpret(db: DB, f: dict) -> dict:
    fid = f["file_id"]
    t0 = time.time()

    def nothing(reason: str, **spec) -> dict:
        define(db, fid, "room", "reader", dict(source="pdf_rooms", created=0, reason=reason, **spec), 0.0, BY)
        return dict(new=0, matched=0, unlinked=0, skipped=reason)

    have = db.run("""select distinct sf.format, sf.file_id from jstore.entity e
                     join jstore.entity_source s using (entity_id) join jstore.source_file sf on sf.file_id = s.file_id
                     where e.drawing_key = :d and e.kind = 'room' and e.status = 'active'""", d=f["drawing_key"])
    if have:
        if any(h["file_id"] == fid for h in have):
            return dict(new=0, matched=0, unlinked=0, skipped="rooms of this file were read by plan.interpret_pdf")
        return nothing("the drawing already has rooms from " + ", ".join(sorted({h["format"] for h in have})) +
                       "; a second reading of the same rooms would count them twice")
    if db.one("select exists (select from jstore.entity where drawing_key = :d and kind = 'room') as x",
              d=f["drawing_key"])["x"]:
        return nothing("the drawing already has rooms")
    pages = db.run("""select page_no, width, height, unit, meta, image, to_image from jstore.page
                      where file_id = :f order by page_no""", f=fid)
    words = db.run("""select id, page_no, text, x0, y0, x1, y1, font, size, line_no, rot from jstore.pdf_text
                      where file_id = :f order by page_no, seq""", f=fid)
    if not words:
        return nothing("the file has no text layer; room names and numbers cannot be read from it")
    by_page = collections.defaultdict(list)
    for w in words:
        by_page[w["page_no"]].append(w)
    recs, infos, skipped = [], [], []
    img_budget, t_img = image_budget(), time.time()
    for page in pages[:MAX_PAGES]:
        no = page["page_no"]
        tags = room_tags(text_items(by_page.get(no, [])))
        if not tags or not page["width"] or not page["height"]:
            continue
        if time.time() - t0 > TIME_BUDGET:
            skipped.append(no)
            continue
        page["deadline"] = t0 + FREE_SPACE_BUDGET
        info = read_page(db, fid, page, tags)
        infos.append(info)
        k = info["k"]
        if not info.get("site_plan") and img_budget - (time.time() - t_img) >= 10:
            t1 = time.time()
            k = info["k"] = image_outlines(page, tags, info, k, page_scale(db, fid, page)[2],
                                           img_budget - (time.time() - t_img))
            t0 += time.time() - t1                       # the text and vector budget does not pay for the model
        frame = db.one("select m from jstore.frame where file_id = :f and page_no = :p", f=fid, p=no)
        if frame and k and abs(math.sqrt(abs(frame["m"][0] * frame["m"][4] - frame["m"][1] * frame["m"][3])) / k - 1) < 0.02:
            m, placed = list(frame["m"]), "frame"
        elif k:
            m, placed = [k, 0, 0, 0, -k, k * page["height"]], "scale only: metres from the bottom-left of the page"
        else:
            m, placed = None, None
        if info.get("site_plan"):
            info["rooms"] = 0
            continue
        if placed != "frame" and m and any(t.get("how") == "image_outline" for t in tags):
            # record how page points map to metres, so the outlines read on the render can be placed on the page again
            # (a frame that only restated a scale text the printed areas contradict is replaced)
            db.run("""insert into jstore.frame (file_id, page_no, m, method) values (:f, :p, cast(:m as double precision[]),
                      'scale_text') on conflict (file_id, page_no) do update set m = excluded.m
                      where jstore.frame.method = 'scale_text'""",
                   f=fid, p=no, m="{" + ",".join(repr(float(v)) for v in m) + "}")
            info["frame_written"] = True
        company = sum(t["poly"] is not None and (t["strong"] or t["num"] is not None) for t in tags)
        kept = 0
        for t in tags:
            # weak tags need an outline drawn with the room pen; a lone name also needs company on the page
            if not t["strong"] and (t["poly"] is None or (t.get("how") == "closed_path" and not t.get("family"))
                                    or (t["num"] is None and not company)):
                continue
            src, claims = [], []
            attrs = dict(indoor=is_indoor(t["label"]), reader="pdf_rooms", page_no=no)

            def tsrc(o, role):
                src.append(("pdf_text", str(o["ids"][0]), role, [o["x0"], o["y0"], o["x1"], o["y1"]], None))
                return len(src) - 1

            if t["poly"] is not None:
                pg = t["poly"]
                if t["how"] == "closed_path":
                    src.append(("pdf_path", str(t["path_id"]), "outline", list(pg.bounds), None))
                    conf = 0.85 if t.get("family") else 0.6
                    area_pt = pg.area
                    for u in tags:          # a room drawn inside this one is not part of it
                        if u is not t and u["poly"] is not None and u["poly"].area < pg.area and \
                                pg.buffer(0.5).contains(u["poly"]):
                            area_pt -= u["poly"].area
                            attrs.setdefault("encloses", []).append(u["number"] or u["label"])
                else:
                    src.append(("region", None, "outline", list(pg.bounds), None))
                    conf, area_pt = t["conf"], t["area_pt"]
                attrs["outline"] = t["how"]
                if k:
                    claims.append(("net_area_m2", round(area_pt * k * k, 3), None, "m2", "measured_outline", 0, conf))
                    if t["how"] == "image_outline":
                        claims.append(("perimeter_m", round(pg.length * k, 2), None, "m", "measured_outline", 0, conf))
            elif t.get("rejected"):
                attrs["outline_rejected"] = t["rejected"]
            if t["num"]:
                tsrc(t["num"], "number")
            for o in t["labels"]:
                tsrc(o, "label")
            if t["area"]:
                m2, unit, raw = t["area"]["val"]
                si = tsrc(t["area"], "area_text")
                claims.append(("net_area_m2", m2, None, "m2", "printed", si, 0.98 if unit == "m2" else 0.95))
                if unit != "m2":
                    attrs["printed_area"] = dict(value=raw, unit=unit, text=t["area"]["text"])
            g = None
            if t["poly"] is not None and m:
                g = affine_transform(t["poly"], [m[0], m[1], m[3], m[4], m[2], m[5]])
                attrs["placed"] = placed
            elif t["poly"] is not None:
                attrs.update(geom_units="pt", outline_pt=t["poly"].wkt if len(t["poly"].exterior.coords) < 400
                             else t["poly"].simplify(1.0).wkt,
                             placed="no scale on this page: the outline is kept in page points, not in metres")
            recs.append(dict(kind="room", ifc_class="IfcSpace", number=t["number"], label=t["label"],
                             label_norm=room_kind(t["label"]), geom=g, attrs=attrs, sources=src, claims=claims,
                             page_no=no, _key=(t["number"], vocab.plain(t["label"] or ""),
                                               t["area"]["val"][0] if t["area"] else None)))
            kept += 1
        info["rooms"] = kept
    # the same room shown on another sheet of the set (plan, ceiling plan, enlarged plan) is one room
    first = {}
    for i, r in enumerate(recs):
        num, lab, area = r.pop("_key")
        key = ("n", num) if num else (("l", lab, area) if area is not None else None)
        if key is None:
            continue
        j = first.setdefault(key, i)
        if j != i and recs[j]["page_no"] != r["page_no"]:
            r["duplicate"] = j
    n_pages = len({r["page_no"] for r in recs})
    spec = dict(source="pdf_rooms", created=len(recs), pages=infos, pages_skipped_for_time=skipped,
                seconds=round(time.time() - t0, 1),
                rule="room tag = stacked text lines (name, identifier, printed area in m2 or square feet); outline = "
                     "the smallest closed path around the tag that holds no other tag, else the free space around "
                     "the tag between linework, cut at openings of door width, else a polygon read on the page render "
                     "(image_outline: Gemini on a cut-out around the tag, snapped to drawn lines, checked against "
                     "the printed area); the same number (or name and printed "
                     "area) on a later page is a duplicate")
    if not recs:
        why = ("no page has a room tag (a room name with a number or printed area, or a number like 2.01)"
               if not infos else "room tags were found but none could be confirmed: " +
               "; ".join(f"page {i['page']}: {i['tags']} tags, {i['strong']} strong, {i['closed_path']} in closed paths"
                         for i in infos[:6]))
        return nothing(why, pages=infos, pages_skipped_for_time=skipped)
    with_outline = sum("outline" in r["attrs"] for r in recs)
    conf = round(0.4 + 0.5 * with_outline / len(recs), 2)
    d = define(db, fid, "room", "reader", dict(spec, with_outline=with_outline, pages_with_rooms=n_pages), conf, BY)
    for r in recs:
        r["sources"] = [(a, b, c, e, d) for a, b, c, e, _ in r["sources"]]
    return merge(db, f, recs, registered=False)
