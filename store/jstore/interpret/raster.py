"""Layer 2 from a raster plan (PNG, JPG): rooms and apartments read from the pixels, added to the drawing's entities.

    interpret(db, file)   reads the image at JSTORE_DATASET_DIR / source_file.path

The reader (read_rooms) is the raster room reader developed for the area questions, kept whole:
  1. text    RapidOCR on the page -> room numbers, names, printed areas, apartment tags, scale, paper size.
  2. walls   the dominant unsaturated mid-grey (wall fill) closed over its hatch, plus solid black bands.
  3. faces   connected free space, grown over the line pixels -> a planar partition with borders on line centres.
  4. borders get a line strength; the weakest cluster (thin room outlines) separates rooms, stronger lines
             (fixtures, door swings, text) are merged across, never joining two numbered faces.
  5. area    region pixels * (m/px)^2, m/px from "1:N" and the paper size over the image size (DPI as fallback).
  6. apartments: the saturated dash-dot boundary, gaps closed; the tag inside names it.
Its constants (wall grey, line strengths) are tuned on rendered CAD plans; on photographs or scans of other
styles it returns few or no rooms, which is then recorded in the definition row.

What is written: per room an entity_source of kind 'region' (bbox in pixels), the area measured from the pixels
(method measured_outline) and the printed area as read by OCR (method ocr).  Rooms are matched to the entities of
the CAD or PDF twin by room number; rooms whose number was not read are stored only when no other source exists.
"""
from __future__ import annotations

import collections
import os
import re
import unicodedata

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from ..db import DB
from . import define, merge
from . import vocab as lexicon

PAPER_MM = {"A0": 1189, "A1": 841, "A2": 594, "A3": 420, "A4": 297}
NUM = re.compile(r"^[A-Z]{0,2}\d{1,2}[.,]\d{2,3}$")
AREA = re.compile(r"^(\d+[.,]\d+)\s*m[²2]?$")
UNIT = re.compile(r"^(?:whg|wohnung|app|apt|appartement|appartamento|apartment|logement|unit|log)\.?\s*([A-Z1l|]{1,2})$",
                  re.I)  # 'Whg.1' / 'Whg.l' = Whg. I
VOCAB = ["Zimmer", "Küche", "Bad", "Korridor", "Treppenhaus", "Lift", "Balkon", "Wohnen/Essen", "Wohnen", "Essen",
         "Raum", "Reduit", "Dusche", "WC", "Abstellraum", "Keller", "Waschküche", "Loggia", "Terrasse", "Schlafzimmer",
         "Entrée", "Eingang", "Diele", "Gang", "Flur", "Vorraum", "Garderobe", "Büro", "Atelier", "Estrich",
         "Wintergarten", "Sitzplatz", "Chambre", "Chambre à coucher", "Cuisine", "Bain", "Salle de bains", "Douche",
         "Couloir", "Séjour", "Séjour/repas", "Repas", "Escalier", "Cage d'escalier", "Ascenseur", "Balcon",
         "Réduit", "Hall", "Pièce", "Local", "Camera", "Camera da letto", "Cucina", "Bagno", "Corridoio", "Soggiorno",
         "Soggiorno/pranzo", "Pranzo", "Stanza", "Scala", "Vano scala", "Ascensore", "Balcone", "Terrazza",
         "Ripostiglio", "Locale", "Disimpegno"]

_ENG = None
DEBUG = {}


def ocr_engine():
    global _ENG
    if _ENG is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENG = RapidOCR(intra_op_num_threads=int(os.environ.get("PNGVEC_THREADS", 2)), inter_op_num_threads=1,
                        max_side_len=8000, use_cls=False, text_score=0.0)
    return _ENG


def ocr(gray, max_px=6e6, min_conf=0.5):
    """RapidOCR on a grey image without the detector's own upscaling (its 'min side >= 736' rule blew a long thin
    crop up to >20k px and got a worker OOM-killed); very large inputs are shrunk instead."""
    f = min(1.0, (max_px / gray.size) ** 0.5)
    if f < 1:
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    res, _ = ocr_engine()(np.repeat(gray.astype(np.uint8)[..., None], 3, 2), det_limit_side_len=min(gray.shape),
                          det_limit_type="min")
    out = []
    for box, txt, conf in res or []:
        if conf >= min_conf:
            out.append((np.asarray(box, float) / f, txt, conf))
    return out


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def lev(a, b):
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (ca != cb))
    return d[-1]


def snap(label, vocab):
    """OCR label -> canonical vocabulary spelling (diacritics, one-letter slips)."""
    f = fold(label)
    if not f:
        return label
    best = min(vocab, key=lambda v: lev(f, fold(v)))
    dist = lev(f, fold(best))
    tol = 0 if len(f) < 3 else 1 if len(f) < 5 else int(0.4 * len(f))
    if len(f) == 3 and len(fold(best)) != 3:
        tol = 0
    return best if dist <= tol else label


# ----------------------------------------------------------------------------------------------------------- text
def read_text(rgb, min_conf=0.5, up=3):
    """Page OCR; low-confidence lines (text crossed by a door arc, tiny 1:200 labels) are re-read from 3x crops
    in one montage call and kept only if the re-read is confident."""
    g = rgb.mean(2)
    g[(rgb.max(2) - rgb.min(2)) > 40] = 255  # drop the coloured apartment overlay
    items, weak = [], []
    for box, txt, conf in ocr(g, min_conf=0.0):
        x0, y0, x1, y1 = box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max()
        it = dict(text=txt.strip(), conf=float(conf), box=(x0, y0, x1, y1))
        (items if conf >= min_conf else weak).append(it)
    if weak:
        H, W = g.shape
        crops = []
        for it in weak:
            x0, y0, x1, y1 = [int(v) for v in it["box"]]
            p = max(2, (y1 - y0) // 3)
            X0, Y0, X1, Y1 = max(0, x0 - p), max(0, y0 - p), min(W, x1 + p), min(H, y1 + p)
            crops.append((X0, Y0, cv2.resize(g[Y0:Y1, X0:X1].astype(np.uint8), None, fx=up, fy=up,
                                             interpolation=cv2.INTER_CUBIC)))
        items += [dict(text=it["text"], conf=it["conf"], box=it["box"]) for it in montage_ocr(crops, up)
                  if it["conf"] >= min_conf]
    return items


CONFUSE = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "l": "1", "I": "1", "|": "1", "S": "5", "Z": "2",
                        "B": "8"})


def fix_num(t):
    t = t.replace(",", ".")
    if "." in t:  # letters after the dot can only be misread digits
        a, b = t.split(".", 1)
        t = a.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1") + "." + b.translate(CONFUSE)
    m = re.match(r"^([A-Z]{0,2})(\d{1,2})\.(\d{2,3})$", t)
    return m and m.group(0)


UNIT_WORD = re.compile(r"^(?:whg|wohnung|app|apt|appartement|appartamento|apartment|logement|unit|log)\.?$", re.I)


def merge_unit_pieces(items):
    """'Whg.' and 'B' read as two boxes on one line -> one item."""
    out, used = [], set()
    for i, it in enumerate(items):
        if i in used:
            continue
        t = it["text"].strip(" []|(){}")
        if UNIT_WORD.match(t):
            x0, y0, x1, y1 = it["box"]
            h = y1 - y0
            for j, jt in enumerate(items):
                bx0, by0, bx1, by1 = jt["box"]
                if j != i and j not in used and re.match(r"^[A-Za-z1|]{1,2}[\]|)]?$", jt["text"].strip()) and \
                        abs((by0 + by1) / 2 - (y0 + y1) / 2) < 0.5 * h and x1 - h < bx0 < x1 + 2 * h:
                    it = dict(it, text=t + " " + jt["text"].strip(" []|()"),
                              box=(x0, min(y0, by0), bx1, max(y1, by1)))
                    used.add(j)
                    break
        out.append(it)
    return out


def classify(items, prefixes=None):
    """Split OCR lines into tokens of kind num / area / unit / label / scale / paper."""
    toks = []
    for it in merge_unit_pieces(items):
        t = it["text"].strip(" []|(){}")
        m = UNIT.match(t.replace(" ", "")) or UNIT.match(t)
        if m:
            toks.append(dict(it, kind="unit", value=re.sub(r"[1l|]", "I", m.group(1)).upper()))
            continue
        if UNIT_WORD.match(t):
            toks.append(dict(it, kind="unit", value=None))  # letter unreadable: re-read later
            continue
        for v in re.findall(r"1\s*:\s*(\d{2,4})", t):
            toks.append(dict(it, kind="scale", value=int(v)))
        for v in re.findall(r"\bA([0-4])\b", t):
            toks.append(dict(it, kind="paper", value="A" + v))
        m = re.match(r"^(\d+(?:[.,]\d+)?)\s*/?\s*(?:m|rn)", t)
        if m or re.match(r"^\d+[.,]\d+(?:\s*[²*]|\s+2)$", t):
            v = (m.group(1) if m else re.match(r"^(\d+[.,]\d+)", t).group(1)).replace(",", ".")
            toks.append(dict(it, kind="area", value=float(v) if "." in v else None))
            continue
        words = [re.sub(r"^[^\w]+|[^\w²]+$", "", w) or w for w in t.split()]  # '2.22/' (arc read as '/')
        if prefixes:  # '507' -> '5.07' when '5' is a known room-number prefix
            words = [next((p + "." + w[len(p):] for p in prefixes if re.fullmatch(re.escape(p) + r"\d{2,3}", w)), w)
                     for w in words]
        nums = [w for w in words if fix_num(w) and re.search(r"\d", w)]
        rest = " ".join(w for w in words if w not in nums)
        if nums and re.search(r"\d", rest):
            continue  # number + junk: not a room number
        for w in nums:
            toks.append(dict(it, kind="num", value=fix_num(w)))
        if rest and re.search(r"[A-Za-zÀ-ÿ]{2,}", rest) and not re.search(r"\d", rest):
            toks.append(dict(it, kind="label", value=rest.strip(" .-|:")))
    # a 'number' printed right under a room number is its area without a readable unit
    nums = [t for t in toks if t["kind"] == "num"]
    for t in nums:
        x0, y0, x1, y1 = t["box"]
        h = y1 - y0
        for u in nums:
            if u is not t and u["kind"] == "num" and 0 <= y0 - u["box"][3] <= 1.2 * h and \
                    min(x1, u["box"][2]) - max(x0, u["box"][0]) > 0.3 * (x1 - x0):
                t["kind"], t["value"] = "area", float(t["value"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
                break
    # room numbers of one floor share their prefix ("4." in 4.01..4.14): drop stray other prefixes
    pre = collections.Counter(t["value"].split(".")[0] for t in toks if t["kind"] == "num")
    if prefixes is not None:
        for t in toks:
            if t["kind"] == "num" and prefixes and t["value"].split(".")[0] not in prefixes:
                t["kind"] = "stray"
    elif pre:
        top = pre.most_common(1)[0][1]
        good = [p for p, c in pre.items() if c >= max(2, 0.25 * top)]
        for t in toks:
            if t["kind"] == "num" and pre[t["value"].split(".")[0]] < max(2, 0.25 * top):
                # one misread prefix character ('62.21' / '01.05' for U2.21 / U1.05, '6.04' for 0.04) -> fix
                p0, suf = t["value"].split(".")
                near = [p for p in good if lev(p0, p) <= 1 and len(p) == len(p0)]
                if len(near) == 1:
                    t["value"] = near[0] + "." + suf
                else:
                    t["kind"] = "stray"
    return toks


def scale_bar(rgb):
    """Graphic scale bar: a long thin dark bar with alternating filled / hollow segments; its labels ('0 1 2 5 m',
    '0 2 4 10 m') are re-read from an upscaled crop above it.  Returns metres per pixel or None."""
    g = rgb.mean(2)
    dark = (g < 110).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if w >= 120 and 3 <= h <= 14 and w > 15 * h and a > 0.5 * w * h:
            mid = lab[y + h // 2, x:x + w] == i
            light = g[y + h // 2, x:x + w] > 180  # hollow segments
            if light.sum() > 0.05 * w and (best is None or w > best[2]):
                best = (x, y, w, h)
    if best is None:
        return None, None
    x, y, w, h = best
    c = g[max(0, y - 6 * h):y, max(0, x - 3 * h):x + w + 6 * h]
    im = cv2.resize(c.astype(np.uint8), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    res = ocr(im)
    labels = []
    for box, txt, conf in res or []:
        for v in re.findall(r"\d+", txt):
            bx = np.asarray(box, float)[:, 0].mean() / 3 + max(0, x - 3 * h)
            labels.append((int(v), bx))
    if not labels:
        return None, best
    L, bx = max(labels)
    # the largest label sits over the right end of the bar (0 at the left end)
    if L <= 0 or abs(bx - (x + w)) > 4 * h + 20:
        return None, best
    return L / float(w), best


def scale_m_per_px(toks, shape, dpi, rgb=None):
    sc = collections.Counter(t["value"] for t in toks if t["kind"] == "scale")
    pa = collections.Counter(t["value"] for t in toks if t["kind"] == "paper")
    if pa:
        mm_per_px = PAPER_MM[pa.most_common(1)[0][0]] / max(shape)
    elif dpi:
        mm_per_px = 25.4 / dpi
    else:
        mm_per_px = 420 / max(shape)
    bar, _ = scale_bar(rgb) if rgb is not None else (None, None)
    info = dict(paper=pa.most_common(1)[0][0] if pa else None, mm_per_px=mm_per_px, dpi=dpi, bar_mpp=bar)
    if sc:
        den = sc.most_common(1)[0][0]
        mpp = den * mm_per_px / 1000.0
        info.update(scale=den, source="title", bar_agree=None if bar is None else round(bar / mpp - 1, 4))
        if bar is not None and abs(bar / mpp - 1) > 0.15:  # title misread (e.g. 1:10o): trust the bar's scale
            den = min((50, 100, 200, 250, 500, 1000), key=lambda d: abs(d * mm_per_px / 1000.0 / bar - 1))
            mpp = den * mm_per_px / 1000.0
            info.update(scale=den, source="bar-snapped")
        return mpp, info
    if bar is not None:
        den = min((50, 100, 200, 250, 500, 1000), key=lambda d: abs(d * mm_per_px / 1000.0 / bar - 1))
        info.update(scale=den, source="bar")
        return den * mm_per_px / 1000.0, info
    info.update(scale=100, source="default")
    return 100 * mm_per_px / 1000.0, info


# ---------------------------------------------------------------------------------------------------- geometry
def wall_fill_level(g, sat):
    m = (sat < 20) & (g > 90) & (g < 215)
    return int(np.argmax(np.bincount(g[m].astype(np.int32), minlength=256)))


def grow(lab, mask, steps):
    """Geodesic label growth: unlabelled mask pixels take a neighbouring label, one pixel per step (never jumps
    across a wall the way a Euclidean expansion would)."""
    ex = lab.astype(np.float32)
    k = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(steps):
        d = cv2.dilate(ex, k)
        upd = (ex == 0) & mask & (d > 0)
        ex[upd] = d[upd]
    return ex.astype(np.int32)


def segment(rgb, long_px=70, expand=4, strong_thr=330, solid_g=45, text_boxes=()):
    g = rgb.mean(2)
    sat = rgb.max(2) - rgb.min(2)
    bg = cv2.dilate(g, np.ones((7, 7), np.uint8))
    dark = (g < bg - 25) | (g < 200)
    wf = wall_fill_level(g, sat)
    core = ((np.abs(g - wf) <= 10) & (sat < 20)).astype(np.uint8)
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    core = cv2.morphologyEx(core, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
    # line strength = darkness integrated across the line (independent of sub-pixel position)
    D = np.clip(bg - g, 0, None)
    D[sat > 40] = 0
    Sh = cv2.filter2D(D, -1, np.ones((1, 7), np.float32))
    Sv = cv2.filter2D(D, -1, np.ones((7, 1), np.float32))
    # thin solid partitions: strong dark bands that touch the wall core twice, or once and are long (door leaves
    # touch it once and are short)
    strong = (np.minimum(Sh, Sv) >= strong_thr) & dark & ~core
    for x0, y0, x1, y1 in text_boxes:  # bold text is not a wall
        strong[int(y0):int(y1) + 1, int(x0):int(x1) + 1] = False
    sl, ns = ndi.label(strong, np.ones((3, 3)))
    if ns:
        cl, nc = ndi.label(strong & cv2.dilate(core.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool),
                           np.ones((3, 3)))
        contacts = collections.Counter()
        if nc:
            owner_of = ndi.maximum(sl, cl, np.arange(1, nc + 1)).astype(int)
            contacts.update(owner_of.tolist())
        part = np.zeros(ns + 1, bool)
        # solid: black (not grey) along the band - double grey fixture rims (bathtubs) are strong too
        gmin = cv2.erode(g, np.ones((3, 3), np.uint8))
        solid = np.asarray(ndi.median(gmin, sl, np.arange(ns + 1))) < solid_g
        for i, sl_ in enumerate(ndi.find_objects(sl), 1):
            ext = max(sl_[0].stop - sl_[0].start, sl_[1].stop - sl_[1].start)
            part[i] = solid[i] and (contacts[i] >= 2 or (contacts[i] >= 1 and ext >= long_px))
        core = core | part[sl]
    free = ~dark & ~core
    lab, n = ndi.label(free)
    ex = grow(lab, ~core, expand)
    # border strength measured against the darker of the two faces' fills (a fill step is not a line)
    fill = np.asarray(ndi.median(g, lab, np.arange(n + 1)))
    gp = np.pad(np.where(sat > 40, 255.0, g), 3, constant_values=255)
    keys, vals = [], []
    for axis, (a, b) in enumerate(((ex[:, :-1], ex[:, 1:]), (ex[:-1], ex[1:]))):
        m = (a != b) & (a > 0) & (b > 0)
        ys, xs = np.nonzero(m)
        A, B = a[m], b[m]
        ref = np.minimum(fill[A], fill[B])
        S = np.zeros(len(ys))
        for k in range(-3, 4):
            v = gp[ys + 3, xs + 3 + k] if axis == 0 else gp[ys + 3 + k, xs + 3]
            S += np.clip(ref - v, 0, None)
        keys.append(np.minimum(A, B).astype(np.int64) * (n + 1) + np.maximum(A, B))
        vals.append(S)
    keys, vals = np.concatenate(keys), np.concatenate(vals)
    o = np.lexsort((vals, keys))
    keys, vals = keys[o], vals[o]
    uk, start, cnt = np.unique(keys, return_index=True, return_counts=True)
    edges = {(int(k // (n + 1)), int(k % (n + 1))): (int(c), float(vals[s0 + c // 2]))
             for k, s0, c in zip(uk, start, cnt)}
    sizes = np.bincount(ex.ravel(), minlength=n + 1)
    border = set(np.unique(np.r_[ex[0], ex[-1], ex[:, 0], ex[:, -1]]).tolist()) - {0}
    # the space around the building inside the sheet frame: a face spanning > 40% of the sheet both ways
    H, W = lab.shape
    for L, (sy, sx) in enumerate(ndi.find_objects(lab), 1):
        if sy is not None and sy.stop - sy.start > 0.4 * H and sx.stop - sx.start > 0.4 * W:
            border.add(L)
    dt = ndi.distance_transform_edt(free)
    thick = 2 * ndi.maximum(dt, lab, np.arange(n + 1))
    edge_px = (ex != np.roll(ex, 1, 0)) | (ex != np.roll(ex, -1, 0)) | (ex != np.roll(ex, 1, 1)) | (ex != np.roll(ex, -1, 1))
    perim = np.bincount(ex[edge_px & (ex > 0)], minlength=n + 1)
    return dict(g=g, sat=sat, lab=lab, ex=ex, n=n, edges=edges, fill=fill, sizes=sizes, border=border, core=core, wf=wf,
                thick=np.asarray(thick), strength=vals, perim=perim)


def hard_threshold(seg):
    """Split border strengths into the weak (room outline) cluster and the rest: valley after the first mode."""
    v = np.array([s for c, s in seg["edges"].values()])
    w = np.array([c for c, s in seg["edges"].values()], float)
    h, e = np.histogram(v, bins=np.arange(0, 400, 10), weights=w)
    sm = np.convolve(h, [1, 2, 1], "same") / 4
    peak = int(np.argmax(sm[:15]))  # weakest prominent mode below 150
    for i in range(peak + 1, len(sm) - 1):
        if sm[i] <= 0.15 * sm[peak] or (sm[i] <= sm[i + 1] and sm[i] < 0.5 * sm[peak]):
            return float(e[i] + 5)
    return 95.0


def wall_strips(seg, max_thick_px, jamb_px, gap=6):
    """Thin axis-aligned rectangular faces whose two short ends abut the wall core: the part of a door opening or a
    window that lies inside the wall thickness (gold room outlines stop at the wall faces)."""
    out = set()
    lab, core = seg["lab"], seg["core"]
    H, W = lab.shape
    for L, (sy, sx) in enumerate(ndi.find_objects(lab), 1):
        if sy is None or seg["thick"][L] > max_thick_px or seg["sizes"][L] < 20:
            continue
        h, w = sy.stop - sy.start, sx.stop - sx.start
        f = lab[sy, sx] == L
        if f.sum() < 0.7 * h * w or max(h, w) < 2.5 * min(h, w):  # rectangle, elongated
            continue
        t = min(h, w)
        C = core if w >= h else core.T  # work as if the strip were horizontal
        (r0, r1), (c0, c1) = ((sy.start, sy.stop), (sx.start, sx.stop)) if w >= h else \
            ((sx.start, sx.stop), (sy.start, sy.stop))
        ok = True
        for a, b in ((max(0, c0 - gap), c0), (c1, min(C.shape[1], c1 + gap))):
            end = C[:, a:b].any(1)
            cover = end[r0:r1].mean() if b > a else 0
            # the end must be a jamb of the same wall (core run beyond the strip short on at least one side; a
            # T-junction is fine), not a long perpendicular wall (a bathtub or counter wedged between two walls)
            lo, hi = r0, r1
            while lo > 0 and end[lo - 1]:
                lo -= 1
            while hi < len(end) and end[hi]:
                hi += 1
            ok &= cover >= 0.6 and min(r0 - lo, hi - r1) <= max(2 * t, jamb_px)
        if ok:
            out.add(L)
    return out


def wall_edge_offset(region, core, g, wf, nk, depth=5):
    H, W = region.shape
    gp = np.pad(g, depth, constant_values=255)
    corep = np.pad(core, depth)
    edge, cnt = np.zeros(nk), np.zeros(nk)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        ys, xs = np.nonzero((region > 0) & np.roll(core, (-dy, -dx), (0, 1)))
        lab_ = region[ys, xs]
        w = np.zeros(len(ys))
        hit = np.zeros(len(ys), bool)
        for k in range(1, depth + 1):
            v = gp[ys + depth + k * dy, xs + depth + k * dx]
            isfill = (np.abs(v - wf) < 12) | ~corep[ys + depth + k * dy, xs + depth + k * dx]
            hit |= isfill
            w += np.where(hit, 0, np.clip(255 - v, 0, 255) / 255)
        d = np.where(hit, w / 2, 0.25)
        edge += np.bincount(lab_, weights=d, minlength=nk)
        cnt += np.bincount(lab_, minlength=nk)
    return edge, cnt


def assign(seg, seeds, thr, hard_faces=(), big_px=1e9, fill_tol=6, thin_px=0):
    """Union faces across soft borders, strongest first, never joining two tagged groups (the page margin is one
    tagged 'outside' group) nor two big groups of different fill (a grey balcony does not join the white outside
    across its black railing; small white fixtures or stair steps still join a grey room)."""
    parent = list(range(seg["n"] + 1))
    hard_faces = set(hard_faces) - set(seeds)
    border = sorted(set(seg["border"]) - set(seeds))
    tag = dict(seeds)
    fill, size = seg["fill"], seg["sizes"]
    dom = {L: (int(size[L]), float(fill[L])) for L in range(seg["n"] + 1)}  # group -> (size, fill) of largest face
    tot = {L: int(size[L]) for L in range(seg["n"] + 1)}  # group -> total size
    per = {L: int(seg["perim"][L]) for L in range(seg["n"] + 1)}  # group -> outline length (px)
    # thin unnumbered faces lying against the outside with the outside's fill (a balcony parapet band) are outside
    if border:
        bset, ofill = set(border), float(np.median([fill[L] for L in border]))
        for (a, b) in seg["edges"]:
            for x, y in ((a, b), (b, a)):
                if y in bset and x not in tag and x not in hard_faces and seg["thick"][x] <= thin_px and \
                        abs(fill[x] - ofill) <= fill_tol:
                    border.append(x)
    for L in border:
        parent[L] = border[0]
        dom[border[0]] = max(dom[border[0]], dom[L])
    if border:
        tag[border[0]] = None

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    soft = [(e, cs) for e, cs in seg["edges"].items() if cs[1] > thr]
    order = (lambda e: -e[1][0]) if MERGE_ORDER == "length" else (lambda e: -e[1][1])
    for (a, b), (c, s) in sorted(soft, key=order):
        if a in hard_faces or b in hard_faces:
            continue
        ra, rb = find(a), find(b)
        if ra == rb or (ra in tag and rb in tag):
            continue
        (sa, fa), (sb, fb) = dom[ra], dom[rb]
        if abs(fa - fb) > fill_tol and min(sa, sb) >= big_px:
            continue
        # a numbered room does not swallow an unnumbered space much larger than itself (a hall or void whose
        # number is missing / not printed)
        if (ra in tag) != (rb in tag):
            t_, u_ = (ra, rb) if ra in tag else (rb, ra)
            if tot[u_] > max(3 * tot[t_], 80 * big_px):
                continue
            # a room-sized unnumbered group joins only across a good part of its outline (not through a door
            # arc: that is a neighbouring room whose number was missed)
            if PERIM_FRAC and tot[u_] > 2 * big_px and c < PERIM_FRAC * per[u_]:
                continue
        parent[rb] = ra
        dom[ra] = max(dom[ra], dom[rb])
        tot[ra] += tot[rb]
        per[ra] += per[rb] - 2 * c
        if rb in tag:
            tag[ra] = tag.pop(rb)
    roots = [find(L) for L in range(seg["n"] + 1)]
    owner = {L: tag[roots[L]] for L in range(1, seg["n"] + 1) if tag.get(roots[L]) is not None}
    free = collections.defaultdict(list)  # untagged groups (candidate rooms whose number OCR missed)
    for L in range(1, seg["n"] + 1):
        if roots[L] not in tag and L not in hard_faces:
            free[roots[L]].append(L)
    return owner, list(free.values())


def face_at(seg, box, avoid=()):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    h = max(4, y1 - y0)
    win = seg["lab"][max(0, y0 - h // 2):y1 + h // 2, max(0, x0 - h // 2):x1 + h // 2]
    vals, cnt = np.unique(win[win > 0], return_counts=True)
    ok = [(c, v) for v, c in zip(vals, cnt) if v not in avoid]
    return int(max(ok)[1]) if ok else None


def reread_unit(rgb, box, up=3):
    """Tag whose letter OCR missed ('Whg.' alone): re-read a crop extended to the right, upscaled."""
    x0, y0, x1, y1 = [int(v) for v in box]
    h = y1 - y0
    c = rgb[max(0, y0 - h // 2):y1 + h // 2, max(0, x0 - h // 2):x1 + 3 * h].mean(2).astype(np.uint8)
    im = cv2.resize(c, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    items = [dict(text=t, box=(b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()))
             for b, t, _ in ocr(im, min_conf=0.3)]
    for it in merge_unit_pieces(items):  # 'Whg.' + 'c'
        t = it["text"].strip(" []|(){}")
        m = UNIT.match(t.replace(" ", "")) or UNIT.match(t)
        if m:
            return re.sub(r"[1l|]", "I", m.group(1)).upper()
    return None


def apartment_units(rgb, toks, near_px=30):
    """Coloured dash-dot loops -> regions; a loop is named by the tag inside it; untagged loops (an apartment drawn
    as several loops) take the tag of the nearest tagged loop."""
    alab, na = apartments(rgb, None)
    if not na:
        return alab, 0, {}
    unit_of = {}
    tags = []
    for t in toks:
        if t["kind"] == "unit":
            val = t["value"] or reread_unit(rgb, t["box"])
            if val and val not in [v for v, _ in tags]:
                tags.append((val, [int(v) for v in t["box"]]))
    # a tag names the loop it lies in; a tag placed on a wall or outside its (concave) loop takes the untagged
    # loop with most pixels in a growing window around it
    left = []
    for val, (x0, y0, x1, y1) in tags:
        win = alab[y0:y1 + 1, x0:x1 + 1]
        v = np.bincount(win.ravel(), minlength=na + 1)
        a = int(np.argmax(v[1:])) + 1
        if v[a] >= 0.5 * win.size and a not in unit_of:
            unit_of[a] = val
        else:
            left.append((val, (x0, y0, x1, y1)))
    for val, (x0, y0, x1, y1) in left:
        h = max(4, y1 - y0)
        for r in (h, 2 * h, 4 * h, 8 * h):
            win = alab[max(0, y0 - r):y1 + r + 1, max(0, x0 - r):x1 + r + 1]
            v = np.bincount(win.ravel(), minlength=na + 1)
            v[0] = 0
            for a in unit_of:
                v[a] = 0
            if v.max() > 0:
                unit_of[int(np.argmax(v))] = val
                break
    if unit_of and len(unit_of) < na:
        # an untagged loop inherits only from a tagged loop right next to it (one wall apart) and only if no
        # other tagged loop is about as close (otherwise it is an apartment whose tag was not read)
        dists = {}
        for a in unit_of:
            dists[a] = ndi.distance_transform_edt(alab != a)
        for a in range(1, na + 1):
            if a not in unit_of:
                m = alab == a
                d = sorted((float(dists[b][m].min()), b) for b in dists)
                if d[0][0] <= near_px and (len(d) == 1 or d[1][0] > 1.5 * d[0][0] + 2):
                    unit_of[a] = unit_of[d[0][1]]
        del dists
    return alab, na, unit_of


def apartments(rgb, ex_shape, L=13):
    """Saturated (coloured) dash-dot boundary -> closed loops.  Gaps are bridged only along the line direction
    (horizontal / vertical / diagonal line closings), so the boundaries of two apartments a thin wall apart do not
    fuse (an isotropic dilation merged neighbouring apartments)."""
    sat = (rgb.max(2) - rgb.min(2)) > 60
    if sat.sum() < 200:
        return None, 0
    red = sat.astype(np.uint8)
    ks = [np.ones((1, L), np.uint8), np.ones((L, 1), np.uint8), np.eye(L, dtype=np.uint8),
          np.fliplr(np.eye(L, dtype=np.uint8)).copy()]
    closed = cv2.morphologyEx(red, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    for k in ks:  # small isotropic closing for corners, line closings for the dash gaps
        closed |= cv2.morphologyEx(red, cv2.MORPH_CLOSE, k)
    # remaining gaps (a corner falling into a dash gap, a jog of the line): join skeleton end points that face
    # each other within L px
    from skimage.morphology import skeletonize
    sk = skeletonize(closed > 0).astype(np.uint8)
    nbc = cv2.filter2D(sk, -1, np.ones((3, 3), np.float32)) - sk
    ys, xs = np.nonzero((sk > 0) & (nbc == 1))
    if len(ys):
        P_ = np.stack([xs, ys], 1).astype(float)
        D_ = np.hypot(*(P_[:, None, :] - P_[None, :, :]).transpose(2, 0, 1))
        np.fill_diagonal(D_, np.inf)
        used_ = set()
        for i in np.argsort(D_.min(1)):
            j = int(np.argmin(D_[i]))
            if i in used_ or j in used_ or D_[i, j] > L:
                continue
            cv2.line(closed, (int(xs[i]), int(ys[i])), (int(xs[j]), int(ys[j])), 1, 2)
            used_ |= {i, j}
    filled = ndi.binary_fill_holes(closed) & ~(closed > 0)
    # fallback where a loop still has a gap: an isotropic dilation closes it (but would fuse neighbouring
    # apartments, so it only fills areas no line-closed loop covers)
    dil = cv2.dilate(red, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    extra = ndi.binary_fill_holes(dil) & ~(dil > 0) & ~cv2.dilate(filled.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    extra = cv2.morphologyEx(extra.astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)) > 0
    lab, n = ndi.label(filled | extra)
    if n:  # drop specks (inside letters of coloured text etc.)
        sizes = np.bincount(lab.ravel())
        small = sizes < 2000
        small[0] = False
        lab[small[lab]] = 0
        lab, n = ndi.label(lab > 0)
    return lab, n


# ------------------------------------------------------------------------------------------------------- solve
def absorb_enclosed(seg, owner, free, strips, fill_tol=8, near_px=3, thin_px=1e9):
    """Unnumbered groups bordered (apart from walls) by one room only join it: fixtures drawn with outline-weight
    lines, the gap between a bathtub and the wall (fill must match), and chains of thin strips that only touch that
    room (stair treads).  Strip chains touching two rooms or the outside (door openings, windows) and groups
    touching an unresolved neighbour stay out."""
    sset = {L for L in strips if L not in owner}
    par = {L: L for L in sset}

    def f(x):
        while par[x] != x:
            x = par[x]
        return x
    for (a, b) in seg["edges"]:
        if a in sset and b in sset:
            par[f(a)] = f(b)
    chains = collections.defaultdict(list)
    for L in sset:
        chains[f(L)].append(L)
    groups = [list(g) for g in free] + list(chains.values())
    nfree = len(free)
    gid = {L: i for i, fs in enumerate(groups) for L in fs}
    nbs = collections.defaultdict(set)
    for (a, b) in seg["edges"]:
        for x, y in ((a, b), (b, a)):
            if x in gid and gid.get(y) != gid[x]:
                nbs[gid[x]].add(owner.get(y, ("G", gid[y]) if y in gid else "OUT"))
    room_fill = {}
    for L, o in owner.items():
        if o not in room_fill or seg["sizes"][L] > room_fill[o][0]:
            room_fill[o] = (seg["sizes"][L], seg["fill"][L])
    # groups walled in completely (a bathtub whose thick rim was taken for a partition) see their neighbours
    # through the thin dark band around them
    lab = seg["lab"]
    objs = ndi.find_objects(lab)
    for i, fs in enumerate(groups[:nfree]):
        if nbs[i]:
            continue
        ys = [objs[L - 1][0] for L in fs]
        xs = [objs[L - 1][1] for L in fs]
        y0, y1 = max(0, min(q.start for q in ys) - near_px - 1), max(q.stop for q in ys) + near_px + 1
        x0, x1 = max(0, min(q.start for q in xs) - near_px - 1), max(q.stop for q in xs) + near_px + 1
        sub = lab[y0:y1, x0:x1]
        m = np.isin(sub, fs)
        ring = cv2.dilate(m.astype(np.uint8), np.ones((2 * near_px + 1,) * 2, np.uint8)).astype(bool) & ~m
        for M in np.unique(sub[ring]):
            if M and M not in fs:
                nbs[i].add(owner.get(M, ("G", gid[M]) if M in gid else "OUT"))
    changed, done = True, set()
    while changed:
        changed = False
        for i, fs in enumerate(groups):
            if i in done or "OUT" in nbs[i]:
                continue
            rooms_ = {o for o in nbs[i] if not isinstance(o, tuple)}
            others = [o for o in nbs[i] if isinstance(o, tuple)]
            # a strip chain must touch nothing but that one room - or thin unnumbered groups (stair treads)
            r0 = next(iter(rooms_)) if len(rooms_) == 1 else None
            if r0 is None or (i >= nfree and not all(
                    o[1] < nfree and (max(seg["thick"][L] for L in groups[o[1]]) <= thin_px or  # stair treads
                                      max(seg["fill"][L] for L in groups[o[1]]) < room_fill[r0][1] - fill_tol)
                    for o in others)):  # ... or shafts (darker fill: never rooms)
                continue
            r = next(iter(rooms_))
            big = max(fs, key=lambda L: seg["sizes"][L])
            if i < nfree and seg["fill"][big] < room_fill[r][1] - fill_tol:  # darker: a shaft; lighter is ok (treads)
                continue
            for L in fs:
                owner[L] = r
            done.add(i)
            changed = True
            for j in nbs:
                if ("G", i) in nbs[j]:
                    nbs[j].discard(("G", i))
                    nbs[j].add(r)


ANON = True
OUT_CONTACT_M = 0.5  # unlabelled room touching the outside across >= this much line length = balcony
PERIM_FRAC = 0.15
MERGE_ORDER = "length"  # soft borders are merged longest first (a face joins the room it shares most border with)
EXTRA_TEXT = True


def add_anonymous(seg, owner, free, strips, info, size_rng, fill_tol=6):
    """Room-like groups left without a number (OCR missed it): white/room-filled, 0.8-40 m2, not touching the
    outside.  They count for floor / apartment totals (gold sums every numbered room) under a placeholder number
    that no room query can match."""
    in_free = {L for g in free for L in g}
    outside = set(range(1, seg["n"] + 1)) - set(owner) - in_free - set(strips)
    fills = collections.Counter(int(round(seg["fill"][L])) for L in owner if seg["sizes"][L] > size_rng[0])
    room_fills = [f for f, c in fills.items() if c >= 2] or [255]
    nb = collections.defaultdict(set)
    for (a, b) in seg["edges"]:
        nb[a].add(b)
        nb[b].add(a)
    k = 0
    for g in free:
        if any(L in owner for L in g):
            continue
        size = seg["sizes"][g].sum()
        big = max(g, key=lambda L: seg["sizes"][L])
        if not size_rng[0] <= size <= size_rng[1] or min(abs(seg["fill"][big] - f) for f in room_fills) > fill_tol:
            continue
        if any(m in outside for L in g for m in nb[L]):
            continue
        k += 1
        key = "?%d" % k
        for L in g:
            owner[L] = key
        info[key] = dict(label=None, raw_label=None, box=None, printed=None, printed_box=None)


def pair_text(seg, toks, strips, vocab, seeds, info, only=None):
    """Room number -> its face (seed); label = nearest label line just above, printed area just below."""
    labels = [t for t in toks if t["kind"] == "label"]
    areas_t = [t for t in toks if t["kind"] == "area"]
    for t in toks:
        if t["kind"] != "num":
            continue
        L = face_at(seg, t["box"], set(strips) | seg["border"])  # a number spilling outside a tiny balcony
        if only is not None and L not in only:
            L = max(only, key=lambda f: seg["sizes"][f])
        if L is None or L in seeds or t["value"] in info:
            continue
        x0, y0, x1, y1 = t["box"]
        h, cx = y1 - y0, (x0 + x1) / 2

        def near(c, above):
            bx0, by0, bx1, by1 = c["box"]
            dy = (y0 - by1) if above else (by0 - y1)
            return -0.5 * h <= dy <= 1.2 * h and bx0 - h < cx < bx1 + h
        lab_c = [c for c in labels if near(c, True)]
        ar_c = [c for c in areas_t if near(c, False)]
        best = min(lab_c, key=lambda c: abs((c["box"][0] + c["box"][2]) / 2 - cx)) if lab_c else None
        lab = best["value"] if best else None
        if best:
            best["used"] = True
        seeds[L] = t["value"]
        info[t["value"]] = dict(label=snap(lab, vocab) if lab else None, raw_label=lab, box=t["box"],
                                printed=ar_c[0]["value"] if ar_c else None,
                                printed_box=ar_c[0]["box"] if ar_c else None)


def montage_ocr(crops, up, gap=40, MW=2400):
    """Many small crops [(x0, y0, upscaled image)] in one OCR call: shelf-packed with white gutters, each read
    word mapped back to its crop and to page coordinates."""
    pos, x, y, rowh = [], gap, gap, 0
    for _, _, c in crops:
        h, w = c.shape
        if x + w + gap > MW:
            x, y, rowh = gap, y + rowh + gap, 0
        pos.append((x, y))
        x, rowh = x + w + gap, max(rowh, h)
    items = []
    if not crops:
        return items
    M = np.full((y + rowh + gap, max(MW, max(c.shape[1] for _, _, c in crops) + 2 * gap)), 255, np.uint8)
    for (px_, py_), (_, _, c) in zip(pos, crops):
        M[py_:py_ + c.shape[0], px_:px_ + c.shape[1]] = c
    for b, txt, conf in ocr(M):
        cx, cy = b[:, 0].mean(), b[:, 1].mean()
        for k, ((px_, py_), (x0, y0, c)) in enumerate(zip(pos, crops)):
            if px_ <= cx < px_ + c.shape[1] and py_ <= cy < py_ + c.shape[0]:
                bb = (b - [px_, py_]) / up + [x0, y0]
                items.append(dict(text=txt.strip(), conf=float(conf), crop=k,
                                  box=(bb[:, 0].min(), bb[:, 1].min(), bb[:, 0].max(), bb[:, 1].max())))
                break
    return items


def reread_areas(rgb, boxes, up=3):
    """Printed areas re-read from 3x crops (a narrow leading '1' is easily dropped at page scale)."""
    g = rgb.mean(2)
    H, W = g.shape
    crops = []
    for x0, y0, x1, y1 in boxes:
        h = int(y1 - y0)
        X0, Y0, X1, Y1 = max(0, int(x0) - h), max(0, int(y0) - h // 3), min(W, int(x1) + h // 2), min(H, int(y1) + h // 3)
        crops.append((X0, Y0, cv2.resize(g[Y0:Y1, X0:X1].astype(np.uint8), None, fx=up, fy=up,
                                         interpolation=cv2.INTER_CUBIC)))
    vals = [None] * len(boxes)
    for it in montage_ocr(crops, up):
        m = re.match(r"^(\d+[.,]\d+)", it["text"].replace(" ", ""))
        if m and vals[it["crop"]] is None:
            vals[it["crop"]] = float(m.group(1).replace(",", "."))
    return vals


def unexplained_text(rgb, toks, core, prefixes, up=3):
    """Text the page OCR did not read (a room number crossed by a door arc): glyph-sized dark components (long
    straight lines removed) outside every OCR box are grouped into word blobs and each blob is re-read alone."""
    hs = [t["box"][3] - t["box"][1] for t in toks if t["kind"] in ("num", "label")]
    if not hs:
        return []
    th = float(np.median(hs))
    g = rgb.mean(2)
    g[(rgb.max(2) - rgb.min(2)) > 40] = 255
    dark = (g < 140).astype(np.uint8)
    L = max(15, int(2 * th))
    lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, L), np.uint8)) | \
        cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((L, 1), np.uint8))
    d2 = dark & ~cv2.dilate(lines, np.ones((3, 3), np.uint8)) & ~core.astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(d2, connectivity=8)
    keep = (st[:, 3] <= 1.2 * th) & (st[:, 2] <= 2 * th) & (st[:, 4] >= 4)
    keep[0] = False
    gl = keep[lab]
    for t in toks:  # already read
        x0, y0, x1, y1 = [int(v) for v in t["box"]]
        gl[max(0, y0 - 2):y1 + 3, max(0, x0 - 2):x1 + 3] = False
    blobs = cv2.dilate(gl.astype(np.uint8), np.ones((max(3, int(th / 3)), max(3, int(th / 2))), np.uint8))
    nb, bl, bst, _ = cv2.connectedComponentsWithStats(blobs)
    crops = []
    H, W = g.shape
    for i in range(1, nb):
        x, y, w, h, a = bst[i]
        if w < 0.8 * th or h > 3 * th or w > 12 * th or gl[y:y + h, x:x + w].sum() < 0.25 * th * th:
            continue
        pad = int(th / 2)
        y0, y1, x0, x1 = max(0, y - pad), min(H, y + h + pad), max(0, x - pad), min(W, x + w + pad)
        crops.append((x0, y0, cv2.resize(g[y0:y1, x0:x1].astype(np.uint8), None, fx=up, fy=up,
                                         interpolation=cv2.INTER_CUBIC)))
    DEBUG["n_blobs"] = len(crops)
    items = montage_ocr(crops, up)
    return classify(items, prefixes)


def crop_tokens(rgb, seg, faces, prefixes, up=2, pad=6):
    """OCR one candidate room on its own: its faces (holes filled = the text inside) on white, upscaled."""
    m = np.isin(seg["lab"], faces)
    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = max(0, ys.min() - pad), ys.max() + pad + 1, max(0, xs.min() - pad), xs.max() + pad + 1
    mm = ndi.binary_fill_holes(cv2.dilate(m[y0:y1, x0:x1].astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    g = rgb[y0:y1, x0:x1].mean(2)
    g[(rgb[y0:y1, x0:x1].max(2) - rgb[y0:y1, x0:x1].min(2)) > 40] = 255
    g[~mm] = 255
    up = min(up, max(1.0, (2.0e6 / g.size) ** 0.5))  # keep the detector input small (memory)
    im = cv2.resize(g.astype(np.uint8), None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    res = ocr(im)
    items = []
    for box, txt, conf in res or []:
        b = np.asarray(box, float) / up
        items.append(dict(text=txt.strip(), conf=float(conf),
                          box=(b[:, 0].min() + x0, b[:, 1].min() + y0, b[:, 0].max() + x0, b[:, 1].max() + y0)))
    return classify(items, prefixes)


def read_rooms(path, vocab=()):
    rgb, dpi = load(path)
    toks = classify(read_text(rgb))
    mpp, scale_info = scale_m_per_px(toks, rgb.shape[:2], dpi, rgb)
    seg = segment(rgb, long_px=1.2 / mpp, text_boxes=[t["box"] for t in toks if t["kind"] in ("num", "label", "area", "unit")])
    thr = hard_threshold(seg)
    strips = wall_strips(seg, 0.45 / mpp, 0.4 / mpp)
    vocab = list(dict.fromkeys(list(vocab) + VOCAB))
    seeds, info = {}, {}
    prefixes = {t["value"].split(".")[0] for t in toks if t["kind"] == "num"}
    if EXTRA_TEXT:
        known = {t["value"] for t in toks if t["kind"] == "num"}
        toks = toks + [t for t in unexplained_text(rgb, toks, seg["core"], prefixes)
                       if t["kind"] != "num" or t["value"] not in known]
    pair_text(seg, toks, strips, vocab, seeds, info)
    big = 0.5 / mpp ** 2
    owner, free = assign(seg, seeds, thr, strips, big_px=big, thin_px=0.25 / mpp)
    # second OCR pass on unnumbered candidate rooms: crop the group alone (everything else whitened), upscale
    n_extra = 0
    for faces in free:
        if not 0.4 / mpp ** 2 <= seg["sizes"][faces].sum() <= 150 / mpp ** 2:
            continue
        sub = crop_tokens(rgb, seg, faces, prefixes)
        before = len(seeds)
        pair_text(seg, sub, strips, vocab, seeds, info, only=set(faces))
        n_extra += len(seeds) - before
    if n_extra:
        owner, free = assign(seg, seeds, thr, strips, big_px=big, thin_px=0.25 / mpp)
    absorb_enclosed(seg, owner, free, strips, near_px=max(2, int(round(0.1 / mpp))), thin_px=0.45 / mpp)
    if ANON:
        add_anonymous(seg, owner, free, strips, info, (0.8 / mpp ** 2, 40 / mpp ** 2))
    # an anonymous room containing an unpaired room label (its number was not read) carries that label, so a room
    # query whose number was not found can still be answered when exactly one such room has the queried label
    for t in toks:
        if t["kind"] == "label" and not t.get("used"):
            L = face_at(seg, t["box"], set(strips) | seg["border"])
            k = owner.get(L)
            if k and k.startswith("?") and info[k]["label"] is None:
                lab = snap(t["value"], vocab)
                if lab in vocab:
                    info[k]["label"] = lab
    ex = seg["ex"]
    O = np.zeros(seg["n"] + 1, np.int32)
    keys = sorted(set(owner.values()))
    kid = {k: i + 1 for i, k in enumerate(keys)}
    for L, o in owner.items():
        O[L] = kid[o]
    region = O[ex]
    DEBUG.update(seg=seg, region=region, kid=kid, strips=strips, seeds=seeds, toks=toks)
    px = np.bincount(region.ravel(), minlength=len(keys) + 1)
    # sub-pixel wall edge: the room outline runs along the centre of the wall's outline stroke, which the region
    # stops in front of; per boundary pixel add half the stroke width, measured as the darkness integral of the
    # dark run before the wall fill (a solid partition with no fill behind gets a quarter pixel)
    edge, bc = wall_edge_offset(region, seg["core"], seg["g"], seg["wf"], len(keys) + 1)
    alab, na, unit_of_ap = apartment_units(rgb, toks, near_px=0.5 / mpp)
    rooms = []
    fill = ndi.median(seg["g"], region, np.arange(len(keys) + 1)) if keys else []
    pk = [k for k in keys if info[k].get("printed_box") is not None]
    reread = dict(zip(pk, reread_areas(rgb, [info[k]["printed_box"] for k in pk])))
    for k in keys:
        i = kid[k]
        unit = None
        if na:
            v = np.bincount(alab[region == i], minlength=na + 1)
            v[0] = 0
            if v.max() > 0.3 * px[i]:
                unit = unit_of_ap.get(int(np.argmax(v)))
        inf = info[k]
        area = (px[i] + edge[i]) * mpp * mpp
        # printed area (= gold when present): page read or 3x re-read, whichever is nearer the measurement;
        # trusted unless wildly off (then the read is broken, a big mismatch of the right read = merged rooms)
        cands = [v for v in (inf["printed"], reread.get(k)) if v and "%.2f" % v != k]
        lr = lambda v: abs(np.log(v / max(area, 1e-6)))
        pv = min(cands, key=lr) if cands else None
        if pv is not None and lr(pv) > 0.3 and any(lr(v + 10) < 0.1 for v in cands if v < 10):
            pv = min((v + 10 for v in cands if v < 10), key=lr)  # narrow leading '1' dropped (17.69 -> 7.69)
        printed = pv is not None and 0.3 < pv / max(area, 1e-6) < 3
        rooms.append(dict(number=k, label=inf["label"], area=pv if printed else area,
                          measured=round(area, 3), bc=int(bc[i]), px=int(px[i]), unit=unit, printed=printed, fill=float(fill[i]),
                          raw_label=inf["raw_label"]))
    # outdoor by fill: unlabelled rooms sharing the fill of rooms whose labels are outdoor
    out_fills = [r["fill"] for r in rooms if r["label"] and lexicon.indoor(r["label"]) is False]
    in_fills = [r["fill"] for r in rooms if r["label"] and not lexicon.indoor(r["label"]) is False]
    for r in rooms:
        if not r["label"] and any(abs(r["fill"] - f) < 4 for f in out_fills) and \
                not any(abs(r["fill"] - f) < 4 for f in in_fills):
            r["label"] = "(outdoor by fill)"
    # outdoor by position: an unlabelled room bounded against the outside by lines (a railing), not walls
    in_free = {L for g in free for L in g}
    outside = set(range(1, seg["n"] + 1)) - set(owner) - in_free - set(strips)
    contact = collections.Counter()
    for (a, b), (c, _) in seg["edges"].items():
        for x, y in ((a, b), (b, a)):
            if x in owner and y in outside:
                contact[owner[x]] += c
    for r in rooms:
        if not r["label"] and not r["number"].startswith("?") and contact[r["number"]] * mpp >= OUT_CONTACT_M:
            r["label"] = "(outdoor: open to the outside)"
    return rooms, dict(scale=scale_info, thr=thr, n_strips=len(strips), mpp=mpp)


def load(path):
    im = Image.open(path)
    dpi = im.info.get("dpi")
    return np.asarray(im.convert("RGB")).astype(np.float32), (float(dpi[0]) if dpi else None)


# ------------------------------------------------------------------ Layer 2
def dataset_root() -> str:
    return os.environ.get("JSTORE_DATASET_DIR", "")


def interpret(db: DB, f: dict) -> dict:
    """Rooms and apartments of one raster file.  -> merge() counts"""
    from shapely.geometry import box
    by = "jstore.interpret.raster"
    path = os.path.join(dataset_root(), f["path"])
    if not os.path.exists(path):
        define(db, f["file_id"], "room", "reader", dict(source="raster", created=0,
               reason=f"image not found below JSTORE_DATASET_DIR ({f['path']})"), 0.0, by)
        return dict(new=0, matched=0, unlinked=0)
    rooms, meta = read_rooms(path)
    mpp = meta["mpp"]
    region, kid = DEBUG["region"], DEBUG["kid"]
    boxes = ndi.find_objects(region)
    frame = db.one("select m, method from jstore.frame where file_id = :f and page_no = 0", f=f["file_id"])
    m = list(frame["m"]) if frame else [mpp, 0, 0, 0, -mpp, 0]            # y is down in an image
    numbered = [r for r in rooms if not r["number"].startswith("?")]
    printed = [r for r in numbered if r["printed"]]
    d_units = define(db, f["file_id"], "units", "reader", dict(
        metres_per_pixel=mpp, how=meta["scale"], rule="scale text 1:N with the paper size over the image size; "
        "DPI of the file as fallback"), 0.7, by)
    d_room = define(db, f["file_id"], "room", "reader", dict(
        source="raster", reader="free-space partition with line-strength merging (png_vec v11)",
        rule="a room is the free space around a room number read by OCR, bounded by walls and the weakest line class",
        created=len(numbered), without_number=len(rooms) - len(numbered), printed_area_read=len(printed),
        line_threshold=meta["thr"]), 0.7 if numbered else 0.0, by)
    recs, units = [], {}
    for r in rooms:
        sl = boxes[kid[r["number"]] - 1]
        if sl is None:
            continue
        x0, y0, x1, y1 = sl[1].start, sl[0].start, sl[1].stop, sl[0].stop
        g = box(m[0] * x0 + m[1] * y0 + m[2], m[3] * x0 + m[4] * y0 + m[5],
                m[0] * x1 + m[1] * y1 + m[2], m[3] * x1 + m[4] * y1 + m[5])
        label = r["label"] if r["label"] and not r["label"].startswith("(") else None
        number = None if r["number"].startswith("?") else r["number"]
        claims = [("net_area_m2", round(float(r["measured"]), 3), None, "m2", "measured_outline", 0, 0.6)]
        if r["printed"]:
            claims.append(("net_area_m2", float(r["area"]), None, "m2", "ocr", 0, 0.8))
        indoor = lexicon.indoor(r["label"]) if r["label"] and not r["label"].startswith("(outdoor") else not r["label"]
        recs.append(dict(kind="room", ifc_class="IfcSpace", number=number, label=label, label_norm=lexicon.room_type(label),
                         zone=r["unit"], geom=g, attrs=dict(indoor=bool(indoor), geom="bounding box of the pixel region"),
                         sources=[("region", r["number"], "outline", [x0, y0, x1, y1], d_room)], claims=claims))
        if r["unit"] and number:
            units.setdefault(r["unit"], []).append(g)
    for tag, gs in sorted(units.items()):
        b = [min(g.bounds[0] for g in gs), min(g.bounds[1] for g in gs), max(g.bounds[2] for g in gs), max(g.bounds[3] for g in gs)]
        recs.append(dict(kind="apartment", ifc_class="IfcZone", number=tag, label=None, geom=box(*b), attrs={},
                         sources=[("region", f"apartment {tag}", "outline", None, d_room)], claims=[]))
    DEBUG.clear()
    return merge(db, f, recs, registered=False)
