"""Doors, windows, sanitary fixtures and stairs on the image of a plan page, from Gemini.  No database here.

    detect(image) -> {plan, triage, areas, views, candidates, objects, calls, cached, seconds, errors, detector}

    candidates  every box any view returned, merged across views, each with `kept` and the reason
    objects     the kept ones, [{label, box: [x0, y0, x1, y1] in pixels of the image given, score, view}]

How (prompt version in PROMPT_VERSION; every step is one cached API request):
  1. triage     gemini-decision-1 on a thumbnail: is this a floor plan at all?  A page that is not is left alone.
  2. layout     Carbon on the whole page: the box of every floor plan on the sheet (title block, legend, notes,
                sections and elevations are left out) and how wide a door is drawn.  Fallback: the ink box
                inside the sheet border (`drawing_area`).
  3. views      each plan area as a whole, plus a grid of overlapping tiles sized so that a door is about
                DOOR_PX pixels wide in the view sent (a dense school plan gets many tiles, an apartment 2x2).
                A view that answers with MAX_OBJECTS or more boxes is cut 2x2 and asked again.
  4. detect     Carbon per view, boxes per class (box_2d on the 0-1000 grid of the view), all views in parallel.
  5. merge      boxes of the same thing from overlapping views become one candidate that counts its votes.
  6. verify     gemini-decision-1 on a crop around every candidate with the box drawn in: which symbol is in the
                box, or none?  A candidate is dropped when the verifier calls it nothing.  Its opinion on the
                class is stored but not used: it confuses ladder-like windows with stairs, Carbon does not.

Responses are cached under <store>/cache/gemini/ keyed by the bytes sent, so a repeated run costs nothing.
Requests have timeouts, are retried, and stop at the deadline the caller gives; what is missing then is listed
in `errors`.  The key is read from GEMINI_API_KEY and never logged or stored.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import hashlib
import io
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MODEL = os.environ.get("JSTORE_GEMINI_MODEL", "gemini-4-carbon")
DECISION_MODEL = os.environ.get("JSTORE_GEMINI_DECISION_MODEL", "gemini-decision-1")
THINKING = os.environ.get("JSTORE_GEMINI_THINKING", "low")       # boxes do not need more (measured)
PROMPT_VERSION = "sym-3"
ROOT = os.environ.get("JSTORE_GEMINI_ROOT", "https://generativelanguage.googleapis.com/v1beta")
FIXTURES = ["toilet", "washbasin", "sink", "bathtub", "shower"]
LABELS = ["door", "window"] + FIXTURES + ["stair"]
MAX_OBJECTS = 45          # per view; this many or more and the view is cut 2x2 and asked again
DOOR_PX = 56              # how wide a door should be in the view sent
VIEW_SIDE = 1400          # long side of a view sent, pixels
MAX_UPSCALE = 4.0
MAX_TILES = 49            # per plan area
OVERLAP = 0.22            # of a tile's side
PAGE_SIDE = 1536          # long side of the page image sent for triage and layout
CONCURRENCY = int(os.environ.get("JSTORE_GEMINI_CONCURRENCY", "16"))
CALL_TIMEOUT = float(os.environ.get("JSTORE_GEMINI_TIMEOUT_S", "35"))
PLAN_MIN = 0.15           # P(floor plan) below this: not a plan page
REJECT_NONE = 0.6         # verifier's P(nothing of the kind in the box) from which a candidate is dropped
Image.MAX_IMAGE_PIXELS = None

DETECT_PROMPT = (
    "The image shows an architectural floor plan, or a part of one cut out of a larger sheet. Detect EVERY door, "
    "EVERY window, EVERY sanitary fixture and EVERY stair drawn in the plan.\n"
    "- door: one door in a wall opening: the gap in the wall with its leaf and swing arc (quarter circle), a "
    "sliding or folding door, or a plain doorway drawn with a threshold line. A double door (two leaves in one "
    "opening) is ONE door. Box the opening together with its leaf and arc. Wardrobe and cupboard fronts are not "
    "doors.\n"
    "- window: one glazed opening in a wall, drawn as thin parallel lines (or a thin frame) inside the wall "
    "thickness. Box the stretch of wall it occupies. A window divided by mullions is one window; windows in "
    "separate openings are separate. Drawing styles differ: the lines may run along the wall or across it like "
    "a short ladder, and small windows (bathroom, stair, basement) count as much as large ones - check every "
    "interruption of an outer wall.\n"
    "- toilet: a WC pan (oval bowl with a cistern).  - washbasin: a hand basin in a bathroom or WC.\n"
    "- sink: a kitchen or utility sink (rectangular bowl or bowls, usually in a worktop).\n"
    "- bathtub: a bath (long rounded basin in a rectangle).  - shower: a shower tray or cubicle (square or "
    "quarter circle, often with a cross or a drain).\n"
    "- stair: one flight or staircase (a run of parallel treads in a room or stair hall, wider than a door), "
    "boxed as a whole. A short band of parallel lines that sits INSIDE a wall is a window, never a stair.\n"
    "Only what is drawn in the plan counts: ignore legends, title blocks, tables, text, dimensions, furniture, "
    "appliances, sections and elevations. Include an object cut by the image border when at least half of it is "
    "visible. Work through the image systematically, room by room and wall by wall, so that nothing is skipped; "
    "do not report the same object twice and do not invent objects that are not drawn.\n"
    "Return one entry per object: label and box_2d = [ymin, xmin, ymax, xmax], integers 0-1000 relative to this "
    "image, tight around the symbol."
)
DETECT_SCHEMA = {
    "type": "object",
    "properties": {"objects": {"type": "array", "items": {
        "type": "object",
        "properties": {"label": {"type": "string", "enum": LABELS},
                       "box_2d": {"type": "array", "items": {"type": "integer"}}},
        "required": ["label", "box_2d"]}}},
    "required": ["objects"],
}
LAYOUT_PROMPT = (
    "This is a sheet of an architectural drawing set (a CAD export or a scan). Find every FLOOR PLAN drawn on it: "
    "a horizontal cut through a storey that shows walls, rooms, doors and windows. For each floor plan return "
    "box_2d = [ymin, xmin, ymax, xmax] (integers 0-1000 relative to this image) around the whole building "
    "outline of that plan including its exterior walls, balconies and stairs. Leave out the title block, "
    "legends, tables, notes, the sheet border, and any section, elevation, site plan, roof plan or detail. "
    "Several plans on one sheet (for example two storeys) are separate entries. Also give door_width: the "
    "width of a typical single door opening of that plan, in the same 0-1000 units measured along the image "
    "width (for example 12 when a door is 1.2 % of the image width). If the sheet shows no floor plan, return "
    "an empty list."
)
LAYOUT_SCHEMA = {
    "type": "object",
    "properties": {"plans": {"type": "array", "items": {
        "type": "object",
        "properties": {"box_2d": {"type": "array", "items": {"type": "integer"}},
                       "door_width": {"type": "number"}},
        "required": ["box_2d", "door_width"]}}},
    "required": ["plans"],
}
TRIAGE_QUESTIONS = {
    "floor_plan": {"type": "noul", "instructions":
                   "Does the image contain at least one architectural floor plan (a horizontal cut through a "
                   "building storey showing walls and rooms from above)? A sheet that shows only sections, "
                   "elevations, details, a site plan, a table, text or a photograph is a no."},
    "kind": {"type": "choice", "instructions": "What is the main content of this sheet?",
             "criteria": {"floor_plan": "Plan view of a building storey with rooms, walls and doors.",
                          "section_or_elevation": "Vertical cut through a building or a facade view.",
                          "site_plan": "Plot, surroundings, roads, roof plan seen from far.",
                          "detail": "Enlarged construction detail or diagram.",
                          "text_or_table": "Mainly text, schedules or tables.",
                          "picture": "Photograph, perspective, sketch or anything else."}},
}
VERIFY_CRITERIA = {
    "door": "A door or doorway in a wall: an opening with a door leaf and/or swing arc, a sliding or folding "
            "door, or a plain opening with a threshold.",
    "window": "A window: thin parallel lines or a thin frame inside the thickness of a wall.",
    "toilet": "A toilet / WC pan.",
    "washbasin": "A hand washbasin of a bathroom or WC.",
    "sink": "A kitchen or utility sink.",
    "bathtub": "A bathtub.",
    "shower": "A shower tray or shower cubicle.",
    "stair": "A stair: a run of parallel treads.",
    "zz_nothing": "None of these: plain wall, empty floor, text, a dimension, furniture, an appliance, a "
                  "column, a legend entry or anything else.",
}
VERIFY_INSTRUCTIONS = ("The image is a cut-out of an architectural floor plan. A red rectangle marks one spot. "
                       "Which plan symbol is drawn inside the red rectangle?")

_slots = threading.Semaphore(CONCURRENCY)
_local = threading.local()


class Stats:
    def __init__(self, deadline: float | None = None):
        self.deadline = deadline
        self.phase_end: float | None = None                 # an earlier stop for the current step
        self.calls = self.cached = 0
        self.by_model: dict[str, int] = {}
        self.errors: list[str] = []
        self.lock = threading.Lock()

    def left(self) -> float:
        end = min(self.deadline or math.inf, self.phase_end or math.inf)
        return end - time.time()

    def count(self, model: str, cached: bool) -> None:
        with self.lock:
            if cached:
                self.cached += 1
            else:
                self.calls += 1
                self.by_model[model] = self.by_model.get(model, 0) + 1

    def error(self, text: str) -> None:
        with self.lock:
            if len(self.errors) < 40:
                self.errors.append(text[:300])


# ------------------------------------------------------------------ requests and cache
def _cache_dir() -> Path:
    from .pipeline import store_dir
    d = store_dir() / "cache" / "gemini"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached(key: str):
    path = _cache_dir() / key[:2] / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            return None
    return None


def _store(key: str, rec: dict) -> None:
    path = _cache_dir() / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, path)


def _post(path: str, body: dict, stats: Stats, timeout: float, retries: int = 3) -> tuple[int, dict]:
    """One request with retries on timeouts, stalls and server errors.  -> (http status, parsed body)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return -2, {"error": {"message": "GEMINI_API_KEY is not set"}}
    data = json.dumps(body).encode()
    status, out = -1, {"error": {"message": "no time left"}}
    for attempt in range(retries):
        left = stats.left()
        if left < 2:
            break
        req = urllib.request.Request(ROOT + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with _slots:
                with urllib.request.urlopen(req, timeout=max(2.0, min(timeout, left))) as r:
                    status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - timeout, connection reset
            status, text = -1, json.dumps({"error": {"message": type(e).__name__}})
        try:
            out = json.loads(text)
        except ValueError:
            out = {"error": {"message": text[:300]}}
        if status == 200 or status not in (-1, 408, 429, 500, 502, 503, 504):
            break
        if attempt + 1 < retries:
            time.sleep(min(max(0.0, stats.left() - 2), (1.0 if status == -1 else 2.0 * 2 ** attempt) + random.random()))
    return status, out


def _err(status: int, js) -> str:
    e = js.get("error") if isinstance(js, dict) else None
    return f"http {status}: {(e.get('message') or '')[:200] if isinstance(e, dict) else ''}"


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def generate(im: Image.Image, prompt: str, schema: dict, stats: Stats, model: str = MODEL, tag: str = "",
             timeout: float = CALL_TIMEOUT) -> dict | None:
    """Structured answer to one image + prompt (generateContent, JSON schema).  None on failure."""
    png = _png(im)
    key = hashlib.sha256(png + f"|{model}|{THINKING}|{PROMPT_VERSION}|{prompt}|{json.dumps(schema, sort_keys=True)}"
                         .encode()).hexdigest()
    hit = _cached(key)
    if hit is not None:
        stats.count(model, True)
        return hit["answer"]
    gen: dict = {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}}
    if THINKING and THINKING != "default":
        gen["thinkingConfig"] = {"thinkingLevel": THINKING}
    body = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}}, {"text": prompt}]}],
        "generationConfig": gen}
    t = time.time()
    answer, why = None, ""
    for _ in range(2):                                   # an unreadable answer is asked once more
        status, js = _post(f"/models/{model}:generateContent", body, stats, timeout)
        if status != -2:
            stats.count(model, False)
        if status != 200:
            why = _err(status, js)
            break
        try:
            parts = js["candidates"][0]["content"]["parts"]
            answer = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            break
        except (KeyError, IndexError, ValueError, TypeError) as e:
            why = f"unreadable answer: {type(e).__name__}"
    if answer is None:
        stats.error(f"{tag or model}: {why}")
        return None
    _store(key, dict(model=model, prompt_version=PROMPT_VERSION, answer=answer, seconds=round(time.time() - t, 1),
                     usage=js.get("usageMetadata"), view_px=list(im.size)))
    return answer


def decide(im: Image.Image, questions: dict, stats: Stats, tag: str = "", timeout: float = 20) -> dict | None:
    """Answers of gemini-decision-1 to typed questions about one image.  None on failure."""
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=6)
    png = buf.getvalue()
    key = hashlib.sha256(png + f"|{DECISION_MODEL}|{PROMPT_VERSION}|{json.dumps(questions, sort_keys=True)}"
                         .encode()).hexdigest()
    hit = _cached(key)
    if hit is not None:
        stats.count(DECISION_MODEL, True)
        return hit["answer"]
    body = {"model": DECISION_MODEL, "questions": questions,
            "input": [{"type": "image", "data": base64.b64encode(png).decode(), "mime_type": "image/png"}]}
    status, js = _post("/decisions", body, stats, timeout)
    if status != -2:
        stats.count(DECISION_MODEL, False)
    if status != 200 or not isinstance(js.get("answers"), dict):
        stats.error(f"{tag or DECISION_MODEL}: {_err(status, js)}")
        return None
    _store(key, dict(model=DECISION_MODEL, prompt_version=PROMPT_VERSION, answer=js["answers"]))
    return js["answers"]


# ------------------------------------------------------------------ drawing area without a model (fallback)
def _runs(idx: np.ndarray) -> list[tuple[int, int]]:
    """Consecutive index runs -> [(first, last)]."""
    if len(idx) == 0:
        return []
    cuts = np.where(np.diff(idx) > 1)[0]
    starts = np.concatenate([[idx[0]], idx[cuts + 1]])
    ends = np.concatenate([idx[cuts], [idx[-1]]])
    return list(zip(starts.tolist(), ends.tolist()))


def drawing_area(im: Image.Image, margin: int = 40) -> tuple[int, int, int, int]:
    """Where the drawing is: (x0, y0, x1, y1) in pixels.

    A sheet has a thin ruled border with white paper just inside it, and ruled lines across the full border
    width that split off the title block or legend; the drawing is the largest cell.  An exterior wall is not
    taken for a border: it is thicker, and other walls meet it from the inside."""
    g = np.asarray(im.convert("L"))
    H, W = g.shape
    dark = g < 128
    thin = max(3, round(0.004 * max(W, H)))

    def rules(a: np.ndarray, span: float) -> list[tuple[int, int]]:
        frac = a.mean(axis=1)
        return [r for r in _runs(np.where(frac >= span)[0]) if r[1] - r[0] + 1 <= thin]

    def blank_inside(a: np.ndarray, run: tuple[int, int], inward: int) -> bool:
        band = a[run[1] + 2: run[1] + 2 + thin] if inward > 0 else a[max(0, run[0] - 1 - thin): max(0, run[0] - 1)]
        return band.size > 0 and band.mean() < 0.01

    x0, y0, x1, y1 = 0, 0, W, H
    hl, vl = rules(dark, 0.6), rules(dark.T, 0.4)
    top = [r for r in hl if r[1] < 0.15 * H and blank_inside(dark, r, 1)]
    left = [r for r in vl if r[1] < 0.15 * W and blank_inside(dark.T, r, 1)]
    right = [r for r in vl if r[0] > 0.85 * W and blank_inside(dark.T, r, -1)]
    if top and left and right:
        bx0, bx1, by0 = left[-1][1] + 1, right[0][0] - 1, top[-1][1] + 1
        inner = dark[:, bx0:bx1]
        seps = [r for r in rules(inner, 0.9) if r[0] > by0 + thin]
        edges = [by0] + [v for r in seps for v in (r[0] - 1, r[1] + 1)] + [H - 1]
        cy0, cy1 = max(zip(edges[0::2], edges[1::2]), key=lambda c: c[1] - c[0])
        if (cy1 - cy0) * (bx1 - bx0) >= 0.25 * W * H:
            x0, y0, x1, y1 = bx0 + 2, cy0 + 2, bx1 - 2, cy1 - 2
    ink = g[y0:y1, x0:x1] < 200
    if ink.mean() > 0.5:                                # dark or grey background: no ink box to trust
        return x0, y0, x1, y1
    ys_, xs_ = np.where(ink)
    if len(xs_) == 0:
        return x0, y0, x1, y1
    return (max(x0, x0 + int(xs_.min()) - margin), max(y0, y0 + int(ys_.min()) - margin),
            min(x1, x0 + int(xs_.max()) + margin), min(y1, y0 + int(ys_.max()) + margin))


# ------------------------------------------------------------------ geometry of boxes
def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def _same(a, b) -> bool:
    """Two boxes show the same thing: they overlap well, or the smaller lies mostly inside the larger or has its
    centre in it (a door leaf and the double door it belongs to) - unless it is far smaller (a symbol inside a
    box drawn around a whole room)."""
    i = _inter(a, b)
    if i <= 0:
        return False
    sa, sb = _area(a), _area(b)
    if i / (sa + sb - i) >= 0.3:
        return True
    small, big = (a, b) if sa <= sb else (b, a)
    if min(sa, sb) < 0.2 * max(sa, sb):
        return False
    cx, cy = (small[0] + small[2]) / 2, (small[1] + small[3]) / 2
    return i / max(1e-9, min(sa, sb)) >= 0.6 or (big[0] <= cx <= big[2] and big[1] <= cy <= big[3])


def _fit(im: Image.Image, side: int, up: float = 1.0) -> Image.Image:
    """Resize so the long side is `side`, never enlarging by more than `up`."""
    f = min(up, side / max(im.size))
    if abs(f - 1) < 0.02:
        return im
    return im.resize((max(1, round(im.size[0] * f)), max(1, round(im.size[1] * f))), Image.LANCZOS)


def grid(rect, tile: float, overlap: float = OVERLAP, max_tiles: int = MAX_TILES) -> list[tuple[int, int, int, int]]:
    """Overlapping tiles of about `tile` pixels that cover rect evenly."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0

    def n_for(size, t):
        return 1 if size <= t * 1.15 else math.ceil((size - overlap * t) / (t * (1 - overlap)))
    nx, ny = n_for(w, tile), n_for(h, tile)
    while nx * ny > max_tiles:                              # too many: larger tiles
        tile *= 1.1
        nx, ny = n_for(w, tile), n_for(h, tile)
    tw = w if nx == 1 else w / (nx - (nx - 1) * overlap)
    th = h if ny == 1 else h / (ny - (ny - 1) * overlap)
    out = []
    for r in range(ny):
        for c in range(nx):
            ax = x0 + (0 if nx == 1 else c * (w - tw) / (nx - 1))
            ay = y0 + (0 if ny == 1 else r * (h - th) / (ny - 1))
            out.append((int(ax), int(ay), int(math.ceil(ax + tw)), int(math.ceil(ay + th))))
    return out


# ------------------------------------------------------------------ steps
def triage(page: Image.Image, stats: Stats) -> dict:
    """-> {plan: bool, p, kind}.  A failed request counts as a plan (the layout step decides then)."""
    ans = decide(_fit(page, 1024), TRIAGE_QUESTIONS, stats, "triage")
    if not ans:
        return dict(plan=True, p=None, kind=None)
    p = (ans.get("floor_plan") or {}).get("noul")
    kind = (ans.get("kind") or {}).get("choice")
    return dict(plan=p is None or p >= PLAN_MIN or kind == "floor_plan", p=p, kind=kind)


def layout(im: Image.Image, stats: Stats) -> list[dict]:
    """Plan areas of the page: [{rect (pixels), door_px, how}].  Empty when the model sees no plan."""
    W, H = im.size
    ans = generate(_fit(im, PAGE_SIDE), LAYOUT_PROMPT, LAYOUT_SCHEMA, stats, tag="layout")
    if ans is None:                                         # no answer: the ink box, one area
        return [dict(rect=list(drawing_area(im)), door_px=None, how="ink box (layout request failed)")]
    out = []
    for p in ans.get("plans") or []:
        b = p.get("box_2d") or []
        if len(b) != 4:
            continue
        ymin, xmin, ymax, xmax = (min(1000, max(0, float(v))) for v in b)
        x0, y0, x1, y1 = min(xmin, xmax) / 1000 * W, min(ymin, ymax) / 1000 * H, max(xmin, xmax) / 1000 * W, max(ymin, ymax) / 1000 * H
        if (x1 - x0) < 0.04 * W or (y1 - y0) < 0.04 * H:
            continue
        pad = 0.04 * max(x1 - x0, y1 - y0)                  # exterior doors, window sills and loose boxes
        rect = [max(0, int(x0 - pad)), max(0, int(y0 - pad)), min(W, int(x1 + pad)), min(H, int(y1 + pad))]
        dw = p.get("door_width")
        door = float(dw) / 1000 * W if isinstance(dw, (int, float)) and 1 <= dw <= 300 else None
        out.append(dict(rect=rect, door_px=door, how="layout"))
    out.sort(key=lambda a: -_area(a["rect"]))
    kept: list[dict] = []
    for a in out:                                           # one plan boxed twice
        if not any(_inter(a["rect"], k["rect"]) > 0.5 * _area(a["rect"]) for k in kept):
            kept.append(a)
    return kept[:8]


def tile_size(area: dict) -> float:
    """Side of a tile in page pixels so that a door is about DOOR_PX wide in the view sent."""
    x0, y0, x1, y1 = area["rect"]
    side = max(x1 - x0, y1 - y0)
    door = area.get("door_px") or side / 40                 # unknown: a mid-sized plan
    door = min(max(door, side / 150), side / 8)             # an estimate, not a measurement
    t = VIEW_SIDE * door / DOOR_PX
    # never one look only: at least 2 tiles along the long side next to the whole area (a single view misses
    # a few symbols; a second, closer view of every spot finds them)
    return max(min(t, 0.62 * side), VIEW_SIDE / MAX_UPSCALE)


def _ask_view(im: Image.Image, rect, name: str, stats: Stats) -> dict:
    x0, y0, x1, y1 = rect
    v = im.crop(rect)
    f = min(MAX_UPSCALE, VIEW_SIDE / max(v.size))
    if abs(f - 1) > 0.02:
        v = v.resize((max(1, round(v.size[0] * f)), max(1, round(v.size[1] * f))), Image.LANCZOS)
    t = time.time()
    ans = generate(v, DETECT_PROMPT, DETECT_SCHEMA, stats, tag=f"view {name}")
    objs = []
    w, h = x1 - x0, y1 - y0
    for o in (ans or {}).get("objects") or []:
        b = o.get("box_2d") or []
        if len(b) != 4 or o.get("label") not in LABELS:
            continue
        try:
            ymin, xmin, ymax, xmax = (min(1000, max(0, float(c))) for c in b)
        except (TypeError, ValueError):
            continue
        box = [x0 + min(xmin, xmax) / 1000 * w, y0 + min(ymin, ymax) / 1000 * h,
               x0 + max(xmin, xmax) / 1000 * w, y0 + max(ymin, ymax) / 1000 * h]
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            continue
        frac = _area(box) / max(1.0, w * h)
        if frac > (0.5 if o["label"] == "stair" else 0.12):  # a box over much of the view is not one symbol
            continue
        # a box cut by an inner tile edge is the half of something a neighbouring tile sees whole
        edge = min(box[0] - x0, box[1] - y0, x1 - box[2], y1 - box[3]) <= 0.004 * max(w, h)
        objs.append(dict(label=o["label"], box=box, view=name, edge=edge))
    return dict(name=name, rect=list(rect), scale=round(f, 3), ok=ans is not None, n=len(objs), objects=objs,
                seconds=round(time.time() - t, 1))


def _has_ink(im: Image.Image, rect) -> bool:
    """A tile is worth asking about when something is drawn in it (not one flat tone)."""
    g = np.asarray(im.crop(rect).convert("L").resize((192, 192), Image.BILINEAR), dtype=np.float32)
    return float((np.abs(g - np.median(g)) > 25).mean()) > 0.002 or float(g.std()) > 8


def merge(objs: list[dict]) -> list[dict]:
    """One candidate per thing: boxes of the same label that show the same thing are clustered.  The candidate
    takes the box of its best member (not cut by a tile edge, then closest to the cluster's median size)."""
    clusters: list[list[dict]] = []
    for o in sorted(objs, key=lambda o: -_area(o["box"])):
        for c in clusters:
            if c[0]["label"] == o["label"] and any(_same(m["box"], o["box"]) for m in c):
                c.append(o)
                break
        else:
            clusters.append([o])
    out = []
    for c in clusters:
        whole = [m for m in c if not m["edge"]] or c
        med = float(np.median([_area(m["box"]) for m in whole]))
        best = min(whole, key=lambda m: abs(_area(m["box"]) - med))
        views = sorted({m["view"] for m in c})
        out.append(dict(label=best["label"], box=[round(v, 1) for v in best["box"]], view=best["view"], views=views,
                        votes=len(views), edge_only=all(m["edge"] for m in c)))
    return out


def slim_stairs(cands: list[dict]) -> None:
    """A 'stair' that is narrower than half a door is the ladder-like symbol of a small window in a wall."""
    doors = [max(c["box"][2] - c["box"][0], c["box"][3] - c["box"][1]) for c in cands if c["label"] == "door"]
    if len(doors) < 3:
        return
    d = float(np.median(doors))
    for c in cands:
        w, h = c["box"][2] - c["box"][0], c["box"][3] - c["box"][1]
        if c["label"] == "stair" and min(w, h) < 0.5 * d and max(w, h) < 1.6 * d:
            c["relabelled_from"], c["label"] = "stair", "window"


def _verify_one(im: Image.Image, c: dict, stats: Stats) -> dict | None:
    x0, y0, x1, y1 = c["box"]
    w, h = x1 - x0, y1 - y0
    side = max(2.6 * max(w, h), 1.5 * (w + h), 64)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx0, ry0 = max(0, int(cx - side / 2)), max(0, int(cy - side / 2))
    rx1, ry1 = min(im.size[0], int(cx + side / 2)), min(im.size[1], int(cy + side / 2))
    crop = im.crop((rx0, ry0, rx1, ry1))
    f = 448 / max(crop.size)
    crop = crop.resize((max(1, round(crop.size[0] * f)), max(1, round(crop.size[1] * f))), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    m = 4
    d.rectangle([(x0 - rx0) * f - m, (y0 - ry0) * f - m, (x1 - rx0) * f + m, (y1 - ry0) * f + m],
                outline=(255, 0, 0), width=2)
    ans = decide(crop, {"symbol": {"type": "choice", "instructions": VERIFY_INSTRUCTIONS,
                                   "criteria": VERIFY_CRITERIA}}, stats, "verify")
    if not ans or "symbol" not in ans:
        return None
    probs = ans["symbol"].get("probabilities") or {}
    return dict(choice=ans["symbol"].get("choice"), p={k: round(float(v), 3) for k, v in probs.items() if v >= 0.01})


def judge(c: dict) -> None:
    """Set kept / score / why on a candidate from its votes and the verifier's answer."""
    v = c.get("verify")
    votes = c["votes"]
    if v is None:                                           # no verifier answer: trust agreement between views
        c["score"] = round(min(0.9, 0.45 + 0.15 * votes), 2)
        c["kept"], c["why"] = True, "not verified"
        return
    p_own = v["p"].get(c["label"], 0.0)
    p_none = v["p"].get("zz_nothing", 0.0)
    if p_none >= REJECT_NONE and not (votes >= 3 and p_none < 0.85):
        c["kept"], c["why"], c["score"] = False, f"verifier: nothing ({p_none:.2f})", round(1 - p_none, 2)
        return
    # the verifier only says whether something is there: on the class Carbon, which saw the surroundings, is better
    c["why"] = "verified" if p_own >= 0.5 else ("verified as a symbol" if p_none < 0.2 else "kept: verifier unsure")
    c["kept"] = True
    c["score"] = round(min(0.99, 0.6 * max(p_own, 1 - p_none - 0.2) + 0.1 * min(votes, 3) + 0.1), 2)


def dedupe(cands: list[dict]) -> None:
    """After relabelling, and for near-identical boxes of different labels: keep the better one."""
    kept = sorted((c for c in cands if c["kept"]), key=lambda c: -c["score"])
    seen: list[dict] = []
    for c in kept:
        clash = next((s for s in seen if _same(s["box"], c["box"]) and
                      (s["label"] == c["label"] or
                       _inter(s["box"], c["box"]) / (_area(s["box"]) + _area(c["box"]) - _inter(s["box"], c["box"])) >= 0.5)),
                     None)
        if clash is not None:
            c["kept"], c["why"] = False, f"same spot as a better {clash['label']} candidate"
        else:
            seen.append(c)


def load(image) -> Image.Image:
    im = image if isinstance(image, Image.Image) else Image.open(image)
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
    return im.convert("RGB")


def detect(image, budget_s: float | None = None, verify: bool = True, force: bool = False) -> dict:
    """Detect doors, windows, fixtures and stairs on one page image.  Boxes are in the pixels of that image.

    budget_s: seconds this call may take; requests stop at that point and what was found until then is returned.
    force: run even when the triage says the page is not a floor plan."""
    t0 = time.time()
    stats = Stats(None if budget_s is None else t0 + budget_s)
    im = load(image)
    W, H = im.size
    out = dict(detector=f"{MODEL} {PROMPT_VERSION}", plan=True, triage=None, areas=[], views=[], candidates=[],
               objects=[], size=[W, H], crop=None)         # crop: read by jstore.ingest.raster.detect

    def done():
        out.update(calls=stats.calls, cached=stats.cached, by_model=stats.by_model, errors=stats.errors,
                   seconds=round(time.time() - t0, 1))
        return out

    if not os.environ.get("GEMINI_API_KEY"):
        stats.error("GEMINI_API_KEY is not set")
        out["plan"] = None
        return done()
    with cf.ThreadPoolExecutor(CONCURRENCY) as pool:
        f_tri = pool.submit(triage, im, stats)
        f_lay = pool.submit(layout, im, stats)              # asked at once: the plan page is the common case
        out["triage"] = tri = f_tri.result()
        if not tri["plan"] and not force:
            out["plan"] = False
            f_lay.cancel()
            return done()
        areas = f_lay.result()
        if not areas:
            if tri["p"] is not None and tri["p"] >= 0.8:    # the triage is sure, the layout found nothing: ink box
                areas = [dict(rect=list(drawing_area(im)), door_px=None, how="ink box (layout found no plan)")]
            else:
                out["plan"] = False
                return done()
        # views: every area whole (when its symbols are legible at that size), and its tiles
        jobs = []
        for i, a in enumerate(areas):
            a["tile"] = round(tile_size(a))
            rects = grid(a["rect"], a["tile"])
            rects = [r for r in rects if _has_ink(im, r)] or rects      # a faint scan: ask about all of it
            a["tiles"] = len(rects)
            if 1 < len(rects) <= 6:
                jobs.append((tuple(a["rect"]), f"a{i}", 0))
            jobs += [(r, f"a{i}.t{k}", 1) for k, r in enumerate(rects)]
        out["areas"] = areas
        out["crop"] = [min(a["rect"][0] for a in areas), min(a["rect"][1] for a in areas),
                       max(a["rect"][2] for a in areas), max(a["rect"][3] for a in areas)]
        raw: list[dict] = []
        if stats.deadline is not None:                      # keep time for the verification
            stats.phase_end = stats.deadline - min(25.0, 0.25 * (stats.deadline - t0))
        pending = {pool.submit(_ask_view, im, j[0], j[1], stats): j for j in jobs}
        while pending:
            fin, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for fu in fin:
                rect, name, lvl = pending.pop(fu)
                r = fu.result()
                split = (lvl in (1, 2) and r["n"] >= MAX_OBJECTS and min(rect[2] - rect[0], rect[3] - rect[1]) > 160
                         and stats.left() > 20)
                out["views"].append(dict(view=name, rect=list(rect), scale=r["scale"], objects=r["n"], ok=r["ok"],
                                         seconds=r["seconds"], split=split))
                raw += r["objects"]                         # a crowded view still counts; its quarters add to it
                if split:
                    for k, q in enumerate(grid(rect, max(rect[2] - rect[0], rect[3] - rect[1]) * 0.6)):
                        pending[pool.submit(_ask_view, im, q, f"{name}.{k}", stats)] = (q, f"{name}.{k}", lvl + 1)
        stats.phase_end = None
        cands = merge(raw)
        slim_stairs(cands)
        # a candidate seen only as a box cut by a tile edge, never whole, is a fragment
        for c in cands:
            if c.pop("edge_only") and c["votes"] == 1:
                c.update(kept=False, why="cut by a tile edge and not seen whole in a neighbouring view", score=0.1)
        todo = [c for c in cands if "kept" not in c]
        if verify and todo and stats.left() > 5:
            for c, v in zip(todo, pool.map(lambda c: _verify_one(im, c, stats), todo)):
                c["verify"] = v
        for c in todo:
            judge(c)
        dedupe(cands)
    out["candidates"] = cands
    out["objects"] = [dict(label=c["label"], box=c["box"], score=c["score"], view=c["view"]) for c in cands if c["kept"]]
    return done()
