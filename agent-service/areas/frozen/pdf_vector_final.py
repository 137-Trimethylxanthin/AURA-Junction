"""pdf-vector: read rooms straight from a vector PDF (pdfplumber + pypdfium2, MIT/BSD).

Pipeline (nothing renderer-specific is hard-coded; styles are chosen at run time):
  text   - spans with positions; room number = token like 2.10 / U1.01; label = line just above it;
           printed area = "12.3 m²" line just below it; apartment tag = "Whg. A" / "App. B" / "Appartamento C" ...
  rooms  - filled vector paths -> shapely polygons (beziers flattened, sub-paths combined even-odd).  The stroke
           style (colour, width) whose polygons contain the most room numbers is the "room outline" style; each
           number is assigned the smallest polygon of that style containing it.
  scale  - "1:100" text in the title block (Massstab/Echelle/Scala/Scale), cross-checked with the drawn scale bar;
           page units are PostScript points (1/72 in), so 1 pt = 25.4/72 mm on paper * scale.
  units  - dashed (dash-dot) closed paths = apartment outlines; the tag inside names it; rooms join by containment;
           untagged dashed rings inside an outline are holes (stair/lift cores).
  indoor - unlabelled rooms take the category of their fill colour: a fill whose labelled rooms are mostly outdoor
           marks them outdoor; a fill with no labelled member is outdoor if it lies outside the building footprint
           (holes-filled union of the wall mass, wall style found at run time, and the other rooms).
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
import os
import re
import sys

import pdfplumber
import pypdfium2 as pdfium
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from areas import common  # noqa: E402

NUM = re.compile(r"^[A-Z]{0,2}\d{1,3}(?:[.,]\d{1,3})+$")
AREA = re.compile(r"^(\d+(?:[.,']\d+)*)\s*m(?:²|2|\^2)$")
TAG = re.compile(r"^(?:whg|wohnung|app|appt|apt|appartement|appartamento|apartment|unit|logement|alloggio)\.?\s*"
                 r"([A-Z]{1,2})$", re.I)
SCALE = re.compile(r"\b1\s*:\s*(\d{1,4})\b")
PT = 0.0254 / 72  # metres of paper per point


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def __or__(self, other: "Rect") -> "Rect":
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))


def render_page(path, page_idx: int = 0, dpi: float = 150.0):
    """Render a PDF page to an RGB PIL.Image using pypdfium2."""
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[page_idx].render(scale=dpi / 72.0).to_pil().convert("RGB")
    finally:
        doc.close()


def _norm_color(c) -> tuple[float, float, float] | None:
    """Normalise PDF grayscale / RGB / CMYK stroke or fill colour to RGB floats in [0..1]."""
    if c is None or isinstance(c, bool):
        return None
    if isinstance(c, (int, float)):
        v = float(c)
        if v > 1.0:
            v /= 255.0
        v = min(1.0, max(0.0, v))
        return (v, v, v)
    if isinstance(c, (tuple, list)):
        if not c:
            return None
        nums = [float(x) for x in c if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if len(nums) in (1, 3, 4):
            scale = 255.0 if any(x > 1.0 for x in nums) else 1.0
            vals = [min(1.0, max(0.0, x / scale)) for x in nums]
            if len(vals) == 1:
                return (vals[0], vals[0], vals[0])
            if len(vals) == 3:
                return (vals[0], vals[1], vals[2])
            cc, mm, yy, kk = vals
            return ((1.0 - cc) * (1.0 - kk), (1.0 - mm) * (1.0 - kk), (1.0 - yy) * (1.0 - kk))
        return (0.5, 0.5, 0.5)
    return (0.5, 0.5, 0.5)


def _is_dashed(dash) -> bool:
    if not dash or dash == "[] 0":
        return False
    if isinstance(dash, (tuple, list)):
        if len(dash) == 2 and isinstance(dash[0], (tuple, list)):
            return any(isinstance(x, (int, float)) and x > 0 for x in dash[0])
        return any(isinstance(x, (int, float)) and x > 0 for x in dash)
    if isinstance(dash, str):
        return dash.strip() not in ("", "[] 0")
    return False


def _to_drawing(obj: dict, dx: float = 0.0, dy: float = 0.0) -> dict:
    """Convert a pdfplumber vector object (curve/rect/line) into normalised drawing metadata
    in CropBox-relative top-down points (matching pypdfium2's rendered page origin)."""
    raw_path = obj.get("path") or []
    raw_pts = obj.get("pts") or []
    if dx or dy:
        path = [(it[0], *((float(p[0]) - dx, float(p[1]) - dy) for p in it[1:])) for it in raw_path if it]
        pts = [(float(p[0]) - dx, float(p[1]) - dy) for p in raw_pts]
    else:
        path = raw_path
        pts = raw_pts
    if not path and not pts and all(k in obj for k in ("x0", "top", "x1", "bottom")):
        x0, y0 = float(obj["x0"]) - dx, float(obj["top"]) - dy
        x1, y1 = float(obj["x1"]) - dx, float(obj["bottom"]) - dy
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {
        "fill": _norm_color(obj.get("non_stroking_color")) if obj.get("fill") else None,
        "color": _norm_color(obj.get("stroking_color")) if obj.get("stroke") else None,
        "width": float(obj.get("linewidth") or 0.0),
        "dashes": "dashed" if _is_dashed(obj.get("dash")) else None,
        "path": path,
        "pts": pts,
    }


def bez(p0, p1, p2, p3, n=8):
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    return [((1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3 * x3,
             (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3 * y3)
            for t in (i / n for i in range(1, n + 1))]


def rings(d: dict) -> list[list[tuple[float, float]]]:
    """Extract sub-path point rings from LTCurve.original_path (`path`), falling back to `pts`.
    A new ring starts on every `'m'` move (preserving inner holes for even-odd symmetric_difference)."""
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    start: tuple[float, float] | None = None
    for op_item in d.get("path") or []:
        if not op_item:
            continue
        op = op_item[0]
        args = op_item[1:]
        if op == "m" and len(args) >= 1:
            if len(cur) >= 3:
                out.append(cur)
            p = (float(args[0][0]), float(args[0][1]))
            cur = [p]
            start = p
        elif op == "l" and len(args) >= 1:
            p = (float(args[0][0]), float(args[0][1]))
            if not cur:
                cur = [p]
                start = p
            else:
                cur.append(p)
        elif op == "c" and len(args) >= 3 and cur:
            p1 = (float(args[0][0]), float(args[0][1]))
            p2 = (float(args[1][0]), float(args[1][1]))
            p3 = (float(args[2][0]), float(args[2][1]))
            cur += bez(cur[-1], p1, p2, p3)
        elif op == "v" and len(args) >= 2 and cur:
            p2 = (float(args[0][0]), float(args[0][1]))
            p3 = (float(args[1][0]), float(args[1][1]))
            cur += bez(cur[-1], cur[-1], p2, p3)
        elif op == "y" and len(args) >= 2 and cur:
            p1 = (float(args[0][0]), float(args[0][1]))
            p3 = (float(args[1][0]), float(args[1][1]))
            cur += bez(cur[-1], p1, p3, p3)
        elif op == "h":
            if cur and start is not None and cur[-1] != start:
                cur.append(start)
            if len(cur) >= 3:
                out.append(cur)
            cur = []
            start = None
    if len(cur) >= 3:
        out.append(cur)
    if not out:
        pts = d.get("pts") or []
        if len(pts) >= 3:
            out.append([(float(p[0]), float(p[1])) for p in pts])
    return out


def to_poly(rs):
    g = None
    for r in rs:
        p = Polygon(r)
        if not p.is_valid:
            p = make_valid(p)
        g = p if g is None else g.symmetric_difference(p)  # even-odd: holes and multi-part both work
    return g


def lines(page, dx: float = 0.0, dy: float = 0.0):
    """text items: (text, bbox, size), one per span, in CropBox-relative top-down points."""
    out = []
    words = page.extract_words(
        keep_blank_chars=True,
        x_tolerance_ratio=0.35,
        y_tolerance=1.5,
        extra_attrs=["size", "fontname"],
        return_chars=True,
    )
    for w in words:
        raw_cs = list(w.get("chars") or [])
        while raw_cs and not str(raw_cs[0].get("text", "")).strip():
            raw_cs.pop(0)
        while raw_cs and not str(raw_cs[-1].get("text", "")).strip():
            raw_cs.pop()
        size_hint = float(w.get("size") or (float(w["bottom"]) - float(w["top"])))
        if raw_cs:
            ch = []
            parts = []
            for idx, c in enumerate(raw_cs):
                ct = str(c.get("text", ""))
                cb = Rect(float(c["x0"]) - dx, float(c["top"]) - dy, float(c["x1"]) - dx, float(c["bottom"]) - dy)
                if idx > 0 and ct.strip() and parts[-1].strip():
                    prev_cb = ch[-1]["bbox"]
                    if cb.x0 - prev_cb.x1 > 0.18 * size_hint and not (parts[-1][-1].isdigit() and ct[0].isdigit()):
                        sp_bb = Rect(prev_cb.x1, min(prev_cb.y0, cb.y0), cb.x0, max(prev_cb.y1, cb.y1))
                        parts.append(" ")
                        ch.append(dict(c=" ", bbox=sp_bb))
                parts.append(ct)
                ch.append(dict(c=ct, bbox=cb))
            t = "".join(parts).strip()
            if not t:
                continue
            bb = Rect(
                min(c["bbox"].x0 for c in ch),
                min(c["bbox"].y0 for c in ch),
                max(c["bbox"].x1 for c in ch),
                max(c["bbox"].y1 for c in ch),
            )
        else:
            t = str(w.get("text", "")).strip()
            if not t:
                continue
            bb = Rect(float(w["x0"]) - dx, float(w["top"]) - dy, float(w["x1"]) - dx, float(w["bottom"]) - dy)
            ch = []
        size = float(w.get("size") or bb.height)
        out.append(dict(text=t, bbox=bb, size=size, chars=ch))

    # Join Td/TJ-kerned words on the same baseline (e.g. "Whg." + "A", "20.5" + "m2", "Salle" + "de" + "bains")
    # when PDF streams omit literal space glyphs, without merging room numbers or scale-bar integer ticks.
    if len(out) >= 2:
        out.sort(key=lambda item: (round(item["bbox"].y0, 1), item["bbox"].x0))
        merged = [out[0]]
        for cur in out[1:]:
            prev = merged[-1]
            same_line = (
                abs(cur["bbox"].y0 - prev["bbox"].y0) <= 0.25 * max(prev["size"], cur["size"])
                and abs(cur["size"] - prev["size"]) <= 0.25 * max(prev["size"], cur["size"])
            )
            gap = cur["bbox"].x0 - prev["bbox"].x1
            if same_line and 0.0 <= gap <= 0.75 * max(prev["size"], cur["size"]):
                pt, ct = prev["text"], cur["text"]
                cand = f"{pt} {ct}"
                is_area_tag_scale = bool(
                    AREA.match(cand.replace("\xa0", " "))
                    or TAG.match(cand)
                    or re.match(r"^(?:(?:massstab|maßstab|echelle|échelle|scala|scale)[:\s]*)?1\s*:\s*\d{1,4}$", cand, re.I)
                )
                both_alpha = bool(
                    re.search(r"[A-Za-zÀ-ÿ]", pt)
                    and re.search(r"[A-Za-zÀ-ÿ]", ct)
                    and not NUM.match(pt)
                    and not NUM.match(ct)
                )
                if is_area_tag_scale or both_alpha:
                    sp = dict(c=" ", bbox=Rect(prev["bbox"].x1, prev["bbox"].y0, cur["bbox"].x0, cur["bbox"].y1))
                    merged[-1] = dict(
                        text=cand,
                        bbox=prev["bbox"] | cur["bbox"],
                        size=prev["size"],
                        chars=(prev["chars"] + [sp] + cur["chars"]) if (prev["chars"] and cur["chars"]) else [],
                    )
                    continue
            merged.append(cur)
        out = merged

    # overlapping room numbers come out as one span ("2.22.23" = 2.22 + 2.23 sharing a glyph): split them using the
    # drawing's dominant number format, allowing overlapping matches
    fmts = collections.Counter(re.sub(r"\d", "9", o["text"]) for o in out if NUM.match(o["text"]))
    if fmts:
        dom = re.escape(fmts.most_common(1)[0][0]).replace("9", r"\d")
        pat = re.compile("(?=(" + dom + "))")
        for o in list(out):
            t = o["text"]
            if re.fullmatch(dom, t) or not re.fullmatch(r"[\dA-Z.,]+", t):
                continue
            ms = [(m.start(), m.group(1)) for m in pat.finditer(t)]
            if len(ms) >= 2 and ms[0][0] == 0 and ms[-1][0] + len(ms[-1][1]) == len(t) and len(o["chars"]) == len(t):
                out.remove(o)
                for st, txt in ms:
                    cs = o["chars"][st:st + len(txt)]
                    bb = cs[0]["bbox"] | cs[-1]["bbox"]
                    out.append(dict(text=txt, bbox=Rect(bb.x0, o["bbox"].y0, bb.x1, o["bbox"].y1),
                                    size=o["size"], chars=cs))
    return out


def neighbour(lines_, ln, above=True):
    """the text line stacked directly above/below ln (horizontally centred on it, within ~1 line height)."""
    cx, h = (ln["bbox"].x0 + ln["bbox"].x1) / 2, ln["bbox"].height
    best = None
    for o in lines_:
        if o is ln:
            continue
        ox = (o["bbox"].x0 + o["bbox"].x1) / 2
        gap = ln["bbox"].y0 - o["bbox"].y1 if above else o["bbox"].y0 - ln["bbox"].y1
        if -0.5 * h <= gap <= 0.8 * h and abs(ox - cx) <= max(o["bbox"].width, ln["bbox"].width) / 2:
            if best is None or gap < best[0]:
                best = (gap, o)
    return best[1] if best else None


def scale_of(page, ls):
    vals = [int(m.group(1)) for l in ls for m in SCALE.finditer(l["text"])]
    vals = [v for v in vals if v in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000)] or vals
    return collections.Counter(vals).most_common(1)[0][0] if vals else None


def scalebar_m_per_pt(ls):
    """scale bar: numeric tick labels on one baseline followed by 'm' -> metres per point from label spacing."""
    ticks = [l for l in ls if re.fullmatch(r"\d{1,3}", l["text"])]
    rows = collections.defaultdict(list)
    for t in ticks:
        rows[round(t["bbox"].y1)].append(t)
    best = None
    for row in rows.values():
        if len(row) < 3:
            continue
        xs = [((t["bbox"].x0 + t["bbox"].x1) / 2, int(t["text"])) for t in row]
        xs.sort()
        vals = [v for _, v in xs]
        if vals != sorted(vals) or vals[0] != 0:
            continue
        k = (xs[-1][0] - xs[0][0]) / (xs[-1][1] - xs[0][1])
        if best is None or len(row) > best[0]:
            best = (len(row), 1 / k)
    return best[1] if best else None


def read(path, page_idx: int = 0):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_idx]
        cb = getattr(page, "cropbox", None) or getattr(page, "mediabox", None) or (0.0, 0.0, 0.0, 0.0)
        dx, dy = float(cb[0]), float(cb[1])
        ls = lines(page, dx=dx, dy=dy)
        scale = scale_of(page, ls)
        raw_objs = [*page.rects, *page.curves, *page.lines]
    drawings = [_to_drawing(o, dx=dx, dy=dy) for o in raw_objs]

    mpp = PT * scale if scale else None
    bar = scalebar_m_per_pt(ls)
    if mpp is None or (bar and abs(bar / mpp - 1) > 0.05):
        mpp = bar or mpp  # title block missing or disagreeing with the drawn bar: trust the bar
    # --- text
    nums = [l for l in ls if NUM.match(l["text"])]
    tags = []
    for l in ls:
        m = TAG.match(l["text"])
        if m:
            tags.append((m.group(1).upper(), l))
    # --- filled polygons, grouped by stroke style
    polys = []
    for d in drawings:
        if d.get("fill") is None:
            continue
        rs = rings(d)
        if not rs:
            continue
        g = to_poly(rs)
        if g is None or g.is_empty or g.area <= 0:
            continue
        if d.get("color") is None:
            continue  # outlines are stroked; fill-only paths are hatch/pattern tiles (unclipped) or backgrounds
        style = (tuple(round(c, 2) for c in d["color"]), round(d.get("width") or 0, 2))
        polys.append((style, g, tuple(round(c, 2) for c in d["fill"])))
    cen = [Point((n["bbox"].x0 + n["bbox"].x1) / 2, (n["bbox"].y0 + n["bbox"].y1) / 2) for n in nums]
    hits = collections.Counter()
    for style, g, _ in polys:
        # a room outline contains a single number; boxes around text or whole-building fills don't qualify
        k = sum(g.contains(c) for c in cen)
        if k == 1:
            hits[style] += 1
    room_style = hits.most_common(1)[0][0] if hits else None
    cand = [(g, f) for s, g, f in polys if s == room_style]
    # --- apartments from dashed closed paths
    apts, holes = [], []
    for d in drawings:
        if d.get("dashes") and d["dashes"] not in ("[] 0", "") and d.get("fill") is None:
            rs = [r for r in rings(d) if len(r) >= 4]
            p = to_poly(rs) if rs else None  # rings of one path combined even-odd
            if p is None or p.is_empty or p.area <= 0:
                continue
            letters = [t for t, l in tags if p.contains(Point((l["bbox"].x0 + l["bbox"].x1) / 2,
                                                              (l["bbox"].y0 + l["bbox"].y1) / 2))]
            (apts if letters else holes).append([letters[0] if letters else None, p])
    # an untagged dashed ring lying inside an apartment outline is a hole of it (stair/lift core drawn separately)
    for _, h in holes:
        outer = [a for a in apts if a[1].contains(h.representative_point()) and a[1].area > h.area]
        if outer:
            a = min(outer, key=lambda a: a[1].area)
            a[1] = a[1].difference(h)
    rooms = []
    geoms = []
    for n, c in zip(nums, cen):
        inside = [(g.area, i) for i, (g, f) in enumerate(cand) if g.contains(c)]
        g, fill = cand[min(inside)[1]] if inside else (None, None)
        lab = neighbour(ls, n, above=True)
        label = lab["text"] if lab and not TAG.match(lab["text"]) and not NUM.match(lab["text"]) else None
        below = neighbour(ls, n, above=False)
        printed = None
        if below:
            m = AREA.match(below["text"].replace("\xa0", " "))
            if m:
                printed = float(m.group(1).replace("'", "").replace(",", "."))
        measured = g.area * mpp * mpp if (g is not None and mpp) else None
        area = printed if printed is not None else measured
        unit = None
        rp = g.representative_point() if g is not None else c
        ins = [(a.area, t) for t, a in apts if a.contains(rp)]
        if ins:
            unit = min(ins)[1]
        geoms.append(g)
        bbox = [round(v, 3) for v in g.bounds] if g is not None else None
        rooms.append(dict(number=n["text"].replace(",", "."), label=label, area=area, unit=unit,
                          printed=printed is not None, measured=measured, fill=fill, bbox=bbox))
    # fill colour encodes the room category: a fill whose labelled rooms are (mostly) outdoor marks the unlabelled
    # rooms drawn with it as outdoor too (e.g. a balcony too small to carry its label)
    votes = collections.defaultdict(list)
    for r in rooms:
        if r["label"] and r["fill"] is not None:
            votes[r["fill"]].append(not common.indoor(r))
    # fill clusters with no labelled member: decide geometrically - outdoor spaces (balconies, loggias) lie outside the
    # building footprint = everything enclosed by the wall mass.  The wall style is found at run time as the filled,
    # stroked, non-room style covering most of the thin band along the room outlines.
    unknown = {r["fill"] for r in rooms if r["fill"] is not None and r["fill"] not in votes}
    if unknown and mpp:
        rooms_u = unary_union([g for g in geoms if g is not None])
        band = rooms_u.buffer(0.15 / mpp).difference(rooms_u.buffer(-0.05 / mpp))
        # wall candidates: closed stroked shapes, filled or not (walls are often a hatch pattern + stroke-only outline)
        shapes = []
        for d in drawings:
            if d.get("color") is None or d.get("dashes") not in (None, "", "[] 0"):
                continue  # dashed = apartment outlines, not walls
            rs = [r for r in rings(d) if len(r) >= 3]
            g = to_poly(rs) if rs else None
            if g is None or g.is_empty or g.area <= 0 or g.area > rooms_u.area:  # page frame, title block
                continue
            st = (tuple(round(c, 2) for c in d["color"]), round(d.get("width") or 0, 2), d.get("fill") is not None)
            shapes.append((st, g))
        score = collections.Counter()
        for st, g in shapes:
            if st[:2] != room_style:
                score[st] += g.intersection(band).area
        if score:
            wall_style = score.most_common(1)[0][0]
            walls = [g for st, g in shapes if st == wall_style]
            for f in unknown:
                gs = [g for r, g in zip(rooms, geoms) if r["fill"] == f and g is not None]
                others = unary_union(walls + [g.buffer(0.05 / mpp) for r, g in zip(rooms, geoms)
                                              if g is not None and r["fill"] != f])
                parts = getattr(others, "geoms", [others])
                foot = unary_union([Polygon(p.exterior) for p in parts if p.geom_type == "Polygon"])
                inside = sum(g.intersection(foot).area for g in gs) / max(1e-9, sum(g.area for g in gs))
                votes[f] = [inside < 0.5]
    for r in rooms:
        v = votes.get(r["fill"])
        if r["label"] is None and v and sum(v) > len(v) / 2:
            r["label"] = "(outdoor by fill)"
    return dict(rooms=rooms, scale=scale, bar=bar, mpp=mpp, room_style=room_style, n_apts=len(apts))


def solve(path, queries):
    info = read(path)
    res = common.answer_all(info["rooms"], queries)
    return [dict(r, evidence=dict(scale=info["scale"], style=str(info["room_style"]))) if r else None for r in res]
