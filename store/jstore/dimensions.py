"""Printed dimensions of a raster floor plan: the strings ("35'-1\"", "3,40", "2450") and the span each one measures.

    parse_dimension(text)          -> metres or None      one dimension string (imperial ft-in or metric)
    parse_area(text)               -> m2 or None          "12.5 m²", "150 sq ft" (an area is never a dimension)
    read_dimensions(gray, words)   -> [Dimension]         the strings on the dimension lines with their pixel spans
    scale_from_dimensions(dims)    -> (m per px, confidence, used)

How a dimension is read (read_dimensions):
  1. lines    the long straight thin strokes of the image (an opening of the ink with a long flat kernel), closed
              over gaps as wide as a dimension text, so that a chain broken by its texts is one line; vertical
              lines the same way on the image turned a quarter.  A line is fitted (y = a x + b) to its pixels, so
              that a slightly tilted phone photo still follows it.
  2. ticks    end marks of the measured spans: strokes that cross the line (ticks, slashes, extension lines) where
              the line itself is drawn, plus both ends of the line.  Text standing on the line is never a tick
              (the boxes of the OCR lines are left out).
  3. texts    every span between two neighbouring ticks takes the dimension string read in it: the OCR line whose
              centre lies over the span (in the gap of the line, or just above / below it).  A span with no
              string, or with a doubtful one (an inch-only string among ft-in ones, a cut-off reading), is read
              again from an enlarged cut-out of the span with the line erased (RapidOCR det + rec).
  4. checks   a dimension whose metres per pixel is far from the median of the others is kept but not used for
              the scale; a chain whose spans add up to an overall dimension next to it confirms the scale.

Bare integers ("340", "2450") are taken only with a `metric_hint` unit (cm, mm) derived from the plan, and
dash-only ft-in ("20-11") only when the plan is imperial (imperial=True), because both read the same as room
numbers or levels otherwise.
"""
from __future__ import annotations

import math
import os
import time
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

FT, INCH = 0.3048, 0.0254
_TR = str.maketrans({"’": "'", "‘": "'", "´": "'", "`": "'", "′": "'", "ʼ": "'",
                     "”": '"', "“": '"', "″": '"', "„": '"', "¨": '"',
                     "–": "-", "—": "-", "‒": "-", "−": "-", "~": "-", "_": "-",
                     "（": "(", "）": ")", "，": ",", "．": ".", " ": " "})
_FRAC = {"½": " 1/2", "¼": " 1/4", "¾": " 3/4", "⅛": " 1/8", "⅜": " 3/8", "⅝": " 5/8",
         "⅞": " 7/8"}
_AREA = re.compile(r"(?:m\s*[2²]|sq\.?\s*(?:ft|m|feet|meters?|metres?)|\bs\.?\s*f\.?(?![a-z])|ft\s*[2²]|square)",
                   re.I)
_INCHES = r"(?:\d{1,2}(?:\s+\d{1,2}/\d{1,2}|\.\d+)?|\d{1,2}/\d{1,2})"
# 35'-1"  35' 1"  35'1  98'-0"  7'  10'-8 1/2"   (a missing closing inch mark is common on photos)
_FTIN = re.compile(r"(?<![\d.,])(\d{1,3})\s*'\s*(?:-\s*)?(" + _INCHES + r")?\s*(?:\"|'')?(?![\d/])")
# 3 1/2"  6"   (inches alone)
_IN = re.compile(r"(?<![\d.,'/])(" + _INCHES + r")\s*(?:\"|''|in\b|inch(?:es)?\b)")
# 20-11   (ft-in written with a dash only; imperial plans only)
_DASH = re.compile(r"(?<![\d.,'/-])(\d{1,3})\s*-\s*(\d{1,2}(?:\s+\d{1,2}/\d{1,2})?)(?![\d.,'/])")
# 3.40  3,40 m  340 cm  2450 mm  12 ft
_METRIC = re.compile(r"(?<![\d.,'/])(\d{1,5}(?:[.,]\d{1,3})?)\s*(mm|cm|m|ft|feet)?(?![\da-z²'\"])", re.I)
_UNIT = {"mm": 0.001, "cm": 0.01, "m": 1.0, "ft": FT, "feet": FT}


def normalise(text: str) -> str:
    """The usual OCR confusions of dimension strings made plain: curly quotes and primes, dashes, '5*10\"' (a star
    for a foot mark), a tick read as '|' inside a number ('2|1\\'-1\"'), fractions as one character."""
    s = (text or "").translate(_TR)
    for k, v in _FRAC.items():
        s = s.replace(k, v)
    s = s.replace("''", '"')
    s = re.sub(r"(?<=\d)\s*[*°º]\s*(?=\d)", "'", s)                 # 5*10" -> 5'10"
    s = re.sub(r"(?<=\d)[|!lI](?=\d)", "", s)                        # 2|1'-1" -> 21'-1"
    s = re.sub(r"(?<=\b\d) (?=\d\s*'\s*-)", "", s)                   # 2 1'-1" (a mark erased) -> 21'-1"
    s = re.sub(r"(?<=\d)\s*['\"]\s*-\s*'", "'-", s)                  # 35''-1 / 35'-'1 doubled marks
    s = re.sub(r"(?<=\d) ?['\"]' ?-", "'-", s)
    s = re.sub(r"(?<=\d)\"(?=\s*-?\s*\d{1,2}\s*[\"'])", "'", s)       # 35"-1" (foot read as inch) -> 35'-1"
    s = re.sub(r"(?<=\d)\s*'\s*-\s*'?\s*(?=\d)", "'-", s)
    return s


def _inches(s: str | None) -> float:
    if not s:
        return 0.0
    total = 0.0
    for part in s.split():
        if "/" in part:
            a, b = part.split("/")
            total += float(a) / float(b) if float(b) else 0.0
        else:
            total += float(part)
    return total


def find_dimensions(text: str, metric_hint: str | None = None, imperial: bool = False) -> list[dict]:
    """Every dimension in one text (an OCR line may hold two of a chain).  -> [{text, metres, start, end, kind}];
    start / end are character positions in the normalised text (which has the length of the original but for
    two-character marks)."""
    s = normalise(text)
    if _AREA.search(s):
        return []
    out: list[dict] = []
    taken = np.zeros(len(s) + 1, bool)

    def add(m, metres, kind, canon=None):
        if metres is None or not (0.0 < metres < 2000.0) or taken[m.start():m.end()].any():
            return
        taken[m.start():m.end()] = True
        out.append(dict(text=canon or m.group(0).strip(" -"), metres=metres, start=m.start(), end=m.end(), kind=kind))

    for m in _FTIN.finditer(s):
        inch = _inches(m.group(2))
        if inch >= 12 or (m.group(2) is None and int(m.group(1)) == 0):
            continue
        add(m, int(m.group(1)) * FT + inch * INCH, "ft_in", f"{int(m.group(1))}'-{(m.group(2) or '0').strip()}\"")
    for m in _IN.finditer(s):
        inch = _inches(m.group(1))
        if 0 < inch < 100:
            add(m, inch * INCH, "in")
    if imperial:
        for m in _DASH.finditer(s):
            inch = _inches(m.group(2))
            if inch < 12:
                add(m, int(m.group(1)) * FT + inch * INCH, "ft_in", f"{int(m.group(1))}'-{m.group(2).strip()}\"")
    for m in _METRIC.finditer(s):
        num, unit = m.group(1), (m.group(2) or "").lower()
        if taken[m.start():m.end()].any():
            continue
        v = float(num.replace(",", "."))
        if unit:
            add(m, v * _UNIT[unit], "metric")
        elif re.search(r"[.,]\d{1,3}$", num):
            if imperial:
                continue
            if re.search(r"[.,]\d{3}$", num) and metric_hint == "mm":   # 2.450 written with a thousands mark
                add(m, float(num.replace(",", "").replace(".", "")) * 0.001, "metric")
            elif v < 100:
                add(m, v, "metric")
        elif metric_hint in _UNIT and metric_hint in ("mm", "cm") and len(num) >= 2:
            add(m, v * _UNIT[metric_hint], "metric")
    out.sort(key=lambda d: d["start"])
    return out


def parse_dimension(text: str, metric_hint: str | None = None, imperial: bool = False) -> float | None:
    """One dimension string -> metres, or None when the text is not exactly one dimension (an area, a word, two
    dimensions, a bare integer without a `metric_hint` of 'cm' or 'mm')."""
    s = normalise(text).strip(" -|.:;()[]")
    if not s or _AREA.search(s):
        return None
    found = find_dimensions(s, metric_hint, imperial)
    if len(found) != 1:
        return None
    d = found[0]
    rest = (s[:d["start"]] + s[d["end"]:]).strip(" -|.:;")
    return d["metres"] if not rest else None


def parse_area(text: str) -> float | None:
    """'12.5 m²', '12,5 m2', '150 sq ft', '150 SF', '150 ft²' -> m2; None for anything that is not an area."""
    s = normalise(text)
    m = re.search(r"(\d{1,6}(?:[.,]\d{1,3})?)\s*(m\s*[2²]|sq\.?\s*m(?:eters?|etres?)?|sq\.?\s*(?:ft|feet)|"
                  r"s\.?\s*f\.?(?![a-z])|ft\s*[2²]|square\s+(?:feet|foot|met(?:er|re)s?))", s, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    u = m.group(2).lower().replace(" ", "")
    return v if (u.startswith("m") or "m" in u and "ft" not in u and "feet" not in u and "f" not in u.replace("square", ""))\
        else v * FT * FT


# ------------------------------------------------------------------------------------------------ reading
@dataclass
class Dimension:
    text: str
    metres: float
    p0: tuple[float, float]           # the measured span on the image (pixels): tick to tick
    p1: tuple[float, float]
    px_length: float
    orientation: str                  # 'h' or 'v'
    box: tuple[float, float, float, float]   # the text
    read: str = "ocr"                 # ocr (the page OCR line) or reocr (read again from the cut-out of the span)
    chain: int = -1                   # dimensions of one line share a chain number
    used: bool = True                 # set by scale_from_dimensions
    word: dict | None = field(default=None, repr=False)
    note: str = ""                                            # a label printed with it ("Wall #1")
    ticks: list = field(default_factory=list, repr=False)     # the end marks of its chain (image px)
    span: tuple = (0, 1)                                      # which of them it measures between

    @property
    def mpp(self) -> float:
        return self.metres / self.px_length if self.px_length > 0 else float("nan")

    def as_dict(self) -> dict:
        return dict(text=self.text, metres=round(self.metres, 4), p0=[round(v, 1) for v in self.p0],
                    p1=[round(v, 1) for v in self.p1], px=round(self.px_length, 1), orientation=self.orientation,
                    box=[round(v, 1) for v in self.box], read=self.read, chain=self.chain, used=self.used,
                    note=self.note)


def _ink(gray: np.ndarray) -> np.ndarray:
    k = max(15, int(max(gray.shape) / 60) | 1)
    bg = cv2.blur(cv2.dilate(gray, np.ones((k, k), np.uint8)), (k, k))
    return (((bg.astype(np.int16) - gray.astype(np.int16)) > 45) | (gray < 70)).astype(np.uint8)


def _turn(box, W: int, H: int, vertical: bool):
    """A box of the image -> the box in the working view (the image turned a quarter clockwise for vertical
    lines: x' = H - 1 - y, y' = x)."""
    x0, y0, x1, y1 = box
    return (H - 1 - y1, x0, H - 1 - y0, x1) if vertical else (x0, y0, x1, y1)


def _back(x: float, y: float, H: int, vertical: bool) -> tuple[float, float]:
    return (y, H - 1 - x) if vertical else (x, y)


def _lines(ink: np.ndarray, h: float) -> list[dict]:
    """Long thin horizontal strokes; pieces in line with each other joined over gaps up to a dimension text wide.
    -> [{x0, x1, a, b, on (bool per column x0..x1), thick}]"""
    L = max(25, int(1.3 * h))
    flat = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, L), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(flat, connectivity=8)
    pieces = []
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        if hh > max(6, 0.35 * h) + w * 0.03:
            continue
        ys, xs = np.nonzero(lab[y:y + hh, x:x + w] == i)
        xs, ys = xs + x, ys + y
        a, b = np.polyfit(xs, ys, 1) if np.ptp(xs) > 0 else (0.0, float(np.mean(ys)))
        if abs(a) > 0.08:
            continue
        pieces.append(dict(x0=int(x), x1=int(x + w - 1), xs=xs, ys=ys, a=float(a), b=float(b)))
    pieces.sort(key=lambda p: p["x0"])
    gap, tol = 8 * h, max(2.5, 0.12 * h)
    groups: list[dict] = []
    for p in pieces:
        best = None
        for g in groups:
            d = p["x0"] - g["x1"]
            if not (-0.5 * h <= d <= gap):
                continue
            xm = (p["x0"] + g["x1"]) / 2
            dy = abs((g["a"] * xm + g["b"]) - (p["a"] * xm + p["b"]))
            if dy <= tol and (best is None or dy < best[0]):
                best = (dy, g)
        if best is None:
            groups.append(dict(parts=[p], x0=p["x0"], x1=p["x1"], a=p["a"], b=p["b"]))
        else:
            g = best[1]
            g["parts"].append(p)
            g["x1"] = max(g["x1"], p["x1"])
            xs = np.concatenate([q["xs"] for q in g["parts"]])
            ys = np.concatenate([q["ys"] for q in g["parts"]])
            g["a"], g["b"] = (float(v) for v in np.polyfit(xs, ys, 1))
    out = []
    for g in groups:
        x0, x1 = g["x0"], g["x1"]
        if x1 - x0 < 4 * h:
            continue
        xs = np.concatenate([q["xs"] for q in g["parts"]])
        on = np.zeros(x1 - x0 + 1, bool)
        on[np.unique(xs) - x0] = True
        out.append(dict(x0=int(x0), x1=int(x1), a=g["a"], b=g["b"], on=on,
                        thick=float(len(xs)) / max(1, int(on.sum()))))
    return out


def _run(ink: np.ndarray, x: int, y: int, dx: int, dy: int, limit: int) -> tuple[int, int]:
    """Rows of ink from (x, y) going (dx, dy) per step, following a stroke that wobbles by a pixel; one blank row
    is bridged.  -> (rows, sideways drift in pixels)."""
    H, W = ink.shape
    n = miss = 0
    x_start = x
    for _ in range(limit):
        y += dy
        x += dx
        if not (0 <= y < H):
            break
        hit = next((x + e for e in (0, -1, 1) if 0 <= x + e < W and ink[y, x + e]), None)
        if hit is None:
            miss += 1
            if miss > 1:
                break
            continue
        x, miss = hit, 0
        n += 1
        x_end = x
    return n, (abs(x_end - x_start) if n else 0)


def _ticks(ink: np.ndarray, ln: dict, h: float, texts: list) -> list[float]:
    """x positions of the end marks along one line (see the module docstring, step 2): a stroke crossing the line
    that is longer than a letter is tall (straight: 0.9 of a text line box, slanted: 0.75 and crossing), or an end of the line.
    `texts`: (x0, x1) spans along the line where a dimension text stands; strokes there are letters."""
    H, W = ink.shape
    x0, a, b = ln["x0"], ln["a"], ln["b"]
    half = max(1, int(round(ln["thick"] / 2)))
    lim = int(1.6 * h)
    on = ln["on"]
    near_on = cv2.dilate(on.astype(np.uint8)[None, :], np.ones((1, max(3, int(h / 3)) | 1), np.uint8))[0] > 0
    blocked = np.zeros(len(on), bool)
    for s0, s1 in texts:
        blocked[max(0, int(s0 - x0)):max(0, int(s1 - x0 + 1))] = True
    score = np.zeros(len(on), np.float32)
    for i in np.nonzero(near_on & ~blocked)[0]:
        x = int(x0 + i)
        yl = int(round(a * x + b))
        ya, yb = yl - half - 1, yl + half + 1
        if not (0 <= ya and yb < H):
            continue
        if not (ink[ya, max(0, x - 2):x + 3].any() or ink[yb, max(0, x - 2):x + 3].any()):
            continue
        best = 0.0
        for dx, need in ((0, 0.9), (1, 0.75), (-1, 0.75)):
            up, du = _run(ink, x, ya + 1, dx, -1, lim)
            dn, dd = _run(ink, x, yb - 1, -dx, 1, lim)
            if up + dn < 0.5 * h or (dx and min(up, dn) < 0.25 * h):
                continue
            leaning = du + dd >= 0.5 * (up + dn)
            if bool(dx) != leaning:
                continue                           # a slash leans, a tick stands (a letter's curve does neither)
            r = (up + dn) / h / need
            best = max(best, r + (5.0 if r >= 1.0 and not dx else 0.0))   # a standing tick before a slash
        score[i] = best
    cand = score >= 1.0
    out: list[tuple[float, float]] = []
    i = 0
    while i < len(cand):
        if cand[i]:
            j = i
            while j + 1 < len(cand) and cand[j + 1]:
                j += 1
            out.append((float(x0 + i + int(np.argmax(score[i:j + 1]))), float(score[i:j + 1].max())))
            i = j + 1
        else:
            i += 1
    ends = [float(x0 + int(np.argmax(on))), float(x0 + len(on) - 1 - int(np.argmax(on[::-1])))]
    for e in ends:
        if not out or min(abs(e - v[0]) for v in out) > max(4, 0.6 * h):
            out.append((e, 0.0))
    out.sort()
    merged: list[tuple[float, float]] = []
    for v in out:                                  # two marks closer than half a text line: the stronger one
        if merged and v[0] - merged[-1][0] < max(4, 0.5 * h):
            if v[1] > merged[-1][1]:
                merged[-1] = v
            continue
        merged.append(v)
    return [v[0] for v in merged]


_ENGINE = None


def _rec(crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """RapidOCR's recogniser alone on cut-outs that hold one text line each -> [(text, conf)]."""
    global _ENGINE
    if _ENGINE is None:
        from .ingest.raster import _get_engine
        _ENGINE = _get_engine()
    crops = [cv2.cvtColor(c, cv2.COLOR_GRAY2BGR) if c.ndim == 2 else c for c in crops]
    try:
        rec, _ = _ENGINE.text_rec(crops, False)
    except Exception:  # noqa: BLE001 - an odd cut-out must not stop the plan
        return [("", 0.0)] * len(crops)
    return [(str(r[0]), float(r[1])) for r in rec]


def _reread(view: np.ndarray, ln: dict, p0: float, p1: float, k: float, h: float, metric_hint,
            imperial, inner: list[float] | None = None) -> list[tuple[str, float, tuple, float]]:
    """Read the span p0..p1 (pixels of `view`) of a line (found at scale k) again: the line is erased, the text
    in the span is cut out tightly (the run of inked columns nearest the middle of the span) and recognised
    upright and turned half round.  -> [(text, metres, box in view pixels, score)], best first."""
    H, W = view.shape
    a, b = ln["a"], ln["b"] / k
    ym = a * (p0 + p1) / 2 + b
    X0, X1 = int(max(0, p0 + 0.12 * h)), int(min(W, p1 - 0.12 * h))
    Y0, Y1 = int(max(0, ym - 1.2 * h)), int(min(H, ym + 1.2 * h))
    if X1 - X0 < 0.6 * h or Y1 - Y0 < 0.6 * h:
        return []
    crop = view[Y0:Y1, X0:X1].copy()
    paper = int(np.percentile(crop, 90))
    half = int(round(ln["thick"] / k / 2)) + 1
    for t in inner or []:                          # marks inside the span (two spans read as one): erased
        c = int(round(t)) - X0
        crop[:, max(0, c - half - 1):max(0, c + half + 2)] = paper
    orig = crop.copy()
    on = ln["on"]
    for x in range(X0, X1):                        # erase the line where it runs (it strikes through some texts)
        j = int((x * k) - ln["x0"])
        if 0 <= j < len(on) and on[j]:
            y = int(round(a * x + b)) - Y0
            crop[max(0, y - half):max(0, y + half + 1), x - X0] = paper
    ink = (crop.astype(np.int16) < paper - 60).astype(np.uint8)
    # strokes longer than a letter is wide or tall (other lines, ticks, extension lines) are not the text
    longs = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(5, int(1.5 * h))), np.uint8)) | \
        cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(5, int(1.0 * h)), 1), np.uint8))
    longs = cv2.dilate(longs, np.ones((3, 3), np.uint8)) & ink
    crop[longs > 0] = paper
    ink = cv2.morphologyEx(ink & (1 - longs), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) > 0
    cols = ink.any(0)
    if cols.sum() < 0.3 * h:
        return []
    runs, i, n = [], 0, len(cols)
    gap = max(3, int(0.7 * h))
    while i < n:                                   # runs of inked columns, small gaps bridged (letter spacing)
        if not cols[i]:
            i += 1
            continue
        j, last = i, i
        while j < n and j - last <= gap:
            if cols[j]:
                last = j
            j += 1
        runs.append((i, last))
        i = j
    mid = (X1 - X0) / 2
    s, e = min(runs, key=lambda r: (0 if r[0] <= mid <= r[1] else min(abs(r[0] - mid), abs(r[1] - mid))) - 0.2 * (r[1] - r[0]))
    prof = ink[:, s:e + 1].any(1)
    if e - s < 0.5 * h or not prof.any():
        return []
    rows, r = [], 0                                 # the run of inked rows nearest the line
    while r < len(prof):
        if prof[r]:
            q = r
            while q + 1 < len(prof) and (prof[q + 1] or (q + 2 < len(prof) and prof[q + 2])):
                q += 1
            rows.append((r, q))
            r = q + 1
        else:
            r += 1
    yr = ym - Y0
    r0, r1 = min(rows, key=lambda t: 0 if t[0] <= yr <= t[1] else min(abs(t[0] - yr), abs(t[1] - yr)))
    pad = int(0.25 * h)
    cuts = []
    for src in (crop, orig):                       # erasing the line can also cut the letters it runs through
        cut = src[max(0, r0 - pad):r1 + pad + 1, max(0, s - pad):e + pad + 1]
        f = 48.0 / max(8, cut.shape[0])
        cut = cv2.resize(cut, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
        cuts += [cut, cv2.rotate(cut, cv2.ROTATE_180)]
    box = (X0 + max(0, s - pad), Y0 + max(0, r0 - pad), X0 + e + pad, Y0 + r1 + pad)
    out = []
    for text, conf in _rec(cuts):
        if re.search(r"scale|=|/\d+\s*\"\s*-", text, re.I):
            continue                               # 1/8" = 1'-0" is a scale
        for d in find_dimensions(text, metric_hint, imperial):
            if d["kind"] == "in" and imperial:
                continue
            out.append((d["text"], d["metres"], box, conf + (0.3 if d["kind"] == "ft_in" else 0)))
    return sorted(out, key=lambda r: -r[3])


def _words(words) -> list[dict]:
    out = []
    for w in words:
        if isinstance(w, dict) and "src" in w and "box" in w:
            out.append(w)
            continue
        if isinstance(w, dict):
            text, box = w.get("text") or "", (w["x0"], w["y0"], w["x1"], w["y1"])
        else:
            text, box = w[0], w[1]
        out.append(dict(text=str(text), box=tuple(float(v) for v in box), src=w))
    return out


def plan_units(words) -> tuple[str | None, bool]:
    """(metric_hint, imperial) of a plan from all its texts: imperial when ft-in strings are found; else 'mm' / 'cm'
    when a unit statement or a 4-digit-dimension habit says so; else None."""
    texts = [w["text"] for w in _words(words)]
    ftin = sum(1 for t in texts for d in find_dimensions(t) if d["kind"] == "ft_in")
    if ftin >= 2:
        return None, True
    joined = " ".join(texts).lower()
    if re.search(r"(?:dimensions?|mitat|maße|masse|cotes|units?)[^.]{0,30}\bmm\b|\bin mm\b|\(mm\)", joined):
        return "mm", False
    if re.search(r"(?:dimensions?|mitat|maße|masse|cotes|units?)[^.]{0,30}\bcm\b|\bin cm\b|\(cm\)", joined):
        return "cm", False
    return None, False


def read_dimensions(image: np.ndarray, words, metric_hint: str | None = None, imperial: bool | None = None,
                    reread: bool = True, max_rereads: int = 60, deadline: float | None = None) -> list[Dimension]:
    """The dimensions printed on a plan with the span each one measures (see the module docstring).
    image: grey (or BGR) pixels; words: [(text, (x0, y0, x1, y1))] or raster_word rows (dicts with text, x0..y1),
    in the pixels of `image`.  reread=False: only the given words (no OCR of cut-outs).  At `deadline` (epoch s)
    the lines not yet looked at are left (the anchored ones, those with a dimension text, come first)."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    ws = _words(words)
    hint, imp = plan_units(ws)
    metric_hint = metric_hint or hint
    imperial = imp if imperial is None else imperial

    def thin(b):
        return min(b[2] - b[0], b[3] - b[1])
    dimlike = [w for w in ws if find_dimensions(w["text"], metric_hint, imperial)]
    hs = [thin(w["box"]) for w in (dimlike or ws) if thin(w["box"]) > 3]
    h = float(np.median(hs)) if hs else max(12.0, max(H, W) / 120)    # height of a text line box
    h = min(max(h, 8.0), max(H, W) / 15)
    k = min(1.0, 4000.0 / max(H, W))                     # very large scans: lines are found on a smaller copy
    small = gray if k == 1.0 else cv2.resize(gray, (max(1, round(W * k)), max(1, round(H * k))),
                                             interpolation=cv2.INTER_AREA)
    Hs, Ws = small.shape
    ink_small = _ink(small)
    hk = h * k
    dims: list[Dimension] = []
    taken: list[tuple] = []                              # text boxes (image px) already given to a dimension
    budget = [max_rereads if reread else 0]
    chain_no = 0
    for vertical in (False, True):
        ink = cv2.rotate(ink_small, cv2.ROTATE_90_CLOCKWISE) if vertical else ink_small
        view = cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE) if vertical else gray
        boxes = [_turn(tuple(v * k for v in w["box"]), Ws, Hs, vertical) for w in ws]
        lines = _lines(ink, hk)

        def nearest(bx) -> int | None:
            """The line a text belongs to: the nearest one running through or along it."""
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            best = None
            for li, ln in enumerate(lines):
                if not (ln["x0"] - hk <= cx <= ln["x1"] + hk):
                    continue
                yl = ln["a"] * cx + ln["b"]
                if bx[1] - 0.6 * hk <= yl <= bx[3] + 0.6 * hk:
                    d = abs(yl - cy)
                    if best is None or d < best[0]:
                        best = (d, li)
            return None if best is None else best[1]

        parts = []
        for i, w in enumerate(ws):
            bx = boxes[i]
            if (bx[2] - bx[0]) < 0.9 * (bx[3] - bx[1]) and len(w["text"]) > 2:
                continue                                 # reads across the lines of this view
            n = max(1, len(w["text"]))
            if re.search(r"scale|ma(?:ss|ß)stab|[ée]chelle|=", w["text"], re.I):
                continue                                 # 1/8" = 1'-0" is a scale, not a dimension
            for d in find_dimensions(w["text"], metric_hint, imperial):
                s0 = bx[0] + (bx[2] - bx[0]) * d["start"] / n
                s1 = bx[0] + (bx[2] - bx[0]) * d["end"] / n
                parts.append(dict(d, i=i, s0=s0, s1=s1, c=(s0 + s1) / 2, line=nearest(bx)))
        anchored = {p["line"] for p in parts if p["line"] is not None}
        order = sorted(range(len(lines)), key=lambda li: (li not in anchored, -(lines[li]["x1"] - lines[li]["x0"])))
        for li in order:
            if deadline is not None and time.time() > deadline:
                break                                    # out of time: the lines read so far
            ln = lines[li]
            mine_parts = [p for p in parts if p["line"] == li]
            if not mine_parts and budget[0] <= 0:
                continue
            ticks = _ticks(ink, ln, hk, [(p["s0"], p["s1"]) for p in mine_parts])
            if len(ticks) < 2:
                continue
            chain = []
            pts = [_back(t / k, (ln["a"] * t + ln["b"]) / k, H, vertical) for t in ticks]
            done: set[int] = set()

            def reread_span(i0: int, i1: int):
                p0, p1 = ticks[i0], ticks[i1]
                budget[0] -= 1
                inner = [t / k for t in ticks[i0 + 1:i1]]
                for text, metres, bxf, _ in _reread(view, ln, p0 / k, p1 / k, k, h, metric_hint, imperial, inner):
                    bx = tuple(v * k for v in bxf)
                    if nearest(bx) != li or not (p0 - 0.3 * hk < (bx[0] + bx[2]) / 2 < p1 + 0.3 * hk):
                        continue
                    if any(_overlap(_box_back(bx, k, H, vertical), t) > 0.3 for t in taken):
                        continue
                    return (text, metres, bx, "reocr", None)
                return None

            def emit(got, i0: int, i1: int) -> None:
                text, metres, bx, how, src = got
                img_box = _box_back(bx, k, H, vertical)
                taken.append(img_box)
                done.update(range(i0, i1))
                chain.append(Dimension(text=text, metres=metres, p0=pts[i0], p1=pts[i1],
                                       px_length=math.dist(pts[i0], pts[i1]), orientation="v" if vertical else "h",
                                       box=img_box, read=how, chain=chain_no,
                                       word=src if isinstance(src, dict) else None, ticks=pts, span=(i0, i1)))

            for ti, (p0, p1) in enumerate(zip(ticks[:-1], ticks[1:])):
                if p1 - p0 < 1.2 * hk:
                    continue
                mine = [p for p in mine_parts if p0 < p["c"] < p1]
                if len(mine) > 1:
                    continue                             # a tick between them was not found
                got = None
                if mine:
                    p = mine[0]
                    doubtful = (imperial and p["kind"] != "ft_in") or ws[p["i"]]["text"].count("'") > 2
                    if not doubtful:
                        got = (p["text"], p["metres"], _part_box(p, boxes), "ocr", ws[p["i"]]["src"])
                if got is None and budget[0] > 0 and (mine or _texty(ink, ln, p0, p1, hk)):
                    got = reread_span(ti, ti + 1)
                if got is None and mine and not (imperial and mine[0]["kind"] == "in"):
                    p = mine[0]
                    got = (p["text"], p["metres"], _part_box(p, boxes), "ocr", ws[p["i"]]["src"])
                if got is not None:
                    emit(got, ti, ti + 1)
            # a mark that runs through a text (an extension line crossing "2|1'-1\""): two spans left without a
            # dimension are read again as one, with the mark between them erased
            for ti in range(len(ticks) - 2):
                if budget[0] <= 0:
                    break
                if ti in done or ti + 1 in done or not _texty(ink, ln, ticks[ti], ticks[ti + 2], hk):
                    continue
                if any(ticks[ti] < p["c"] < ticks[ti + 2] for p in mine_parts if p["kind"] == "ft_in"):
                    continue
                got = reread_span(ti, ti + 2)
                if got is not None:
                    emit(got, ti, ti + 2)
            chain.sort(key=lambda d: d.span)
            if chain:
                dims.extend(chain)
                chain_no += 1
    dims = _dedupe(dims)
    _resnap(dims)
    return dims


def _resnap(dims: list[Dimension], tol: float = 0.05) -> None:
    """A dimension whose span disagrees with the others (a wall or a leader crossing the dimension line was taken
    for a tick) is measured again over the neighbouring spans of its chain that hold no dimension of their own,
    when that brings it within `tol` of the median metres per pixel of the others."""
    if len(dims) < 3:
        return
    med = float(np.median([d.mpp for d in dims if d.px_length > 0]))
    for d in dims:
        if not d.ticks or abs(d.mpp / med - 1) <= tol:
            continue
        busy = {i for o in dims if o.chain == d.chain and o is not d for i in range(o.span[0], o.span[1])}
        best = None
        i0, i1 = d.span
        for lo in range(i0, max(-1, i0 - 3), -1):
            if lo < i0 and lo in busy:
                break
            for hi in range(i1, min(len(d.ticks), i1 + 3)):
                if hi > i1 and (hi - 1) in busy:
                    break
                L = math.dist(d.ticks[lo], d.ticks[hi])
                err = abs(d.metres / L / med - 1) if L > 0 else 9.0
                if best is None or err < best[0]:
                    best = (err, lo, hi, L)
        if best and best[0] <= tol and (best[1], best[2]) != d.span:
            _, lo, hi, L = best
            d.span, d.p0, d.p1, d.px_length = (lo, hi), d.ticks[lo], d.ticks[hi], L


def _part_box(p: dict, boxes: list) -> tuple:
    """The box of the part of an OCR line that holds one dimension (by its characters)."""
    b = boxes[p["i"]]
    return (max(b[0], p["s0"]), b[1], min(b[2], p["s1"]), b[3]) if p["s1"] - p["s0"] > 2 else b


def _box_back(bx, k: float, H: int, vertical: bool) -> tuple:
    a = _back(bx[0] / k, bx[1] / k, H, vertical)
    b = _back(bx[2] / k, bx[3] / k, H, vertical)
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _texty(ink: np.ndarray, ln: dict, p0: float, p1: float, h: float) -> bool:
    """Is there something like a text in the span: ink in many columns just above, on or below the line?"""
    H, W = ink.shape
    X0, X1 = int(p0 + 0.2 * h), int(p1 - 0.2 * h)
    if X1 - X0 < 0.6 * h:
        return False
    ym = ln["a"] * (p0 + p1) / 2 + ln["b"]
    half = int(round(ln["thick"] / 2)) + 1
    best = 0.0
    for lo, hi in ((ym - 1.1 * h, ym - half - 1), (ym + half + 1, ym + 1.1 * h)):
        band = ink[int(max(0, lo)):int(min(H, hi)), X0:X1]
        if band.size:
            best = max(best, float(band.any(0).mean()))
    return best >= 0.12


def _dedupe(dims: list[Dimension]) -> list[Dimension]:
    """One text measured twice (two lines found through it): keep the shorter-tolerance one = the first by text box."""
    out: list[Dimension] = []
    for d in dims:
        dup = next((o for o in out if o.orientation == d.orientation and abs(o.metres - d.metres) < 1e-6 and
                    _overlap(o.box, d.box) > 0.5), None)
        if dup is None:
            out.append(d)
    return out


def _overlap(a, b) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


# -------------------------------------------------------------------------------------------------- scale
def scale_from_dimensions(dims: list[Dimension], tol: float = 0.05) -> tuple[float | None, float, list[Dimension]]:
    """Metres per pixel from the dimensions: the median of metres / span, those within `tol` of it kept (the others
    get used=False), then the median of the kept.  A chain whose spans add up to a parallel overall dimension
    (within 1.5 %) confirms it.  -> (m per px or None, confidence 0-0.85, the dimensions used)."""
    good = [d for d in dims if d.px_length > 0 and d.metres > 0 and d.read != "gemini_ends"]
    if not good:
        return None, 0.0, []
    # long spans are measured more precisely: weight the first guess towards them
    v = np.array([d.mpp for d in good])
    med = float(np.median(v))
    for _ in range(2):
        kept = [d for d in good if abs(d.mpp / med - 1) <= tol]
        if not kept:
            break
        med = float(np.median([d.mpp for d in kept]))
    kept = [d for d in good if abs(d.mpp / med - 1) <= tol]
    for d in dims:
        d.used = any(d is k for k in kept)
    if not kept:
        return None, 0.0, []
    total_m = sum(d.metres for d in kept)
    total_px = sum(d.px_length for d in kept)
    mpp = total_m / total_px                              # length-weighted: the long spans count most
    spread = float(np.std([d.mpp for d in kept]) / mpp) if len(kept) > 1 else 1.0
    chains = _chain_checks(kept)
    n = len(kept)
    conf = 0.35 if n == 1 else 0.5 if n == 2 else 0.6 if n < 5 else 0.7
    if spread > 0.03:
        conf -= 0.1
    if chains["confirmed"]:
        conf += 0.15
    if len(good) >= 3 and n < 0.5 * len(good):
        conf = min(conf, 0.3)                             # most dimensions disagree with each other
    return mpp, round(max(0.1, min(0.85, conf)), 2), kept


def _chain_checks(dims: list[Dimension]) -> dict:
    """Overall dimensions that equal the sum of a run of a parallel chain: {'confirmed': n, 'pairs': [...]}."""
    pairs = []
    by_chain: dict[int, list[Dimension]] = {}
    for d in dims:
        by_chain.setdefault(d.chain, []).append(d)
    for o in dims:
        for c, segs in by_chain.items():
            if c == o.chain or segs[0].orientation != o.orientation or len(segs) < 2:
                continue
            ax = 0 if o.orientation == "h" else 1
            lo, hi = sorted((o.p0[ax], o.p1[ax]))
            inside = [s for s in segs if min(s.p0[ax], s.p1[ax]) >= lo - 0.02 * (hi - lo)
                      and max(s.p0[ax], s.p1[ax]) <= hi + 0.02 * (hi - lo)]
            if len(inside) >= 2:
                cov = sum(s.px_length for s in inside) / max(1e-6, hi - lo)
                tot = sum(s.metres for s in inside)
                if cov > 0.97 and abs(tot / o.metres - 1) < 0.015:
                    pairs.append(dict(overall=o.text, parts=[s.text for s in inside]))
    return dict(confirmed=len(pairs), pairs=pairs)


# ------------------------------------------------------------------------------------------------- Gemini
GEMINI_MODEL = os.environ.get("JSTORE_DIMENSIONS_MODEL", "gemini-4-carbon")
_SCHEMA = {"type": "object", "properties": {"dimensions": {"type": "array", "items": {
    "type": "object", "properties": {
        "text": {"type": "string"}, "note": {"type": "string"},
        "p0": {"type": "array", "items": {"type": "integer"}},
        "p1": {"type": "array", "items": {"type": "integer"}}},
    "required": ["text", "p0", "p1"]}}}, "required": ["dimensions"]}
_PROMPT = (
    "The image is an architectural floor plan (possibly a phone photo of a paper print). List EVERY dimension "
    "string printed on it: the length texts written on dimension lines (e.g. 35'-1\", 9'-2\", 3.40, 2450) and "
    "lengths written along a wall with marks at both ends (e.g. 20'-11\" (Wall #1)). For each one:\n"
    "- text: the length exactly as printed (only the length, without a note such as '(Wall #1)'); keep the foot "
    "and inch marks.\n"
    "- note: a label printed with this length that names what it measures (e.g. 'Wall #1'), else ''.\n"
    "- p0, p1: the two end marks (ticks, slashes, arrow tips or extension lines) of the span this length "
    "measures, as [y, x] integers normalised to 0-1000 of this image. For a chain of dimensions the end mark "
    "between two neighbours is shared.\n"
    "Do not list room areas, scales (1/8\" = 1'-0\"), room numbers or levels.")


def gemini_dimensions(image: np.ndarray, deadline: float | None = None) -> dict:
    """One Gemini call (cached by the image bytes and the prompt): the dimension strings with the approximate
    ends of their spans.  -> {dims: [{text, p0, p1 (image px)}], cached, model} or {error}."""
    from PIL import Image
    from .interpret.raster_rooms import ask
    rgb = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    im = Image.fromarray(rgb)
    H, W = rgb.shape[:2]
    if max(W, H) > 3072:
        f = 3072 / max(W, H)
        im = im.resize((round(W * f), round(H * f)), Image.LANCZOS)
    res = ask(im, _PROMPT, _SCHEMA, GEMINI_MODEL, deadline or (time.time() + 120), thinking="low")
    if "error" in res:
        return dict(error=res["error"])
    out = []
    for d in (res.get("data") or {}).get("dimensions") or []:
        try:
            (y0, x0), (y1, x1) = d["p0"][:2], d["p1"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        out.append(dict(text=str(d.get("text") or ""), note=str(d.get("note") or "").strip(), p0=(x0 / 1000 * W, y0 / 1000 * H),
                        p1=(x1 / 1000 * W, y1 / 1000 * H)))
    return dict(dims=out, cached=bool(res.get("cached")), model=GEMINI_MODEL)


def add_gemini(dims: list[Dimension], g: list[dict], shape: tuple, metric_hint: str | None = None,
               imperial: bool | None = None) -> dict:
    """Merge the Gemini dimensions into those read in the pixels.  Each Gemini end is snapped onto the nearest end
    mark of a chain found in the pixels (within 0.6 % of the image or 5 % of the span).  A span read already
    with the same value is confirmed (and takes the Gemini ends when they snap and its own did not agree); one
    read with another value keeps whichever value fits the scale of the others better; a span not read is added
    (read 'gemini').  A Gemini span whose ends do not land on marks is added as 'gemini_ends' (its ends as Gemini
    gave them) and is never used for the scale.  -> counts."""
    H, W = shape[:2]
    if imperial is None:
        imperial = any("'" in d.text for d in dims) or \
            sum(1 for x in g for f in find_dimensions(x["text"]) if f["kind"] == "ft_in") >= 2
    chains: dict[int, list] = {}
    orient: dict[int, str] = {}
    for d in dims:
        chains.setdefault(d.chain, d.ticks)
        orient.setdefault(d.chain, d.orientation)
    good = [d for d in dims if d.px_length > 0]
    med = float(np.median([d.mpp for d in good])) if len(good) >= 3 else None
    counts = dict(gemini=len(g), confirmed=0, corrected=0, added=0, unsnapped=0)
    next_chain = max(chains, default=-1) + 1
    for x in g:
        val = parse_dimension(x["text"], metric_hint, imperial)
        if val is None:
            found = find_dimensions(x["text"], metric_hint, imperial)
            val = found[0]["metres"] if len(found) == 1 else None
        L_g = math.dist(x["p0"], x["p1"])
        if val is None or L_g < 4:
            continue
        tol = max(0.006 * max(W, H), 0.05 * L_g)
        best = None
        for c, ticks in chains.items():
            if len(ticks) < 2:
                continue
            i0 = min(range(len(ticks)), key=lambda i: math.dist(ticks[i], x["p0"]))
            i1 = min(range(len(ticks)), key=lambda i: math.dist(ticks[i], x["p1"]))
            e0, e1 = math.dist(ticks[i0], x["p0"]), math.dist(ticks[i1], x["p1"])
            if i0 != i1 and max(e0, e1) <= tol and (best is None or e0 + e1 < best[0]):
                best = (e0 + e1, c, min(i0, i1), max(i0, i1))
        if best is not None:
            _, c, i0, i1 = best
            ticks = chains[c]
            p0, p1, span, how = ticks[i0], ticks[i1], (i0, i1), "gemini"
        else:
            c, ticks, span, how = None, [], (0, 1), "gemini_ends"
            p0, p1 = tuple(x["p0"]), tuple(x["p1"])
            counts["unsnapped"] += 1
        L = math.dist(p0, p1)
        o = orient[c] if c is not None else ("h" if abs(p1[0] - p0[0]) >= abs(p1[1] - p0[1]) else "v")

        def overlap(d) -> float:
            """Share of the shorter of the two spans that the other covers (both on one line)."""
            ax = 0 if o == "h" else 1
            if d.orientation != o or abs((d.p0[1 - ax] + d.p1[1 - ax]) / 2 - (p0[1 - ax] + p1[1 - ax]) / 2) > \
                    0.006 * max(W, H):
                return 0.0
            a0, a1 = sorted((d.p0[ax], d.p1[ax]))
            b0, b1 = sorted((p0[ax], p1[ax]))
            return max(0.0, min(a1, b1) - max(a0, b0)) / max(1e-6, min(a1 - a0, b1 - b0))

        olap = [d for d in dims if overlap(d) > 0.5]
        same = next((d for d in olap if abs(d.metres - val) < 0.01), None)
        if same is not None:
            counts["confirmed"] += 1
            same.note = same.note or x.get("note") or ""
            if "gemini" not in same.read:
                same.read += "+gemini"
            if med and abs(val / L / med - 1) < abs(same.mpp / med - 1) and \
                    (how == "gemini" or abs(same.mpp / med - 1) > 0.05):
                if how != "gemini":
                    same.read = "gemini_ends"
                same.p0, same.p1, same.px_length, same.ticks = p0, p1, L, ticks
                if c is not None:
                    same.chain, same.span = c, span
            continue
        if olap:
            d = max(olap, key=overlap)
            if how == "gemini" and med and abs(val / L / med - 1) <= 0.05 and \
                    abs(val / L / med - 1) < abs(d.mpp / med - 1):
                d.text, d.metres, d.read, d.note = x["text"], val, "gemini", x.get("note") or d.note
                d.p0, d.p1, d.px_length, d.chain, d.ticks, d.span = p0, p1, L, c, ticks, span
                counts["corrected"] += 1
            continue
        if c is None:
            c = next_chain
            next_chain += 1
        xs, ys = (x["p0"][0], x["p1"][0]), (x["p0"][1], x["p1"][1])
        pad = 0.01 * max(W, H)                             # no text box from Gemini: the span, a little widened
        dims.append(Dimension(text=x["text"], metres=val, p0=p0, p1=p1, px_length=L, orientation=o,
                              box=(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad), read=how, chain=c,
                              note=x.get("note") or "", ticks=ticks, span=span,
                              used=how == "gemini"))
        counts["added"] += 1
    return counts


_NOTE = re.compile(r"\(?\s*((?:wall|mur|wand|seinä|muro)\s*(?:#|no\.?|nr\.?)?\s*\d{1,3})\s*\)?", re.IGNORECASE)


def attach_notes(dims: list[Dimension], words) -> None:
    """A label like '(Wall #1)' printed in or right next to a dimension's text becomes its note."""
    ws = _words(words)
    for d in dims:
        if d.note:
            continue
        x0, y0, x1, y1 = d.box
        h = max(4.0, min(x1 - x0, y1 - y0))
        for w in ws:
            b = w["box"]
            if b[0] > x1 + 2.5 * h or b[2] < x0 - 2.5 * h or b[1] > y1 + h or b[3] < y0 - h:
                continue
            m = _NOTE.search(w["text"])
            if m:
                d.note = re.sub(r"\s+", " ", m.group(1)).strip()
                break
