"""Layer 2 for raster plans that have no CAD / IFC / PDF twin: rooms and their areas from a scan or image export.

    interpret(db, file)   called by jstore.interpret.run for every PNG / JPG after raster.interpret

How a room comes about (each step is recorded in the definition rows and in the entity attrs):
  1. labels   every room label is a room, also when no outline is found.  With GEMINI_API_KEY set, one overview
              call says what the sheet shows (anything but a floor plan ends here), reads scale text, paper size
              and scale bar and lists the labelled rooms (name, number, printed area, label box, room box); sheets
              with many or small labels are read again from enlarged tiles (at most MAX_TILES).  Everything Gemini
              returned is stored as raster_detection rows (class room_label / scale_bar).  A label that the page
              OCR also read points at its raster_word row.  Without a key the labels come from the OCR lines alone
              (words of the room vocabulary).
  2. outline  walls = the thick ink (thin lines, text and furniture removed by an opening as wide as the usual
              line), falling back to all ink for plans drawn with thin lines only; free space is cut at its
              constrictions (watershed on the distance to the walls, basins joined again where the passage between
              them is nearly as wide as they are) and a room is the part that holds its label.  A region that
              does not fit the room box Gemini gave is dropped and the box itself is used (method gemini_box).
  3. scale    in this order: the frame of the page, printed scale with the DPI of the file (above 96), printed
              scale with the paper size named on the sheet (the image must have the ISO proportions), the scale
              bar measured in the pixels, the printed room areas themselves (>= 3 rooms that agree).  Where two of
              them exist they must agree within 8 %, else the confidence drops.  Without one of these no measured
              area is written, only printed ones.
  4. claims   net_area_m2 printed (method 'ocr' when the page OCR read the same value, else 'printed' = read by
              Gemini) and measured (method 'measured_outline'), each with its pixel box as source.

Nothing is added when another format of the drawing already gave rooms, or when raster.interpret read a numbered
plan well (>= 3 numbered rooms, at least half of them with a name or a printed area; see _prior).  Rooms raster.interpret left behind that do not pass that test are set to status
'superseded' so that the drawing does not hold the same room twice.

Limits: MAX_WORK_PX for segmentation, TIMEOUT_S per image (Gemini calls stop at the deadline and the reader goes on
with what it has).
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import hashlib
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

import cv2
import numpy as np
from PIL import Image

from ..db import DB, js
from . import define, issue, merge
from . import vocab

Image.MAX_IMAGE_PIXELS = None
TIMEOUT_S = float(os.environ.get("JSTORE_RASTER_ROOMS_TIMEOUT_S", "85"))
MAX_WORK_PX = 2200            # long side of the image the walls are segmented on
MAX_VIEW_PX = 1600            # long side of a view sent to Gemini
MAX_TILES = 9
BY = "jstore.interpret.raster_rooms"
VERSION = "rr-1"
ROOT = "https://generativelanguage.googleapis.com/v1beta"
MODEL_FAST = os.environ.get("JSTORE_RASTER_ROOMS_MODEL_FAST", "gemini-4-hydrogen")
MODEL_STRONG = os.environ.get("JSTORE_RASTER_ROOMS_MODEL", "gemini-4-carbon")

_ROOM_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "number": {"type": "string"}, "area_m2": {"type": "number"},
        "area_text": {"type": "string"}, "outdoor": {"type": "boolean"},
        "label_box": {"type": "array", "items": {"type": "integer"}},
        "room_box": {"type": "array", "items": {"type": "integer"}}},
    "required": ["name", "label_box", "room_box"]}
SCHEMA_TILE = {"type": "object", "properties": {"rooms": {"type": "array", "items": _ROOM_ITEM}}, "required": ["rooms"]}
SCHEMA_OVERVIEW = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["floor_plan", "site_plan", "elevation", "section", "detail", "photo",
                                            "map", "other"]},
        "plan_box": {"type": "array", "items": {"type": "integer"}},
        "scale_text": {"type": "string"}, "paper_size": {"type": "string"},
        "scale_bar": {"type": "object", "properties": {
            "box": {"type": "array", "items": {"type": "integer"}}, "length": {"type": "number"},
            "unit": {"type": "string"}}},
        "rooms": {"type": "array", "items": _ROOM_ITEM}},
    "required": ["kind", "rooms"]}
_ROOMS_TEXT = (
    "List EVERY room or space that carries a text label inside the floor plan (a name such as 'Kitchen', 'MH', "
    "'Corridor', 'WC', and/or a room number). One entry per label occurrence: a name printed in three rooms gives "
    "three entries. For each entry:\n"
    "- name: the room name exactly as written (keep the language, abbreviations and diacritics; join a name that is "
    "broken over several lines with spaces); '' when the room has only a number.\n"
    "- number: the room number printed with the name, '' when there is none.\n"
    "- area_m2 and area_text: the floor area printed for this room (e.g. '12,5 m2'), only when it is printed next to "
    "this label; omit both otherwise. Never estimate an area.\n"
    "- outdoor: true for balconies, terraces, loggias, gardens, yards and other spaces outside the building.\n"
    "- label_box: box around the label text. room_box: box around the floor of the room this label belongs to "
    "(wall to wall).\n"
    "Boxes are [ymin, xmin, ymax, xmax], integers normalized to 0-1000 relative to this image. Do not list "
    "furniture, fixture or equipment labels, dimension texts, title block texts or legends. Do not invent rooms: "
    "a room without any text label is not listed.")
PROMPT_OVERVIEW = (
    "The image is a scan or export of a drawing sheet.\n"
    "kind: what the main drawing shows: floor_plan (a horizontal cut through a building showing rooms), site_plan, "
    "elevation, section, detail, photo, map or other.\n"
    "If it is a floor plan:\n"
    "plan_box: box around all floor plan drawings on the sheet (without title block and legend).\n"
    "scale_text: the drawing scale as printed (e.g. '1:100', '1/4\" = 1\\'-0\"'), '' when none is printed.\n"
    "paper_size: the sheet size if printed in the title block (e.g. 'A2'), else ''.\n"
    "scale_bar: when a graphic scale bar is drawn: box = tight box around the bar itself from its first tick (0) to "
    "its last tick (without the numbers), length = the real length the whole bar stands for, unit = 'm', 'ft', "
    "'cm' or 'mm'. Omit when there is none.\n" + _ROOMS_TEXT +
    "\nIf it is not a floor plan, return an empty rooms list.")
PROMPT_TILE = "The image shows a part of an architectural floor plan, enlarged.\n" + _ROOMS_TEXT + (
    "\nInclude a room when its label is visible in this image, even when the room itself is cut by the image "
    "border (then room_box is the visible part).")


def _cache_dir():
    from ..pipeline import store_dir
    d = store_dir() / "cache" / "gemini_rooms"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


def ask(view: Image.Image, prompt: str, schema: dict, model: str, deadline: float, thinking: str | None = "low") -> dict:
    """One structured answer about one view, from the disk cache or the API.
    -> {data, cached, seconds} or {error}.  Never raises; gives up at `deadline` (epoch seconds)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return dict(error="GEMINI_API_KEY is not set")
    img = _jpeg(view)
    h = hashlib.sha256(img + f"|{model}|{VERSION}|{thinking}|{hashlib.sha256(prompt.encode()).hexdigest()}".encode())
    path = _cache_dir() / f"{h.hexdigest()}.json"
    if path.exists():
        try:
            return dict(json.loads(path.read_text()), cached=True)
        except ValueError:
            pass
    gen: dict = {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}}
    if thinking:
        gen["thinkingConfig"] = {"thinkingLevel": thinking}
    body = json.dumps({"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}}, {"text": prompt}]}],
        "generationConfig": gen}).encode()
    t, err = time.time(), "no attempt"
    for attempt in range(3):
        left = deadline - time.time()
        if left < 3:
            return dict(error=f"out of time ({err})")
        req = urllib.request.Request(f"{ROOT}/models/{model}:generateContent", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=left) as r:
                status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - timeout, connection reset
            status, text = -1, type(e).__name__
        if status == 200:
            try:
                js = json.loads(text)
                parts = js["candidates"][0]["content"]["parts"]
                data = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            except (KeyError, IndexError, ValueError, TypeError) as e:
                err = f"unreadable answer: {type(e).__name__}"
                continue
            rec = dict(model=model, version=VERSION, data=data, seconds=round(time.time() - t, 1),
                       view_px=list(view.size))
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(rec))
            os.replace(tmp, path)
            return dict(rec, cached=False)
        err = f"http {status}: {text[:160]}"
        if status not in (-1, 408, 429, 500, 502, 503, 504):
            break
        time.sleep(min(2 + 3 * attempt, max(0, deadline - time.time() - 3)))
    return dict(error=err)


# ------------------------------------------------------------------------------------------------ labels (Gemini)
def _px_box(b, rect):
    """Gemini box [ymin, xmin, ymax, xmax] on 0-1000 of a view cut from rect (x0, y0, x1, y1) -> source pixels."""
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return None
    try:
        y0, x0, y1, x1 = [min(1000.0, max(0.0, float(v))) for v in b]
    except (TypeError, ValueError):
        return None
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    out = [rect[0] + min(x0, x1) / 1000 * w, rect[1] + min(y0, y1) / 1000 * h,
           rect[0] + max(x0, x1) / 1000 * w, rect[1] + max(y0, y1) / 1000 * h]
    return out if out[2] - out[0] >= 1 and out[3] - out[1] >= 1 else None


def _view(im: Image.Image, rect, scale: float | None = None) -> Image.Image:
    part = im.crop(tuple(int(v) for v in rect)).convert("RGB")
    k = scale if scale else min(2.5, max(1200 / max(part.size), 1.0)) if max(part.size) < 1200 else 1.0
    k = min(k, MAX_VIEW_PX / max(part.size))
    if abs(k - 1) > 0.02:
        part = part.resize((max(1, round(part.size[0] * k)), max(1, round(part.size[1] * k))),
                           Image.LANCZOS if k < 1 else Image.BICUBIC)
    return part


def _rooms_of(data: dict, rect, view: str) -> list[dict]:
    out = []
    for r in data.get("rooms") or []:
        if not isinstance(r, dict):
            continue
        lb, rb = _px_box(r.get("label_box"), rect), _px_box(r.get("room_box"), rect)
        name = re.sub(r"\s+", " ", str(r.get("name") or "")).strip()
        number = re.sub(r"\s+", " ", str(r.get("number") or "")).strip()
        if lb is None or not (name or number):
            continue
        area = r.get("area_m2")
        area = float(area) if isinstance(area, (int, float)) and 0.2 <= float(area) <= 20000 else None
        cut = rb is not None and view != "overview" and (
            rb[0] <= rect[0] + 2 or rb[1] <= rect[1] + 2 or rb[2] >= rect[2] - 2 or rb[3] >= rect[3] - 2)
        edge = min(lb[0] - rect[0], lb[1] - rect[1], rect[2] - lb[2], rect[3] - lb[3])
        out.append(dict(name=name, number=number or None, area=area, area_text=str(r.get("area_text") or "") or None,
                        outdoor=bool(r.get("outdoor")), label_box=lb, room_box=rb, view=view, cut=cut, edge=edge))
    return out


def _same(a: dict, b: dict, tol: float) -> bool:
    la, lb = a["label_box"], b["label_box"]
    d = math.hypot((la[0] + la[2] - lb[0] - lb[2]) / 2, (la[1] + la[3] - lb[1] - lb[3]) / 2)
    ix = min(la[2], lb[2]) - max(la[0], lb[0])
    iy = min(la[3], lb[3]) - max(la[1], lb[1])
    na, nb = vocab.plain(a["name"])[:4], vocab.plain(b["name"])[:4]
    return (ix > 0 and iy > 0) or (d < tol and (na == nb or a["number"] and a["number"] == b["number"]))


def _tiles(rect, label_h: float) -> tuple[list, float]:
    """Overlapping tiles over rect so that labels are about 16 px high in a view of MAX_VIEW_PX."""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    up = min(3.0, max(0.25, 16.0 / max(label_h, 1.0)))
    side = MAX_VIEW_PX / up
    while True:
        nx, ny = max(1, math.ceil((w - 0.15 * side) / (0.85 * side))), max(1, math.ceil((h - 0.15 * side) / (0.85 * side)))
        if nx * ny <= MAX_TILES:
            break
        side *= 1.15
    tw, th = min(w, max(side, w / nx * 1.15)), min(h, max(side, h / ny * 1.15))
    xs = [rect[0] + (w - tw) * i / max(1, nx - 1) for i in range(nx)]
    ys = [rect[1] + (h - th) * j / max(1, ny - 1) for j in range(ny)]
    return [(x, y, x + tw, y + th) for y in ys for x in xs], min(up, MAX_VIEW_PX / max(tw, th))


def read_labels(im: Image.Image, deadline: float, log: dict) -> dict | None:
    """Overview call and, where the labels are many or small, tile calls.  -> {kind, rooms, scale_text, paper_size,
    scale_bar} with boxes in source pixels, or None when Gemini cannot be asked."""
    if not os.environ.get("GEMINI_API_KEY") or os.environ.get("JSTORE_RASTER_ROOMS_GEMINI", "1") in ("0", "off"):
        log["gemini"] = "off (no GEMINI_API_KEY)" if not os.environ.get("GEMINI_API_KEY") else "off"
        return None
    W, H = im.size
    full = (0, 0, W, H)
    t = time.time()
    res = ask(_view(im, full), PROMPT_OVERVIEW, SCHEMA_OVERVIEW, MODEL_FAST, min(deadline, time.time() + 60))
    log.update(calls=0 if res.get("cached") else 1, cached=1 if res.get("cached") else 0, model=MODEL_FAST,
               overview_s=round(time.time() - t, 1))
    if "error" in res:
        log["gemini"] = "overview failed: " + res["error"]
        return None
    d = res["data"] if isinstance(res.get("data"), dict) else {}
    rooms = _rooms_of(d, full, "overview")
    out = dict(kind=d.get("kind") or "other", rooms=rooms, scale_text=str(d.get("scale_text") or ""),
               paper_size=str(d.get("paper_size") or ""), scale_bar=None, tiles=0)
    bar = d.get("scale_bar")
    if isinstance(bar, dict) and _px_box(bar.get("box"), full) and isinstance(bar.get("length"), (int, float)):
        out["scale_bar"] = dict(box=_px_box(bar.get("box"), full), length=float(bar["length"]),
                                unit=str(bar.get("unit") or "m").lower())
    if out["kind"] != "floor_plan" or not rooms:
        return out
    hs = sorted(r["label_box"][3] - r["label_box"][1] for r in rooms)
    label_h = hs[len(hs) // 2]
    in_view = label_h * min(1.0, MAX_VIEW_PX / max(W, H))
    if not (len(rooms) >= 40 or (len(rooms) >= 12 and in_view < 13) or in_view < 8):
        return out
    boxes = [r["room_box"] or r["label_box"] for r in rooms]
    pb = _px_box(d.get("plan_box"), full) or boxes[0]
    rect = [min([pb[0]] + [b[0] for b in boxes]), min([pb[1]] + [b[1] for b in boxes]),
            max([pb[2]] + [b[2] for b in boxes]), max([pb[3]] + [b[3] for b in boxes])]
    pad = 0.03 * max(rect[2] - rect[0], rect[3] - rect[1])
    rect = [max(0, rect[0] - pad), max(0, rect[1] - pad), min(W, rect[2] + pad), min(H, rect[3] + pad)]
    tiles, up = _tiles(rect, label_h)
    if len(tiles) == 1 and up <= 1.2 * min(1.0, MAX_VIEW_PX / max(W, H)):
        return out                                        # a tile would show no more than the overview did
    t = time.time()
    views = [_view(im, tr, up) for tr in tiles]
    with cf.ThreadPoolExecutor(len(tiles)) as ex:
        answers = list(ex.map(lambda v: ask(v, PROMPT_TILE, SCHEMA_TILE, MODEL_FAST, deadline), views))
    got: list[dict] = []
    failed = 0
    for i, (tr, a) in enumerate(zip(tiles, answers)):
        if "error" in a:
            failed += 1
            continue
        log["calls"] += 0 if a.get("cached") else 1
        log["cached"] += 1 if a.get("cached") else 0
        for r in _rooms_of(a["data"] if isinstance(a.get("data"), dict) else {}, tr, f"tile {i}"):
            dup = next((g for g in got if _same(g, r, 1.5 * label_h + 6)), None)
            if dup is None:
                got.append(r)
            elif r["edge"] > dup["edge"]:                 # the tile that shows the label farther from its border
                got[got.index(dup)] = r
    log.update(tiles=len(tiles), tiles_failed=failed, tiles_s=round(time.time() - t, 1))
    tol = 0.02 * max(W, H) + 2 * label_h
    for r in rooms:                                       # what only the overview saw; its room box where a tile cut it
        m = next((g for g in got if _same(g, r, tol)), None)
        if m is None:
            got.append(r)
        elif (m["cut"] or m["room_box"] is None) and r["room_box"] is not None:
            m["room_box"], m["cut"] = r["room_box"], False
    out.update(rooms=got, tiles=len(tiles))
    return out


# --------------------------------------------------------------------------------------------------- labels (OCR)
def _ocr_tokens(words: list[dict]) -> list[dict]:
    out = []
    for w in words:
        t = (w["text"] or "").strip()
        t2 = re.sub(r"(?<=\d)\s*m\s*[2²]$", " m2", t)
        kind, val = vocab.classify(t2)
        if kind == "other" and re.fullmatch(r"\d{1,3}[.,]\d{1,2}", t):
            kind, val = "bare", float(t.replace(",", "."))
        out.append(dict(w, kind=kind, value=val, box=[w["x0"], w["y0"], w["x1"], w["y1"]]))
    return out


def labels_from_ocr(toks: list[dict]) -> list[dict]:
    """Rooms from the OCR lines alone: words of the room vocabulary; number and printed area next to them."""
    rooms = []
    for t in toks:
        if t["kind"] != "label" or vocab.room_type(t["value"]) is None or len(t["value"]) > 30:
            continue
        x0, y0, x1, y1 = t["box"]
        h = max(4.0, min(y1 - y0, x1 - x0))

        def near(o, k=2.5):
            b = o["box"]
            return b[0] < x1 + k * h and b[2] > x0 - k * h and b[1] < y1 + k * h and b[3] > y0 - k * h
        area = next((o for o in toks if o["kind"] == "area" and near(o)), None)
        num = next((o for o in toks if o["kind"] == "num" and near(o, 1.5)), None)
        rooms.append(dict(name=t["value"], number=num["value"] if num else None, area=area["value"] if area else None,
                          area_text=area["text"] if area else None, outdoor=False, label_box=list(t["box"]),
                          room_box=None, view="ocr", word=t, area_word=area))
    return rooms


def attach_ocr(rooms: list[dict], toks: list[dict]) -> None:
    """Point each Gemini label at the OCR line that reads the same, and its printed area at the OCR area text."""
    for r in rooms:
        if r.get("view") == "ocr":
            continue
        lb = r["label_box"]
        h = max(4.0, lb[3] - lb[1])
        want = vocab.plain(r["name"] or r["number"] or "").replace(" ", "")
        for t in toks:
            b = t["box"]
            if min(lb[2], b[2]) - max(lb[0], b[0]) > -h and min(lb[3], b[3]) - max(lb[1], b[1]) > -h:
                got = vocab.plain(t["text"]).replace(" ", "")
                if got and want and (got == want or (len(got) >= 3 and (got in want or want in got))):
                    r["word"] = t
                    break
        if r["area"] is not None:
            rb = r["room_box"] or [lb[0] - 6 * h, lb[1] - 6 * h, lb[2] + 6 * h, lb[3] + 6 * h]
            for t in toks:
                cx, cy = (t["box"][0] + t["box"][2]) / 2, (t["box"][1] + t["box"][3]) / 2
                if t["kind"] in ("area", "bare") and abs(t["value"] - r["area"]) < 0.051 and \
                        rb[0] - h <= cx <= rb[2] + h and rb[1] - h <= cy <= rb[3] + h:
                    r["area_word"] = t
                    break


# ------------------------------------------------------------------------------------------------------- outline
def _ink(gray: np.ndarray) -> np.ndarray:
    """Dark marks against the local paper tone (also on yellowed or unevenly lit scans)."""
    k = max(15, int(max(gray.shape) / 60) | 1)
    bg = cv2.blur(cv2.dilate(gray, np.ones((k, k), np.uint8)), (k, k))
    return ((bg.astype(np.int16) - gray.astype(np.int16)) > 45) | (gray < 70)


def _line_width(ink: np.ndarray) -> int:
    """Most common run length of ink across rows and columns = the usual line width in pixels."""
    counts = np.zeros(41, np.int64)
    for a in (ink, ink.T):
        p = np.pad(a.astype(np.int8), ((0, 0), (1, 1)))
        d = np.diff(p, axis=1)
        s, e = np.nonzero(d == 1), np.nonzero(d == -1)
        counts += np.bincount(np.clip(e[1] - s[1], 0, 40), minlength=41)
    return int(np.argmax(counts[1:40])) + 1 if counts[1:40].sum() else 1


def _basins(barrier: np.ndarray, deadline: float):
    """Free space cut at its constrictions.  -> (labels, peak per basin, {(a, b): width of the passage between})."""
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed
    free = ~barrier
    dt = cv2.distanceTransform(free.astype(np.uint8), cv2.DIST_L2, 5)
    pk = peak_local_max(dt, min_distance=max(3, int(max(barrier.shape) / 250)), threshold_abs=2.0, exclude_border=False)
    if len(pk) == 0 or time.time() > deadline:
        return None
    markers = np.zeros(barrier.shape, np.int32)
    markers[pk[:, 0], pk[:, 1]] = np.arange(1, len(pk) + 1)
    lab = watershed(-dt, markers, mask=free)
    n = len(pk)
    peak = np.zeros(n + 1, np.float32)
    np.maximum.at(peak, lab.ravel(), dt.ravel())
    keys, vals = [], []
    for a, b, da, db_ in ((lab[:, :-1], lab[:, 1:], dt[:, :-1], dt[:, 1:]), (lab[:-1], lab[1:], dt[:-1], dt[1:])):
        m = (a != b) & (a > 0) & (b > 0)
        A, B = a[m].astype(np.int64), b[m].astype(np.int64)
        keys.append(np.minimum(A, B) * (n + 1) + np.maximum(A, B))
        vals.append(np.minimum(da[m], db_[m]))
    keys, vals = np.concatenate(keys), np.concatenate(vals)
    edges = {}
    if len(keys):
        o = np.lexsort((vals, keys))
        keys, vals = keys[o], vals[o]
        last = np.r_[np.nonzero(np.diff(keys))[0], len(keys) - 1]         # the largest value of each key
        edges = {(int(k // (n + 1)), int(k % (n + 1))): float(v) for k, v in zip(keys[last], vals[last])}
    return lab, peak, edges


def _group(lab, peak, edges, seeds: dict, limit: dict, home: dict | None = None, ratio: float = 0.8):
    """Join basins across wide passages, widest first; two basins that hold different labels (or a label and the
    sheet margin) are never joined, and a label takes no free space whose centre lies outside the box it may grow in
    (limit: label index -> [x0, y0, x1, y1]).  seeds: basin -> label index (-1 = outside).  -> owner label per basin."""
    n = len(peak) - 1
    parent = list(range(n + 1))
    tag = dict(seeds)
    top = peak.copy()
    ys, xs = np.nonzero(lab > 0)
    v = lab[ys, xs]
    cnt = np.bincount(v, minlength=n + 1).astype(np.float64)
    sx, sy = np.bincount(v, weights=xs, minlength=n + 1), np.bincount(v, weights=ys, minlength=n + 1)

    def inside(label, root):
        b = limit.get(label)
        if b is None or cnt[root] == 0:
            return True
        return b[0] <= sx[root] / cnt[root] <= b[2] and b[1] <= sy[root] / cnt[root] <= b[3]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    order = sorted(edges.items(), key=lambda kv: -kv[1] / max(1e-6, min(peak[kv[0][0]], peak[kv[0][1]])))
    for _ in range(2):                                    # second round: groups have grown, ratios are re-read
        for (a, b), s in order:
            ra, rb = find(a), find(b)
            if ra == rb or (ra in tag and rb in tag):
                continue
            if s < ratio * min(top[ra], top[rb]):
                continue
            if (ra in tag and not inside(tag[ra], rb)) or (rb in tag and not inside(tag[rb], ra)):
                continue
            parent[rb] = ra
            cnt[ra] += cnt[rb]
            sx[ra] += sx[rb]
            sy[ra] += sy[rb]
            top[ra] = max(top[ra], top[rb])
            if rb in tag:
                tag[ra] = tag.pop(rb)
    # free space left over that lies inside the room box of a label it touches (a nook behind a narrower passage)
    core = {i: b for i, b in (home or {}).items()}
    for _ in range(2):
        best = {}
        for (a, b), s in edges.items():
            ra, rb = find(a), find(b)
            for u, t in ((ra, rb), (rb, ra)):
                if u != t and u not in tag and tag.get(t, -1) >= 0 and cnt[u] > 0:
                    bx = core.get(tag[t])
                    if bx is not None and bx[0] <= sx[u] / cnt[u] <= bx[2] and bx[1] <= sy[u] / cnt[u] <= bx[3] and \
                            s > best.get(u, (0, None))[0]:
                        best[u] = (s, t)
        if not best:
            break
        for u, (s, t) in best.items():
            ru, rt = find(u), find(t)
            if ru != rt and ru not in tag and rt in tag:
                parent[ru] = rt
                cnt[rt] += cnt[ru]
                sx[rt] += sx[ru]
                sy[rt] += sy[ru]
    owner = np.full(n + 1, -2, np.int32)
    for x in range(1, n + 1):
        owner[x] = tag.get(find(x), -2)
    return owner


def _seed_basin(lab, box, k: float):
    """Basin under a label: the most frequent one inside its box (work pixels)."""
    x0, y0, x1, y1 = [int(round(v * k)) for v in box]
    win = lab[max(0, y0):max(y0 + 1, y1 + 1), max(0, x0):max(x0 + 1, x1 + 1)]
    v = win[win > 0]
    if v.size == 0:
        return None
    ids, cnt = np.unique(v, return_counts=True)
    return int(ids[np.argmax(cnt)])


def _iou(a, b) -> float:
    ix, iy = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    i = ix * iy
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def segment(gray: np.ndarray, rooms: list[dict], k: float, text_boxes: list, deadline: float, log: dict) -> None:
    """Give every room that can be outlined: region (bool mask in a bbox), bbox (work px), px, outline method."""
    ink = _ink(gray)
    H, W = ink.shape
    lw = _line_width(ink)
    op = max(3, 2 * lw + 1)
    walls = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, np.ones((op, op), np.uint8))
    n, cl, st, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
    small = np.maximum(st[:, 2], st[:, 3]) < 4 * op                      # bold glyphs, dots, arrow heads
    small[0] = False
    walls = (walls > 0) & ~small[cl]
    share = float(walls.sum()) / max(1, int(ink.sum()))
    thin = ink.copy()
    for b in text_boxes:
        x0, y0, x1, y1 = [int(round(v * k)) for v in b]
        thin[max(0, y0 - 1):y1 + 2, max(0, x0 - 1):x1 + 2] = False
    modes = ([("walls", walls)] if share >= 0.12 else []) + [("all_ink", thin)]
    log.update(line_px=lw, wall_ink_share=round(share, 3), modes=[])
    todo = list(range(len(rooms)))
    for name, barrier in modes:
        if not todo or time.time() > deadline:
            break
        res = _basins(barrier, deadline)
        if res is None:
            continue
        lab, peak, edges = res
        seeds = {int(b): -1 for b in np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]) if b > 0}
        for i in todo:
            b = _seed_basin(lab, rooms[i]["label_box"], k)
            if b is not None and b not in seeds:
                seeds[b] = i
        limit = {}
        for i in todo:
            rb = rooms[i].get("room_box")
            if rb is not None:
                mx, my = 0.12 * (rb[2] - rb[0]) + 4, 0.12 * (rb[3] - rb[1]) + 4
                limit[i] = [(rb[0] - mx) * k, (rb[1] - my) * k, (rb[2] + mx) * k, (rb[3] + my) * k]
        home = {i: [v * k for v in rooms[i]["room_box"]] for i in todo if rooms[i].get("room_box") is not None}
        owner = _group(lab, peak, edges, seeds, limit, home)
        reg = owner[lab]
        objs = {}
        ys, xs = np.nonzero(reg >= 0)
        if len(ys):
            v = reg[ys, xs]
            for i in np.unique(v):
                m = v == i
                objs[int(i)] = (xs[m].min(), ys[m].min(), xs[m].max() + 1, ys[m].max() + 1)
        done = 0
        for i in list(todo):
            if i not in objs:
                continue
            x0, y0, x1, y1 = objs[i]
            mask = reg[y0:y1, x0:x1] == i
            if name == "all_ink":                         # furniture and text inside the room belong to it
                from scipy import ndimage as ndi
                mask = ndi.binary_fill_holes(cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE,
                                                              np.ones((op, op), np.uint8)) > 0)
            px = int(mask.sum())
            rb = rooms[i].get("room_box")
            if rb is not None:
                fit = _iou([x0, y0, x1, y1], [v * k for v in rb])
                ok = fit >= 0.45
            else:
                fit, ok = None, px < 0.2 * H * W
            if ok and px >= 30:
                rooms[i].update(region=mask, bbox=[x0, y0, x1, y1], px=px, outline="wall_segmentation",
                                barrier=name, fit=None if fit is None else round(fit, 2))
                todo.remove(i)
                done += 1
        log["modes"].append(dict(barrier=name, basins=int(len(peak) - 1), outlined=done))
    for i in todo:                                        # no region: the box Gemini drew around the room
        rb = rooms[i].get("room_box")
        if rb is not None and not rooms[i].get("cut"):
            x0, y0, x1, y1 = [int(round(v * k)) for v in rb]
            x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
            if (x1 - x0) * (y1 - y0) < 0.5 * H * W:
                rooms[i].update(region=None, bbox=[x0, y0, x1, y1], px=(x1 - x0) * (y1 - y0), outline="gemini_box",
                                barrier=None, fit=None)


# --------------------------------------------------------------------------------------------------------- scale
PAPER_MM = {"A0": 1189, "A1": 841, "A2": 594, "A3": 420, "A4": 297}
UNIT_M = {"m": 1.0, "ft": 0.3048, "feet": 0.3048, "'": 0.3048, "cm": 0.01, "mm": 0.001, "in": 0.0254}


def _bar_px(gray: np.ndarray, box, k: float) -> float | None:
    """Length in source pixels of the scale bar: the extent of the ink in the rows (columns) of the box Gemini gave,
    looked for a little beyond both ends."""
    x0, y0, x1, y1 = box
    horiz = (x1 - x0) >= (y1 - y0)
    if not horiz:
        gray, (x0, y0, x1, y1) = gray.T, (y0, x0, y1, x1)
    w = x1 - x0
    X0 = max(0, int((x0 - 0.12 * w) * k))
    c = gray[max(0, int(y0 * k)):int(math.ceil(y1 * k)) + 1, X0:int((x1 + 0.12 * w) * k) + 1]
    if c.size == 0:
        return None
    cols = ((c.astype(np.int16) < int(np.percentile(c, 90)) - 30).any(0)).astype(np.uint8)[None, :]
    gap = max(3, int(0.04 * w * k)) | 1
    cols = cv2.morphologyEx(cols, cv2.MORPH_CLOSE, np.ones((1, gap), np.uint8))[0]
    d = np.diff(np.r_[0, cols.astype(np.int8), 0])
    s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    if not len(s):
        return None
    i = int(np.argmax(e - s))
    L = (e[i] - s[i]) / k
    return float(L) if 0.8 * w <= L <= 1.2 * w else None


def find_scale(db: DB, f: dict, page: dict, toks: list[dict], g: dict | None, gray: np.ndarray, k: float,
               dpi_of_file: float | None = None) -> dict:
    """Metres per source pixel and how far it can be trusted.  -> {mpp or None, confidence, source, detail}."""
    from ..frames import scales_in, trusted_scale
    W, H = page["image_w"], page["image_h"]
    meta = page["meta"] or {}
    detail: dict = {}
    fr = db.one("select m, method from jstore.frame where file_id = :f and page_no = 0 and method <> 'pixel_scale'",
                f=f["file_id"])
    if fr:
        detail["frame"] = dict(method=fr["method"], mpp=round(math.hypot(fr["m"][0], fr["m"][3]), 6))
    sc = trusted_scale(meta.get("scales"))
    if sc is None and g and g.get("scale_text") and not meta.get("scales"):
        sc = trusted_scale(scales_in(g["scale_text"])) or next(iter(scales_in("scale " + g["scale_text"])), None)
    ratio = float(sc["ratio"]) if sc else None
    detail.update(scale_text=sc["text"] if sc else None, scales_on_page=[s["text"] for s in meta.get("scales") or []])
    papers = {m.group(1).upper() for t in toks if len(t["text"] or "") <= 24
              for m in re.finditer(r"(?<![A-Za-z0-9])(A[0-4])(?![A-Za-z0-9])", t["text"] or "")}
    if g and re.fullmatch(r"A[0-4]", (g.get("paper_size") or "").strip().upper()):
        papers.add(g["paper_size"].strip().upper())
    mpp_paper = None
    if ratio and len(papers) == 1 and abs(max(W, H) / min(W, H) / math.sqrt(2) - 1) < 0.03:
        paper = next(iter(papers))
        mpp_paper = ratio * PAPER_MM[paper] / 1000.0 / max(W, H)
        detail.update(paper=paper, mpp_paper=round(mpp_paper, 6))
    mpp_bar = None
    bar = g.get("scale_bar") if g else None
    if bar and UNIT_M.get(bar["unit"]) and bar["length"] > 0:
        L = _bar_px(gray, bar["box"], k)
        detail["scale_bar"] = dict(length=bar["length"], unit=bar["unit"], box=[round(v) for v in bar["box"]],
                                   measured_px=None if L is None else round(L, 1))
        if L:
            mpp_bar = bar["length"] * UNIT_M[bar["unit"]] / L
            detail["mpp_bar"] = round(mpp_bar, 6)
    agree = lambda a, b: a and b and abs(a / b - 1) < 0.08       # noqa: E731
    dpi = (meta.get("dpi") or [dpi_of_file])[0]
    if ratio and dpi and float(dpi) > 96 and not fr:          # 72 and 96 are writer defaults, not a statement about paper
        mpp_dpi = ratio * 0.0254 / float(dpi)
        detail.update(dpi=round(float(dpi), 2), mpp_dpi=round(mpp_dpi, 6))
        others = [v for v in (mpp_paper, mpp_bar) if v]
        ok = not others or any(agree(v, mpp_dpi) for v in others)
        if ok:
            return dict(mpp=mpp_dpi, confidence=0.8 if others else 0.7, detail=detail,
                        source="printed scale and the DPI of the file" +
                               (", confirmed by the paper size or the scale bar" if others else ""))
    if fr and fr["method"] != "scale_text":
        return dict(mpp=detail["frame"]["mpp"], confidence=0.8, source=f"frame ({fr['method']})", detail=detail)
    if fr:
        ok = mpp_bar is None or agree(mpp_bar, detail["frame"]["mpp"])
        return dict(mpp=detail["frame"]["mpp"], confidence=0.7 if ok else 0.4, detail=detail,
                    source="printed scale and the DPI of the file" + ("" if ok else "; the scale bar disagrees"))
    if mpp_paper and agree(mpp_bar, mpp_paper):
        return dict(mpp=mpp_paper, confidence=0.8, source="printed scale and paper size, confirmed by the scale bar",
                    detail=detail)
    if mpp_bar:
        return dict(mpp=mpp_bar, confidence=0.4 if mpp_paper else 0.6, detail=detail,
                    source="scale bar measured in the pixels" + ("; printed scale with paper size disagrees" if mpp_paper else ""))
    if mpp_paper:
        return dict(mpp=mpp_paper, confidence=0.5, source="printed scale and paper size (image taken as the whole sheet)",
                    detail=detail)
    why = "no scale statement" if not ratio else "a printed scale but neither DPI, paper size nor scale bar to size a pixel"
    if len(meta.get("scales") or []) > 1 and sc is None:
        why = "several different scales on the sheet"
    return dict(mpp=None, confidence=0.0, source=why, detail=detail)


def scale_from_printed(rooms: list[dict], k: float) -> dict | None:
    """m per source pixel implied by rooms that have both a printed area and a segmented outline."""
    v = [math.sqrt(r["area"] / (r["px"] / k / k)) for r in rooms
         if r.get("area") and r.get("outline") == "wall_segmentation" and r.get("px")]
    if len(v) < 3:
        return None
    v = np.sort(np.array(v))
    med = float(np.median(v))
    spread = float((np.percentile(v, 75) - np.percentile(v, 25)) / med)
    return dict(mpp=med, n=len(v), spread=round(spread, 3))


# ------------------------------------------------------------------------------------------------------- Layer 2
def _prior(db: DB, f: dict) -> dict:
    """What the drawing already holds: rooms of other formats, and rooms raster.interpret read from this file."""
    rows = db.run("""select e.entity_id, e.number, e.label,
                            bool_or(s.file_id = :f) as mine, bool_or(sf.format not in ('png', 'jpg')) as other,
                            bool_or(s.file_id <> :f and sf.format in ('png', 'jpg')) as other_raster,
                            exists (select from jstore.claim c where c.entity_id = e.entity_id and c.file_id = :f
                                    and c.method = 'ocr') as printed
                     from jstore.entity e join jstore.entity_source s using (entity_id)
                     join jstore.source_file sf on sf.file_id = s.file_id
                     where e.drawing_key = :d and e.kind = 'room' and e.status = 'active' group by 1, 2, 3""",
                  f=f["file_id"], d=f["drawing_key"])
    mine = [r for r in rows if r["mine"] and not r["other"] and not r["other_raster"]]
    numbered = [r for r in mine if r["number"]]
    described = [r for r in numbered if r["label"] or r["printed"]]
    return dict(other=sum(1 for r in rows if r["other"]), other_raster=sum(1 for r in rows if r["other_raster"]),
                mine=[r["entity_id"] for r in mine], numbered=len(numbered),
                good=len(numbered) >= 3 and len(described) >= 0.5 * len(numbered))


def _nothing(db: DB, f: dict, reason: str, extra: dict | None = None) -> dict:
    define(db, f["file_id"], "room", "reader", dict(source="raster_rooms", created=0, reason=reason, **(extra or {})),
           0.0, BY)
    return dict(new=0, matched=0, unlinked=0, reason=reason)


def _polygon(r: dict, k: float):
    """Outline of a room in source pixels (shapely), from its region mask or its box."""
    from shapely.geometry import Polygon, box
    x0, y0, x1, y1 = r["bbox"]
    if r.get("region") is not None:
        cs, _ = cv2.findContours(r["region"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cs:
            c = cv2.approxPolyDP(max(cs, key=cv2.contourArea), 1.2, True).reshape(-1, 2)
            if len(c) >= 3:
                p = Polygon([((x0 + x + 0.5) / k, (y0 + y + 0.5) / k) for x, y in c]).buffer(0)
                if not p.is_empty and p.geom_type == "Polygon":
                    return p
    return box(x0 / k, y0 / k, x1 / k, y1 / k)


def interpret(db: DB, f: dict) -> dict:
    """Rooms of one raster file that nothing else has read.  -> merge() counts (plus `reason` when nothing is added)."""
    from shapely.affinity import affine_transform
    from ..pipeline import store_dir
    t0 = time.time()
    deadline = t0 + TIMEOUT_S
    prior = _prior(db, f)
    if prior["other"]:
        return _nothing(db, f, f"the drawing already has {prior['other']} rooms from a CAD, IFC or PDF source")
    if prior["other_raster"]:
        return _nothing(db, f, f"another image of the drawing already gave {prior['other_raster']} rooms")
    if prior["good"] and os.environ.get("JSTORE_RASTER_ROOMS_FORCE") != "1":      # FORCE: for comparing the readers
        return _nothing(db, f, f"raster.interpret already read {prior['numbered']} numbered rooms with names or "
                               "printed areas from this file")
    page = db.one("select * from jstore.page where file_id = :f and page_no = 0", f=f["file_id"])
    path = next((p for p in ([store_dir() / page["image"]] if page and page.get("image") else []) +
                 [os.path.join(os.environ.get("JSTORE_DATASET_DIR", ""), f["path"])] if os.path.exists(p)), None)
    if page is None or path is None:
        return _nothing(db, f, "page image not found")
    im = Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    W, H = im.size
    dpi_of_file = None
    orig = os.path.join(os.environ.get("JSTORE_DATASET_DIR", ""), f["path"])
    for src in (im, orig):                                # the stored page image may be a re-written copy without DPI
        try:
            info = src.info if src is im else Image.open(src).info
            if info.get("dpi") and float(info["dpi"][0]) > 1:
                dpi_of_file = float(info["dpi"][0])
                break
        except Exception:  # noqa: BLE001 - no such file, odd DPI tag
            pass
    if (page["image_w"], page["image_h"]) != (W, H):
        page = dict(page, image_w=W, image_h=H)
    words = db.run("select id, text, conf, x0, y0, x1, y1 from jstore.raster_word where file_id = :f and page_no = 0",
                   f=f["file_id"])
    toks = _ocr_tokens(words)
    log: dict = {}
    g = read_labels(im, deadline - 25, log)
    if g is not None and g["kind"] != "floor_plan":
        return _nothing(db, f, f"not a floor plan: the sheet shows a {g['kind'].replace('_', ' ')}", dict(gemini=log))
    rooms = g["rooms"] if g is not None else labels_from_ocr(toks)
    detector = f"raster_rooms {VERSION} {log.get('model', 'ocr')}"
    db.run("delete from jstore.raster_detection where file_id = :f and detector like 'raster_rooms %'", f=f["file_id"])
    if not rooms:
        return _nothing(db, f, "no room labels were read" + ("" if g is not None else f" by OCR (Gemini: {log.get('gemini')})"),
                        dict(gemini=log, ocr_lines=len(words)))
    attach_ocr(rooms, toks)

    # the pixels, small enough to segment
    k = min(1.0, MAX_WORK_PX / max(W, H))
    gray = np.asarray(im.convert("L") if k == 1.0 else
                      im.convert("L").resize((max(1, round(W * k)), max(1, round(H * k))), Image.LANCZOS))
    seg_log: dict = {}
    try:
        segment(gray, rooms, k, [t["box"] for t in toks] + [r["label_box"] for r in rooms], deadline - 8, seg_log)
    except MemoryError:
        seg_log["error"] = "out of memory"
    scale = find_scale(db, f, page, toks, g, gray, k, dpi_of_file)
    by_areas = scale_from_printed(rooms, k)
    if by_areas:
        scale["detail"]["printed_areas"] = dict(by_areas, mpp=round(by_areas["mpp"], 6))
        if scale["mpp"] is None and by_areas["spread"] < 0.12:
            scale.update(mpp=by_areas["mpp"], confidence=0.5,
                         source=f"the printed areas of {by_areas['n']} rooms over their outlines in pixels")
        elif scale["mpp"] and abs(by_areas["mpp"] / scale["mpp"] - 1) > 0.12 and by_areas["spread"] < 0.12:
            scale["confidence"] = min(scale["confidence"], 0.3)
            scale["source"] += "; the printed room areas imply another scale"
    mpp = scale["mpp"]

    # Layer 1: what Gemini returned, inspectable
    det_id: dict[int, int] = {}
    if g is not None:
        for i, r in enumerate(rooms):
            lb = r["label_box"]
            det_id[i] = db.one(
                """insert into jstore.raster_detection (file_id, page_no, class, score, x0, y0, x1, y1, detector, attrs, geom)
                   values (:f, 0, 'room_label', null, cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8), cast(:y1 as float8), :det, cast(:a as jsonb),
                           st_makeenvelope(cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8), cast(:y1 as float8))) returning id""",
                f=f["file_id"], x0=lb[0], y0=lb[1], x1=lb[2], y1=lb[3], det=detector,
                a=js(dict(name=r["name"], number=r["number"], area_m2=r["area"], area_text=r["area_text"],
                          outdoor=r["outdoor"], room_box=r["room_box"], view=r["view"])))["id"]
        if g.get("scale_bar"):
            b = g["scale_bar"]["box"]
            db.run("""insert into jstore.raster_detection (file_id, page_no, class, x0, y0, x1, y1, detector, attrs, geom)
                      values (:f, 0, 'scale_bar', cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8), cast(:y1 as float8), :det, cast(:a as jsonb),
                              st_makeenvelope(cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8), cast(:y1 as float8)))""",
                   f=f["file_id"], x0=b[0], y0=b[1], x1=b[2], y1=b[3], det=detector,
                   a=js(dict(length=g["scale_bar"]["length"], unit=g["scale_bar"]["unit"])))

    seg_n = sum(1 for r in rooms if r.get("outline") == "wall_segmentation")
    box_n = sum(1 for r in rooms if r.get("outline") == "gemini_box")
    d_units = define(db, f["file_id"], "units", "reader", dict(
        metres_per_pixel=mpp, how=scale["source"], detail=scale["detail"],
        rule="a measured area is written only when a pixel can be sized: frame of the page, printed scale with paper "
             "size, scale bar, or >= 3 printed room areas that agree"), scale["confidence"], BY)
    d_room = define(db, f["file_id"], "room", "reader", dict(
        source="raster_rooms", version=VERSION,
        rule="every room label read on the plan is a room; its outline is the free space between the walls that "
             "holds the label, cut from the neighbours at the narrowest passages",
        labels_from="gemini" if g is not None else "ocr", gemini=log, created=len(rooms),
        with_number=sum(1 for r in rooms if r["number"]), printed_area_read=sum(1 for r in rooms if r["area"]),
        labels_also_in_ocr=sum(1 for r in rooms if r.get("word")),
        outline=dict(wall_segmentation=seg_n, gemini_box=box_n, none=len(rooms) - seg_n - box_n, **seg_log),
        measured_areas="written" if mpp else f"not written: {scale['source']}",
        work_scale=round(k, 4), seconds=round(time.time() - t0, 1)),
        0.7 if seg_n >= 0.6 * len(rooms) else 0.5 if g is not None else 0.4, BY)

    fr = db.one("select m from jstore.frame where file_id = :f and page_no = 0 and method <> 'pixel_scale'",
                f=f["file_id"])
    m = list(fr["m"]) if fr else [mpp, 0, 0, 0, -mpp, mpp * H] if mpp else None
    if not fr and m:   # record how pixels map to metres, so the outlines can be placed on the page again
        db.run("""insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, cast(:m as double precision[]),
                  'pixel_scale') on conflict (file_id, page_no) do update set m = excluded.m, method = excluded.method""",
               f=f["file_id"], m="{" + ",".join(repr(float(v)) for v in m) + "}")
    recs = []
    for i, r in enumerate(rooms):
        label = r["name"] or None
        outdoor = r["outdoor"] or not vocab.indoor(label)
        lb = [round(float(v), 1) for v in r["label_box"]]
        if r.get("word"):
            sources = [("raster_word", str(r["word"]["id"]), "label", [round(float(v), 1) for v in r["word"]["box"]], d_room)]
        elif i in det_id:
            sources = [("raster_detection", str(det_id[i]), "label", lb, d_room)]
        else:
            sources = [("region", f"label {i}", "label", lb, d_room)]
        claims = []
        if r["area"] is not None:
            aw = r.get("area_word")
            if aw:
                sources.append(("raster_word", str(aw["id"]), "area_text", [round(float(v), 1) for v in aw["box"]], d_room))
                claims.append(("net_area_m2", r["area"], aw["text"], "m2", "ocr", len(sources) - 1, 0.8))
            else:
                claims.append(("net_area_m2", r["area"], r["area_text"], "m2", "printed", 0, 0.6))
        attrs = dict(indoor=not outdoor, read_by="gemini " + r["view"] if g is not None else "ocr",
                     outline=r.get("outline") or "none")
        shape = None
        if r.get("outline"):
            poly = _polygon(r, k)
            bb = [round(v / k, 1) for v in r["bbox"]]
            px_src = r["px"] / k / k
            attrs.update(outline_px=round(px_src), barrier=r.get("barrier"), box_fit=r.get("fit"),
                         geom="outline of the pixel region" if r["outline"] == "wall_segmentation" else
                              "box drawn by Gemini around the room")
            sources.append(("region", f"room {i}", "outline", bb, d_room))
            if m:
                shape = affine_transform(poly, [m[0], m[1], m[3], m[4], m[2], m[5]])
            if mpp:
                conf = round(scale["confidence"] * (0.7 if r["outline"] == "wall_segmentation" else 0.3), 2)
                claims.append(("net_area_m2", round(px_src * mpp * mpp, 2), None, "m2", "measured_outline",
                               len(sources) - 1, conf))
        recs.append(dict(kind="room", ifc_class="IfcSpace", number=r["number"], label=label,
                         label_norm=vocab.room_type(label), zone=None, geom=shape, attrs=attrs, sources=sources,
                         claims=claims))
    if prior["mine"]:                                     # what raster.interpret left: not a numbered plan it can read
        db.run("""update jstore.entity set status = 'superseded',
                         attrs = attrs || '{"superseded_by": "jstore.interpret.raster_rooms"}'::jsonb
                  where entity_id = any(cast(:ids as bigint[]))""", ids=prior["mine"])
    out = merge(db, f, recs, registered=False)
    if not mpp:
        issue(db, f, "no_scale", f"{f['path']}: rooms were read but no measured area is given ({scale['source']}); "
                                 "printed areas are stored where the plan shows them")
    return dict(out, seconds=round(time.time() - t0, 1), gemini_calls=log.get("calls", 0))
