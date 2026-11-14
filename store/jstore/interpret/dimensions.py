"""Dimensions and sizes printed on a sheet: dimension lines, chains, overall dimensions, room sizes, areas, levels.

    interpret(db, f)   Layer 2 entities of kind 'dimension' for one source file (called by interpret.run)
    scale_rung(...)    metres per pixel of a scan from dimension lines whose two ends are known (raster_rooms.find_scale)
    parse(text, hint)  one printed string -> {"kind", "value_m" | "a_m","b_m" | "area_m2", "unit", ...} or None

Sources
  CAD          DIMENSION entities (measured value, definition points, text override) - exact.
  PDF, scans   a Gemini pass over tiles of the page image returns every dimension string with the two ends of its
               dimension line (cached like the other readers; one overview call decides whether a page has any).
               The texts of Layer 1 under each string (pdf_text, raster_word) are linked as sources.
A dimension is read once per drawing: the first file (CAD, then PDF, then image) that has any supplies them.

Each record: geom = the dimension line in the drawing frame (null when the page has no frame), attrs = {text,
value_m, unit, orientation, dim_kind: linear | overall | room_size | area | level, room, page_no, px: the line and
text box in page pixels, check: printed value over the length of the line in the frame}.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import base64
import math
import os
import re
import time

from ..db import DB, js
from . import define, merge

BY = "jstore.interpret.dimensions"
VERSION = "dm-1"
MODEL = os.environ.get("JSTORE_DIMENSIONS_MODEL", "gemini-4-carbon")
THINKING = "low"
VIEW_SIDE = 1400              # long side of a tile as sent
TILE_PX = 1100                # source pixels a tile covers at most (text of 10 px stays readable)
MAX_TILES = int(os.environ.get("JSTORE_DIMENSIONS_MAX_TILES", "12"))      # per page
MAX_PAGES = int(os.environ.get("JSTORE_DIMENSIONS_MAX_PAGES", "6"))
BUDGET_S = float(os.environ.get("JSTORE_DIMENSIONS_TIMEOUT_S", "90"))
CALL_TIMEOUT = 60.0
AGREE = 0.06                  # two dimension lines imply the same scale within this
FT, IN = 0.3048, 0.0254
UNIT_M = {"m": 1.0, "cm": 0.01, "mm": 0.001, "ft": FT, "in": IN}

# ------------------------------------------------------------------------------------------------------ parsing
_QUOTES = str.maketrans({"″": '"', "”": '"', "“": '"', "′": "'", "’": "'", "‘": "'",
                         "´": "'", "`": "'", "×": "x", "✕": "x", "–": "-", "—": "-",
                         "−": "-", "½": " 1/2", "¼": " 1/4", "¾": " 3/4", "²": "2",
                         " ": " "})
_SUP = str.maketrans("⁰¹³⁴⁵⁶⁷⁸⁹", "013456789")
_NUM = r"\d+(?:[.,]\d+)?"
_FRAC = r"\d+\s*/\s*\d+"
# 12'-6"  12' - 6 1/2"  12'6  12'  6"  4 1/2"  3/4"  12 ft 6 in  12ft
_IMPERIAL = re.compile(
    rf"""^(?:(?P<ft>{_NUM})\s*(?:'|ft\.?|feet|foot)\s*-?\s*)?
         (?:(?P<in>{_NUM})??\s*-?\s*(?P<frac>{_FRAC})?\s*(?:"|''|in\.?|inch(?:es)?)?)?$""", re.X | re.I)
_METRIC = re.compile(rf"^(?P<v>{_NUM})\s*(?P<u>mm|cm|m|dm)\.?$", re.I)
_BARE = re.compile(rf"^(?P<v>{_NUM})$")
_AREA = re.compile(rf"""^(?:(?:area|fl(?:ä|ae)che|nf|ngf|bgf|wfl\.?)\s*[:=]?\s*)?(?:ca\.?\s*)?(?P<v>\d+(?:[.,]\d+)?)\s*
                        (?P<u>m\s?2|m\^2|qm|sq\.?\s?m\.?|sqm|sf|s\.f\.|sq\.?\s?ft\.?|sqft|ft\s?2)$""", re.X | re.I)
_SPLIT = re.compile(r"\s*(?:x|\*|\bby\b)\s*", re.I)
_LEVEL = re.compile(r"^(?:(?:el(?:ev)?\.?|ffl|fgl|ok|uk|t\.?o\.?\s?\w*|b\.?o\.?\s?\w*|okff|okrf|okfb|level|niveau)\s*[:=.]?\s*)?"
                    r"(?P<s>[+\-±]|\+/-)?\s*(?P<rest>.+)$", re.I)
_NOISE = re.compile(r"\b(?:typ(?:ical)?\.?|clr\.?|clear|min\.?|max\.?|approx\.?|ca\.?|v\.?i\.?f\.?|r\.?o\.?|o\.?c\.?|"
                    r"nom\.?|eq\.?|\+/-)\b|[()±~≈]", re.I)


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTES).translate(_SUP)).strip()


def parse_length(text: str, hint: str | None = None) -> dict | None:
    """One printed length -> {"value_m", "unit": as printed (m | cm | mm | ft-in | in | ft) or None, "inferred"}.

    `hint` is the unit of bare numbers on this sheet (m | cm | mm | in | ft); without it a bare decimal below 100 is
    taken as metres ("3.60") and other bare numbers stay without a value (value_m None, unit None).
    """
    s = _NOISE.sub(" ", _clean(text))
    s = re.sub(r"\s+", " ", s).strip(" .:=")
    if not s or not re.search(r"\d", s):
        return None
    m = _METRIC.match(s)
    if m:
        u = m["u"].lower()
        return dict(value_m=_f(m["v"]) * (0.1 if u == "dm" else UNIT_M[u]), unit=u, inferred=False)
    m = _BARE.match(s)
    if m:
        v = _f(m["v"])
        if hint in UNIT_M:
            return dict(value_m=v * UNIT_M[hint], unit=hint, inferred=True)
        if re.search(r"[.,]\d", m["v"]) and v < 100:
            return dict(value_m=v, unit="m", inferred=True)
        return dict(value_m=None, unit=None, inferred=True, number=v)
    m = _IMPERIAL.match(s)
    if m and (m["ft"] or re.search(r"\"|''|in|/", s, re.I)):
        ft = _f(m["ft"]) if m["ft"] else 0.0
        inch = _f(m["in"]) if m["in"] else 0.0
        if m["frac"]:
            a, b = (float(x) for x in re.split(r"\s*/\s*", m["frac"]))
            if b == 0:
                return None
            inch += a / b
        if inch >= 12 and m["ft"]:
            return None
        return dict(value_m=round(ft * FT + inch * IN, 5), inferred=False,
                    unit="ft-in" if m["ft"] and (m["in"] or m["frac"]) else "ft" if m["ft"] else "in")
    return None


def parse(text: str, hint: str | None = None) -> dict | None:
    """One printed string -> what it states, or None when it is not a dimension.

    area       {"kind": "area", "area_m2", "unit"}
    room size  {"kind": "room_size", "a_m", "b_m", "value_m" (the larger), "unit"}
    level      {"kind": "level", "value_m", "unit"}
    length     {"kind": "linear", "value_m", "unit"}
    """
    s = _clean(text)
    if not s or len(s) > 40:
        return None
    m = _AREA.match(s)
    if m:
        u = re.sub(r"[\s.^]", "", m["u"].lower())
        metric = u in ("m2", "qm", "sqm")
        return dict(kind="area", area_m2=round(_f(m["v"]) * (1.0 if metric else FT * FT), 4), unit="m2" if metric else "sf")
    parts = _SPLIT.split(s)
    if len(parts) == 2 and all(re.search(r"\d", p) for p in parts):
        b = parse_length(parts[1], hint)
        unit = b and b["unit"]
        a = parse_length(parts[0], hint)
        if a and a["value_m"] is None and b and not b["inferred"] and unit in UNIT_M:   # "320 x 410 cm"
            a = parse_length(parts[0], unit)
        if b and b["value_m"] is None and a and not a["inferred"] and a["unit"] in UNIT_M:
            b = parse_length(parts[1], a["unit"])
        if a and b and a["value_m"] and b["value_m"]:
            return dict(kind="room_size", a_m=a["value_m"], b_m=b["value_m"], value_m=max(a["value_m"], b["value_m"]),
                        unit=a["unit"] or b["unit"])
        return None
    lv = _LEVEL.match(s)
    if lv and (lv["s"] or lv["rest"] != s):
        dec = re.fullmatch(r"\d+[.,]\d+", lv["rest"].strip())        # "+104.50", "-0,400": a level in metres, whatever
        v = dict(value_m=_f(dec[0]), unit="m") if dec else parse_length(lv["rest"], hint)   # unit the lengths are in
        if v and v["value_m"] is not None:
            sign = -1.0 if lv["s"] == "-" else 1.0
            return dict(kind="level", value_m=sign * v["value_m"], unit=v["unit"])
        return None
    v = parse_length(s, hint)
    if v is None:
        return None
    return dict(kind="linear", **v)


def infer_unit(texts: list[str], stated: str | None = None) -> str | None:
    """Unit of the bare numbers of a sheet: what the sheet states, else what its other dimension strings show."""
    stated = (stated or "").lower().strip()
    if stated in ("m", "cm", "mm"):
        return stated
    if stated in ("ft-in", "ft", "in", "imperial"):
        return "in"
    kinds = [parse_length(t) for t in texts]
    kinds = [k for k in kinds if k]
    if sum(1 for k in kinds if k["unit"] in ("ft-in", "ft", "in")) >= max(2, 0.3 * len(kinds)):
        return "in"
    bare = sorted(k["number"] for k in kinds if k.get("number"))
    if len(bare) >= 3:
        med = bare[len(bare) // 2]
        return "mm" if med >= 1000 else "cm"
    return None


# ------------------------------------------------------------------------------------------------------ scale
def scale_from(pairs: list[tuple[float, float]]) -> dict | None:
    """Metres per pixel from (length in pixels, printed metres) of dimension lines.

    The ratios of honest dimension lines agree; misread strings and wrong ends scatter.  The largest group within
    AGREE of its median wins; 3 agreeing lines are the least that counts.
    -> {"mpp", "n", "of", "spread", "confidence"} or None
    """
    r = sorted(v / px for px, v in pairs if px > 0 and v and v > 0)
    best: list[float] = []
    for i, lo in enumerate(r):
        grp = [x for x in r[i:] if x <= lo * (1 + 2 * AGREE)]
        if len(grp) > len(best):
            best = grp
    if len(best) < 3:
        return None
    med = best[len(best) // 2] if len(best) % 2 else (best[len(best) // 2 - 1] + best[len(best) // 2]) / 2
    share = len(best) / len(r)
    spread = (best[-1] - best[0]) / med
    conf = 0.7 if len(best) >= 6 and share >= 0.6 else 0.6 if share >= 0.5 else 0.45
    return dict(mpp=med, n=len(best), of=len(r), spread=round(spread, 4), confidence=conf)


def best_unit(items: list[dict], stated: str | None) -> str | None:
    """Unit of bare numbers: stated or seen on the sheet, else the one under which most bare numbers agree with the
    scale that the unit-carrying dimension lines give (or, without any, with each other)."""
    hint = infer_unit([i["text"] for i in items], stated)
    if hint or not any(i.get("line") for i in items):
        return hint
    return None


# ------------------------------------------------------------------------------------------------------ Gemini
SCHEMA = {"type": "object", "properties": {"dimensions": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"},
    "kind": {"type": "string", "enum": ["linear", "room_size", "area", "level"]},
    "text_box": {"type": "array", "items": {"type": "integer"}},
    "p1": {"type": "array", "items": {"type": "integer"}},
    "p2": {"type": "array", "items": {"type": "integer"}},
    "room": {"type": "string"}}, "required": ["text", "kind", "text_box"]}}}, "required": ["dimensions"]}
PROMPT = (
    "The image is a part of an architectural or construction drawing, enlarged. Read every DIMENSION printed in it.\n"
    "- linear: a number written along a dimension line (a thin line between two ticks, arrows, dots or extension "
    "lines), e.g. 3.60, 360, 12'-6\", 4 1/2\". Give `p1` and `p2`: the two ENDS of that dimension line, exactly "
    "where its ticks / arrow heads / extension lines are (not the ends of the text). In a chain of dimensions "
    "every segment is its own entry with its own two ends.\n"
    "- room_size: the size of a room written inside it, e.g. 3.20 x 4.10 or 12'0\" x 10'6\". No p1/p2. `room` = the "
    "room name or number printed with it.\n"
    "- area: a printed floor area, e.g. 12.5 m2, 147 SF. No p1/p2. `room` = the room name or number printed with it.\n"
    "- level: a height mark such as +2.80 or EL. 100'-0\". No p1/p2.\n"
    "`text`: exactly as printed, with units and fraction marks. `text_box`: [ymin, xmin, ymax, xmax] around the "
    "text. All coordinates are integers from 0 to 1000 relative to this image, p1 and p2 as [y, x].\n"
    "Do NOT report: room numbers, door or window tags, grid bubbles, sheet numbers, scales (1:100), dates, phone "
    "numbers, quantities in schedules, a dimension whose line is cut off by the image border. Report nothing "
    "when there are no dimensions.")
SCHEMA_PAGE = {"type": "object", "properties": {
    "dimension_strings": {"type": "string", "enum": ["none", "few", "many"]},
    "units": {"type": "string", "enum": ["m", "cm", "mm", "ft-in", "unknown"]}},
    "required": ["dimension_strings", "units"]}
PROMPT_PAGE = (
    "The image is a whole sheet (drawing, scan or page). Does it show printed dimensions: numbers along dimension "
    "lines, room sizes such as 3.20 x 4.10, printed floor areas such as 12.5 m2 or 147 SF, height marks? "
    "`dimension_strings`: none | few (up to about 10) | many. `units`: the unit bare dimension numbers on this "
    "sheet are in (m, cm, mm), ft-in for feet and inches, unknown when it cannot be told.")


def _generate(view, prompt: str, schema: dict, stats) -> dict | None:
    """Structured answer to one view, from the cache or the API (gemini_detect's request helper and cache)."""
    from .. import gemini_detect as G
    png = G._png(view)
    key = hashlib.sha256(png + f"|{MODEL}|{THINKING}|{VERSION}|{prompt}|{json.dumps(schema, sort_keys=True)}"
                         .encode()).hexdigest()
    hit = G._cached(key)
    if hit is not None:
        stats.count(MODEL, True)
        return hit["answer"]
    body = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}}, {"text": prompt}]}],
        "generationConfig": {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}},
                             "thinkingConfig": {"thinkingLevel": THINKING}}}
    t = time.time()
    answer, why, out = None, "", {}
    for _ in range(2):
        status, out = G._post(f"/models/{MODEL}:generateContent", body, stats, CALL_TIMEOUT)
        if status != -2:
            stats.count(MODEL, False)
        if status != 200:
            why = G._err(status, out)
            break
        try:
            parts = out["candidates"][0]["content"]["parts"]
            answer = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            break
        except (KeyError, IndexError, ValueError, TypeError) as e:
            why = f"unreadable answer: {type(e).__name__}"
    if not isinstance(answer, dict):
        stats.error(f"dimensions: {why or 'answer is not an object'}")
        return None
    G._store(key, dict(model=MODEL, version=VERSION, answer=answer, seconds=round(time.time() - t, 1),
                       usage=out.get("usageMetadata"), view_px=list(view.size)))
    return answer


def _pt(p, rect):
    if not isinstance(p, list) or len(p) != 2:
        return None
    x0, y0, x1, y1 = rect
    try:
        return (x0 + float(p[1]) / 1000 * (x1 - x0), y0 + float(p[0]) / 1000 * (y1 - y0))
    except (TypeError, ValueError):
        return None


def items_of(answer: dict, rect) -> list[dict]:
    """Entries of one tile's answer in page pixels: {"text", "said", "box", "line" or None, "room", "edge"}."""
    out = []
    x0, y0, x1, y1 = rect
    for d in (answer or {}).get("dimensions") or []:
        tb = d.get("text_box")
        text = (d.get("text") or "").strip()
        if not text or not isinstance(tb, list) or len(tb) != 4:
            continue
        a, b = _pt([tb[0], tb[1]], rect), _pt([tb[2], tb[3]], rect)
        if a is None or b is None:
            continue
        box = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
        p1, p2 = _pt(d.get("p1"), rect), _pt(d.get("p2"), rect)
        line = [p1, p2] if p1 and p2 and math.dist(p1, p2) > 3 else None
        # how close the entry comes to the border of its tile (the neighbouring tile sees it whole)
        pts = (line or []) + [(box[0], box[1]), (box[2], box[3])]
        edge = min(min(p[0] - x0, x1 - p[0], p[1] - y0, y1 - p[1]) for p in pts)
        out.append(dict(text=text, said=d.get("kind") or "linear", box=box, line=line,
                        room=(d.get("room") or "").strip() or None, edge=edge))
    return out


def _key(text: str) -> str:
    return _clean(text).replace(" ", "").lower()


def dedupe(items: list[dict]) -> list[dict]:
    """Overlapping tiles report a string twice: keep the reading that lies deeper inside its tile."""
    keep: list[dict] = []
    for it in sorted(items, key=lambda i: -i["edge"]):
        cx, cy = (it["box"][0] + it["box"][2]) / 2, (it["box"][1] + it["box"][3]) / 2
        size = max(it["box"][2] - it["box"][0], it["box"][3] - it["box"][1], 8.0)
        if any(abs(cx - (k["box"][0] + k["box"][2]) / 2) < 0.7 * size and
               abs(cy - (k["box"][1] + k["box"][3]) / 2) < 0.7 * size and
               (_key(k["text"]).endswith(_key(it["text"])) or _key(it["text"]).endswith(_key(k["text"]))) for k in keep):
            continue
        keep.append(it)
    return keep


def tiles_of(W: int, H: int) -> list[tuple[int, int, int, int]]:
    from .. import gemini_detect as G
    return G.grid((0, 0, W, H), TILE_PX, 0.2, MAX_TILES)


def read_page(im, stats, log: dict) -> dict | None:
    """Every dimension string of one page image.  -> {"items": [...], "units": stated unit} or None (no answer)."""
    from PIL import Image
    from .. import gemini_detect as G
    W, H = im.size
    page = _generate(G._fit(im, 1536), PROMPT_PAGE, SCHEMA_PAGE, stats)
    if page is None:
        return None
    log.update(overview=page)
    if page.get("dimension_strings") == "none":
        return dict(items=[], units=page.get("units"))
    tiles = tiles_of(W, H)
    tiles = [t for t in tiles if G._has_ink(im, t)]

    def one(rect):
        view = im.crop(rect)
        up = min(3.0, VIEW_SIDE / max(view.size))
        if abs(up - 1) > 0.05:
            view = view.resize((max(1, round(view.width * up)), max(1, round(view.height * up))), Image.LANCZOS)
        return items_of(_generate(view, PROMPT, SCHEMA, stats) or {}, rect)

    with cf.ThreadPoolExecutor(min(6, max(1, len(tiles)))) as ex:
        found = [i for part in ex.map(one, tiles) for i in part]
    log.update(tiles=len(tiles), raw=len(found))
    return dict(items=dedupe(found), units=page.get("units"))


def understand(items: list[dict], stated: str | None) -> str | None:
    """Parse every item in place (adds "parsed": parse() result or None).  -> the unit bare numbers were read in."""
    hint = best_unit(items, stated)
    lines = [(math.dist(*i["line"]), i) for i in items if i.get("line")]
    if hint is None and lines:
        # no stated unit: the one under which the bare numbers agree best with each other and with the rest
        best = (0, None)
        for u in ("cm", "m", "mm"):
            pairs = [(px, p["value_m"]) for px, i in lines
                     for p in [parse(i["text"], u)] if p and p["kind"] == "linear" and p.get("value_m")]
            sc = scale_from(pairs)
            if sc and sc["n"] > best[0]:
                best = (sc["n"], u)
        hint = best[1]
    for i in items:
        p = parse(i["text"], hint)
        words = _clean(i["text"]).split(" ")
        while p is None and i["said"] == "level" and len(words) > 1:      # "MEZZANINE 9' - 3\"": the name of the level
            words = words[1:]
            p = parse(" ".join(words), hint)
            if p and p["kind"] not in ("linear", "level"):
                p = None
        if p and p["kind"] == "linear" and i["said"] == "level" and not i.get("line"):
            p["kind"] = "level"
        if p and p["kind"] == "linear" and p.get("value_m") is None:
            p = None
        i["parsed"] = p
    return hint


def page_scale(items: list[dict]) -> dict | None:
    """Metres per page pixel from the parsed linear dimensions that have a line."""
    return scale_from([(math.dist(*i["line"]), i["parsed"]["value_m"]) for i in items
                       if i.get("line") and i.get("parsed") and i["parsed"]["kind"] == "linear"])


def _open(db: DB, f: dict, page: dict):
    from PIL import Image
    from ..pipeline import store_dir
    for p in ([store_dir() / page["image"]] if page.get("image") else []) + \
             [os.path.join(os.environ.get("JSTORE_DATASET_DIR", ""), f["path"])]:
        if os.path.isfile(p) and str(p).lower().endswith((".png", ".jpg", ".jpeg")):
            Image.MAX_IMAGE_PIXELS = None
            im = Image.open(p)
            return im if im.mode == "RGB" else im.convert("RGB")
    return None


def scale_rung(db: DB, f: dict, page: dict, detail: dict | None = None, budget_s: float = 30.0) -> dict | None:
    """The rung of raster_rooms.find_scale for scans without a scale statement: metres per pixel from printed
    dimensions.  -> {mpp, confidence, source, detail} or None.  The Gemini answers are cached, so interpret() below
    reads the same sheet without asking again."""
    from .. import gemini_detect as G
    if os.environ.get("JSTORE_DIMENSIONS", "on") == "off" or not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        im = _open(db, f, page)
        if im is None:
            return None
        log: dict = {}
        got = read_page(im, G.Stats(deadline=time.time() + budget_s), log)
        if not got or not got["items"]:
            return None
        understand(got["items"], got["units"])
        sc = page_scale(got["items"])
    except Exception:  # noqa: BLE001 - a rung that fails is a rung that says nothing
        return None
    if sc is None:
        return None
    k = im.size[0] / (page.get("image_w") or im.size[0])      # the stored page image may be a scaled copy
    detail = dict(detail or {}, dimensions=dict(n=sc["n"], of=sc["of"], spread=sc["spread"]))
    return dict(mpp=sc["mpp"] * k, confidence=sc["confidence"], detail=detail,
                source=f"{sc['n']} of {sc['of']} printed dimension lines agree on the scale")


# ------------------------------------------------------------------------------------------------------ Layer 2
def _inv(m):
    a, b, c, d, e, f = m
    det = a * e - b * d
    return [e / det, -b / det, (b * f - c * e) / det, -d / det, a / det, (c * d - a * f) / det]


def _ap(m, p):
    return (m[0] * p[0] + m[1] * p[1] + m[2], m[3] * p[0] + m[4] * p[1] + m[5])


def orientation(p, q) -> str:
    ang = abs(math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))) % 180
    return "horizontal" if ang < 10 or ang > 170 else "vertical" if abs(ang - 90) < 10 else "oblique"


def _rooms(db: DB, drawing_key: str) -> list[dict]:
    from shapely import from_wkb
    rows = db.run("""select entity_id, number, label, encode(st_asbinary(geom), 'hex') as wkb from jstore.entity
                     where drawing_key = :d and kind = 'room' and status = 'active' and geom is not null""", d=drawing_key)
    return [dict(r, shape=from_wkb(r["wkb"])) for r in rows]


def _room_at(rooms: list[dict], pt, reach: float = 0.0):
    from shapely.geometry import Point
    p = Point(pt)
    hit = [(r["shape"].area, r) for r in rooms if r["shape"].distance(p) <= reach]
    return min(hit, key=lambda t: t[0])[1] if hit else None


def _room_named(rooms: list[dict], name: str | None):
    key = re.sub(r"\W+", "", (name or "").lower())
    if not key:
        return None
    hit = [r for r in rooms if key in (re.sub(r"\W+", "", (r["number"] or "").lower()),
                                       re.sub(r"\W+", "", (r["label"] or "").lower()))]
    return hit[0] if len(hit) == 1 else None


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def cad_records(db: DB, f: dict, rooms: list[dict]) -> tuple[list[dict], dict]:
    """DIMENSION entities of a CAD file."""
    from shapely.geometry import LineString
    rows = db.run("""select id, handle, text, attrs, st_x(st_startpoint(geom)) as x0, st_y(st_startpoint(geom)) as y0,
                            st_x(st_endpoint(geom)) as x1, st_y(st_endpoint(geom)) as y1, geometrytype(geom) as gt
                     from jstore.cad_entity where file_id = :f and dxftype = 'DIMENSION' and space = 'model'
                       and parent is null""", f=f["file_id"])
    spec = dict(source="cad DIMENSION entities", found=len(rows))
    if not rows:
        return [], spec
    fr = db.one("select m from jstore.frame where file_id = :f and page_no = 0", f=f["file_id"])
    m = fr["m"] if fr else None
    unit_m = math.hypot(m[0], m[3]) if m else None            # metres per drawing unit
    def_id = define(db, f["file_id"], "dimension", "reader", dict(spec, rule="measured value of the entity; text "
                    "override where one is set; geom = its two definition points"), 0.95, BY)
    recs = []
    for r in rows:
        meas = (r["attrs"] or {}).get("measurement")
        if not isinstance(meas, (int, float)) or meas <= 0 or (r["attrs"] or {}).get("dimtype", 0) & 7 in (2, 5):
            continue                                          # angular dimensions
        over = (r["text"] or "").strip()
        text = re.sub(r"\\[A-Za-z][^;]*;|\\X.*$|[{}]", "", over).replace("<>", _fmt(meas)).strip() or _fmt(meas)
        printed = parse(text) if over and "<>" not in over else None
        value = printed["value_m"] if printed and printed.get("value_m") else (meas * unit_m if unit_m else None)
        g, ori = None, None
        if r["gt"] == "LINESTRING":
            a, b = (r["x0"], r["y0"]), (r["x1"], r["y1"])
            ori = orientation(a, b)
            if m:
                g = LineString([_ap(m, a), _ap(m, b)])
        room = _room_at(rooms, g.interpolate(0.5, normalized=True).coords[0]) if g is not None else None
        attrs = dict(text=text, value_m=None if value is None else round(value, 4),
                     unit=(printed or {}).get("unit") or "drawing units", orientation=ori, dim_kind="linear",
                     measured_units=round(meas, 4), room=room and (room["number"] or room["label"]),
                     room_id=room and room["entity_id"])
        recs.append(dict(kind="dimension", label=text, host=room and room["number"], geom=g, attrs=attrs,
                         sources=[("cad_entity", r["handle"], "dimension", None, def_id)],
                         claims=[] if value is None else [("length_m", round(value, 4), text, "m", "printed", 0, 0.95)]))
    return recs, spec


def _link_texts(db: DB, f: dict, page: dict, items: list[dict]) -> None:
    """Layer 1 texts under each string (pdf_text in points, raster_word in pixels) -> item["l1"] = [(table, id, bbox)]."""
    inv = _inv(page["to_image"]) if page.get("to_image") else None
    if f["format"] == "pdf":
        rows = db.run("select id, x0, y0, x1, y1 from jstore.pdf_text where file_id = :f and page_no = :p",
                      f=f["file_id"], p=page["page_no"])
        table = "pdf_text"
    else:
        rows = db.run("select id, x0, y0, x1, y1 from jstore.raster_word where file_id = :f and page_no = :p",
                      f=f["file_id"], p=page["page_no"])
        table, inv = "raster_word", None
    for i in items:
        b = i["box"]
        if inv:
            (ax, ay), (bx, by) = _ap(inv, (b[0], b[1])), _ap(inv, (b[2], b[3]))
            b = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
        pad = 0.15 * max(b[2] - b[0], b[3] - b[1])
        i["l1"] = [(table, str(r["id"]), [r["x0"], r["y0"], r["x1"], r["y1"]]) for r in rows
                   if b[0] - pad <= (r["x0"] + r["x1"]) / 2 <= b[2] + pad and b[1] - pad <= (r["y0"] + r["y1"]) / 2 <= b[3] + pad][:6]


def image_records(db: DB, f: dict, rooms: list[dict], t0: float) -> tuple[list[dict], dict]:
    """Dimensions read on the page images of a PDF or scan."""
    from shapely.geometry import LineString, Point
    from .. import gemini_detect as G
    pages = db.run("""select page_no, image, image_w, image_h, to_image from jstore.page
                      where file_id = :f and image is not null order by page_no""", f=f["file_id"])
    spec: dict = dict(source="gemini tiles of the page image", model=MODEL, version=VERSION, pages=[])
    if not pages:
        return [], dict(spec, reason="no page image")
    if len(pages) > MAX_PAGES:
        spec["pages_left_out"] = [p["page_no"] for p in pages[MAX_PAGES:]]
    stats = G.Stats(deadline=t0 + BUDGET_S)
    detector = f"dimensions {VERSION} {MODEL}"
    recs: list[dict] = []
    per_page = []
    for page in pages[:MAX_PAGES]:
        im = _open(db, f, page)
        log: dict = dict(page=page["page_no"])
        spec["pages"].append(log)
        if im is None or stats.left() < 5:
            log["skipped"] = "no image" if im is None else "no time left"
            continue
        got = read_page(im, stats, log)
        if not got or not got["items"]:
            continue
        k = (page["image_w"] or im.size[0]) / im.size[0]      # tile pixels -> pixels of the page row
        items = got["items"]
        for i in items:
            i["box"] = [v * k for v in i["box"]]
            if i["line"]:
                i["line"] = [(p[0] * k, p[1] * k) for p in i["line"]]
        unit = understand(items, got["units"])
        sc = page_scale(items)
        fr = db.one("select m, method from jstore.frame where file_id = :f and page_no = :p", f=f["file_id"],
                    p=page["page_no"])
        to_file = _inv(page["to_image"]) if page.get("to_image") else [1, 0, 0, 0, 1, 0]
        m = fr["m"] if fr else None
        mpp_frame = math.hypot(m[0], m[3]) * math.hypot(to_file[0], to_file[3]) if m else None
        log.update(strings=len(items), parsed=sum(1 for i in items if i["parsed"]), bare_unit=unit,
                   scale=sc and dict(sc, mpp=round(sc["mpp"], 6)), frame=fr and fr["method"],
                   mpp_frame=mpp_frame and round(mpp_frame, 6),
                   scale_error=round(sc["mpp"] / mpp_frame - 1, 4) if sc and mpp_frame else None)
        _link_texts(db, f, page, items)
        per_page.append((page, items, m, to_file, sc, mpp_frame))
    if not per_page:
        return [], spec
    db.run("delete from jstore.raster_detection where file_id = :f and detector like 'dimensions %'", f=f["file_id"])
    def_id = define(db, f["file_id"], "dimension", "reader", spec, 0.7, BY)
    for page, items, m, to_file, sc, mpp_frame in per_page:
        on_first = page["page_no"] == per_page[0][0]["page_no"]
        span = {}                                             # extent of all dimension lines, per direction
        for i in items:
            if i["line"] and i["parsed"] and i["parsed"]["kind"] == "linear":
                ax = 0 if orientation(*i["line"]) == "horizontal" else 1
                lo, hi, n = span.get(ax, (math.inf, -math.inf, 0))
                span[ax] = (min(lo, i["line"][0][ax], i["line"][1][ax]), max(hi, i["line"][0][ax], i["line"][1][ax]), n + 1)
        for i in items:
            p = i["parsed"]
            b = i["box"]
            det = db.one(
                """insert into jstore.raster_detection (file_id, page_no, class, x0, y0, x1, y1, detector, attrs, geom)
                   values (:f, :p, 'dimension', cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8),
                           cast(:y1 as float8), :det, cast(:a as jsonb),
                           st_makeenvelope(cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8), cast(:y1 as float8)))
                   returning id""", f=f["file_id"], p=page["page_no"], x0=b[0], y0=b[1], x1=b[2], y1=b[3], det=detector,
                a=js(dict(text=i["text"], said=i["said"], line=i["line"], room=i["room"], parsed=p)))["id"]
            if not p:
                continue
            fb = [*_ap(to_file, (b[0], b[1])), *_ap(to_file, (b[2], b[3]))]
            g, ori, check = None, None, None
            if i["line"] and p["kind"] == "linear":
                ori = orientation(*i["line"])
                if m:
                    g = LineString([_ap(m, _ap(to_file, q)) for q in i["line"]])
                    check = round(p["value_m"] / g.length, 3) if g.length > 0 else None
            elif m:
                g = Point(_ap(m, _ap(to_file, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))))
            room = None
            if on_first or len(per_page) == 1:
                room = _room_named(rooms, i["room"]) if p["kind"] in ("room_size", "area") else None
                if room is None and g is not None:
                    mid = g.interpolate(0.5, normalized=True).coords[0] if g.geom_type == "LineString" else g.coords[0]
                    room = _room_at(rooms, mid, 0.0 if p["kind"] == "linear" else 0.3)
            kind = p["kind"]
            if kind == "linear" and i["line"] and ori in ("horizontal", "vertical"):
                lo, hi, n = span[0 if ori == "horizontal" else 1]
                if n >= 3 and math.dist(*i["line"]) >= 0.75 * (hi - lo):
                    kind = "overall"
            attrs = dict(text=i["text"], value_m=p.get("value_m"), unit=p.get("unit"), orientation=ori, dim_kind=kind,
                         room=(room and (room["number"] or room["label"])) or i["room"],
                         room_id=room and room["entity_id"], page_no=page["page_no"],
                         px=dict(line=i["line"] and [[round(v, 1) for v in q] for q in i["line"]],
                                 box=[round(v, 1) for v in b]), check=check)
            claims = []
            if kind == "room_size":
                attrs.update(a_m=p["a_m"], b_m=p["b_m"])
                claims = [("length_m", max(p["a_m"], p["b_m"]), i["text"], "m", "printed", 0, 0.8),
                          ("width_m", min(p["a_m"], p["b_m"]), i["text"], "m", "printed", 0, 0.8)]
            elif kind == "area":
                attrs.update(area_m2=p["area_m2"])
                claims = [("area_m2", p["area_m2"], i["text"], "m2", "printed", 0, 0.8)]
            elif kind == "level":
                claims = [("level_m", p["value_m"], i["text"], "m", "printed", 0, 0.8)]
            else:
                ok = check is None or abs(check - 1) < 0.1
                claims = [("length_m", p["value_m"], i["text"], "m", "printed", 0, 0.85 if ok else 0.5)]
            recs.append(dict(kind="dimension", label=i["text"], host=room and room["number"], geom=g, attrs=attrs,
                             page_no=page["page_no"], room_entity=room and room["entity_id"],
                             sources=[("raster_detection", str(det), "dimension", fb, def_id)] +
                                     [(t, key, "text", bb, def_id) for t, key, bb in i.get("l1", [])],
                             claims=claims))
    spec["requests"], spec["cached"], spec["errors"] = stats.calls, stats.cached, stats.errors[:5]
    db.run("update jstore.definition set spec = cast(:s as jsonb) where def_id = :d", s=js(spec), d=def_id)
    return recs, spec


def room_claims(db: DB, f: dict, recs: list[dict]) -> int:
    """Printed sizes and areas of rooms become claims on the room: an area only where the room has no printed one."""
    n = 0
    for r in recs:
        eid = r.get("room_entity")
        kind = r["attrs"]["dim_kind"]
        if not eid or kind not in ("room_size", "area"):
            continue
        src = db.one("""insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role, bbox, def_id)
                        values (:e, :f, :p, :t, :k, :role, cast(:b as float8[]), :d) returning id""",
                     e=eid, f=f["file_id"], p=r.get("page_no", 0), t=r["sources"][0][0], k=r["sources"][0][1],
                     role="area_text" if kind == "area" else "size_text", b=r["sources"][0][3], d=r["sources"][0][4])["id"]
        if kind == "area":
            have = db.one("""select count(*) as n from jstore.claim where entity_id = :e and property = 'net_area_m2'
                             and method in ('printed', 'ocr')""", e=eid)["n"]
            props = [] if have else [("net_area_m2", r["attrs"]["area_m2"], "m2")]
        else:
            props = [("length_m", max(r["attrs"]["a_m"], r["attrs"]["b_m"]), "m"),
                     ("width_m", min(r["attrs"]["a_m"], r["attrs"]["b_m"]), "m")]
        for prop, v, unit in props:
            db.run("""insert into jstore.claim (entity_id, property, value_num, value_text, unit, file_id, method,
                                                source_id, confidence)
                      values (:e, :p, :v, :t, :u, :f, 'printed', :s, 0.8)""",
                   e=eid, p=prop, v=v, t=r["attrs"]["text"], u=unit, f=f["file_id"], s=src)
            n += 1
    return n


def interpret(db: DB, f: dict) -> dict:
    """Dimensions of one source file.  -> merge() counts (plus `skipped` when nothing was looked for)."""
    t0 = time.time()
    none = dict(new=0, matched=0, unlinked=0)
    if os.environ.get("JSTORE_DIMENSIONS", "on") == "off":
        return dict(none, skipped="JSTORE_DIMENSIONS is off")
    have = db.one("""select count(*) as n from jstore.entity where drawing_key = :d and kind = 'dimension'
                     and status = 'active'""", d=f["drawing_key"])["n"]
    if have:
        return dict(none, skipped=f"another file of the drawing already gave {have} dimensions")
    rooms = _rooms(db, f["drawing_key"])
    if f["format"] in ("dxf", "dwg"):
        recs, spec = cad_records(db, f, rooms)
    elif f["format"] in ("pdf", "png", "jpg"):
        if not os.environ.get("GEMINI_API_KEY"):
            return dict(none, skipped="GEMINI_API_KEY is not set")
        recs, spec = image_records(db, f, rooms, t0)
    else:
        return dict(none, skipped=f"no dimension reader for {f['format']}")
    if not recs:
        return dict(none, found=0)
    out = merge(db, f, recs)
    out["room_claims"] = room_claims(db, f, recs)
    return out
