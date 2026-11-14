"""Layer 1 for SVG plans: a vector page that reuses pdf_path / pdf_text.  No interpretation.

Every drawn subpath becomes a pdf_path row (flattened geometry, stroke / fill as '#rrggbb', line width, dash,
closed) and every text run a pdf_text row.  Coordinates are SVG user units, shifted so the viewBox starts at
(0, 0): origin top-left, y down; transforms of groups are applied.  `attrs` keeps what identifies the element:
its id, class and every data-* attribute, its own or inherited from the enclosing groups (model-rendered plans
carry the model element id there, e.g. data-guid), plus the element tag.

Text boxes are estimates (no font metrics): the anchor is exact, the extent is 0.55 x font size per character.
page.meta.unit_m is the paper size of one user unit in metres when the SVG states a physical size.

The page image is drawn by a small rasteriser in this module (PIL): fills, strokes and text.  Gradients,
patterns, clipping, masks and embedded images are not drawn; meta.render says so.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from ..db import DB, arr, js
from ..frames import trusted_scale
from .pdf import PATH_COLUMNS, TEXT_COLUMNS, point_wkb, wkb

RENDER_LONG_SIDE = 3000
MAX_PATHS = 300_000
STEPS = 10                                             # segments per curve
UNIT_M = {"mm": 0.001, "cm": 0.01, "in": 0.0254, "pt": 0.0254 / 72, "pc": 0.0254 / 6, "m": 1.0}
SKIP = {"defs", "symbol", "clipPath", "mask", "marker", "pattern", "linearGradient", "radialGradient", "metadata",
        "style", "title", "desc", "script", "filter", "namedview"}
INHERITED = ("fill", "stroke", "stroke-width", "stroke-dasharray", "fill-rule", "font-size", "font-family",
             "text-anchor", "font-weight", "fill-opacity", "stroke-opacity")
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
I = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)                     # (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f


def tag(e) -> str:
    return e.tag.split("}")[-1] if isinstance(e.tag, str) else ""


def length(s, default=None) -> tuple[float | None, str]:
    """'200mm' -> (200.0, 'mm')."""
    m = re.fullmatch(r"\s*([-+]?[\d.]+(?:e[-+]?\d+)?)\s*([a-z%]*)\s*", str(s or ""), re.I)
    return (float(m[1]), m[2].lower()) if m else (default, "")


def num(s, default=0.0) -> float:
    v, unit = length(s)
    return default if v is None or unit == "%" else v


def mul(m, n):
    """Transform n, then m."""
    a, b, c, d, e, f = m
    g, h, i, j, k, l = n
    return (a * g + c * h, b * g + d * h, a * i + c * j, b * i + d * j, a * k + c * l + e, b * k + d * l + f)


def parse_transform(s: str):
    m = I
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            c, s_ = math.cos(math.radians(v[0])), math.sin(math.radians(v[0]))
            t = (c, s_, -s_, c, 0, 0)
            if len(v) == 3:
                t = mul(mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = mul(m, t)
    return m


def colour(v) -> tuple[str | None, str | None]:
    """A paint value -> ('#rrggbb' or None, the raw value when it is not a plain colour)."""
    v = (v or "").strip()
    if not v or v == "none" or v == "transparent":
        return None, None
    if m := re.fullmatch(r"#([0-9a-f]{3})", v, re.I):
        return "#" + "".join(ch * 2 for ch in m[1]).lower(), None
    if re.fullmatch(r"#[0-9a-f]{6}", v, re.I):
        return v.lower(), None
    if m := re.fullmatch(r"rgba?\(([^)]*)\)", v, re.I):
        p = [x.strip() for x in m[1].replace("/", ",").split(",")]
        try:
            rgb = [round(float(x[:-1]) * 2.55) if x.endswith("%") else round(float(x)) for x in p[:3]]
            return "#" + "".join(f"{min(255, max(0, c)):02x}" for c in rgb), None
        except ValueError:
            return None, v
    try:
        from matplotlib.colors import to_hex
        return to_hex(v.lower()), None
    except ValueError:
        return None, v[:80]                           # url(#gradient), currentColor ...


def css_rules(root) -> list[tuple[str, dict]]:
    """Simple selectors of embedded style sheets: .class, tag, #id, tag.class."""
    rules = []
    for st in root.iter():
        if tag(st) != "style" or not st.text:
            continue
        for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", st.text, flags=re.S)):
            props = dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in body.split(";") if ":" in d))
            for s in sel.split(","):
                rules.append((s.strip(), props))
    return rules


def style_of(e, rules) -> dict:
    """Own presentation properties of an element: style sheet, then attributes, then the style attribute."""
    out: dict = {}
    t, classes, eid = tag(e), (e.get("class") or "").split(), e.get("id")
    for sel, props in rules:
        m = re.fullmatch(r"(\w+)?(?:\.([\w-]+))?(?:#([\w-]+))?", sel)
        if m and (m[1] or m[2] or m[3]) and (not m[1] or m[1] == t) and (not m[2] or m[2] in classes) \
                and (not m[3] or m[3] == eid):
            out.update(props)
    for k in INHERITED + ("opacity", "display", "visibility"):
        if e.get(k) is not None:
            out[k] = e.get(k)
    for d in (e.get("style") or "").split(";"):
        if ":" in d:
            k, v = d.split(":", 1)
            out[k.strip()] = v.strip()
    return out


# ------------------------------------------------------------------ geometry
def _arc(p0, rx, ry, phi, large, sweep, p1) -> list[tuple[float, float]]:
    """Points along an SVG elliptical arc from p0 to p1 (end point included)."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry, ph = abs(rx), abs(ry), math.radians(phi)
    c, s = math.cos(ph), math.sin(ph)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    k = math.sqrt(max(0.0, (rx * rx * ry * ry - den) / den)) if den else 0.0
    if large == sweep:
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2, s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2
    a0 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    a1 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    da = a1 - a0
    if sweep and da < 0:
        da += math.tau
    elif not sweep and da > 0:
        da -= math.tau
    n = max(4, int(abs(da) / (math.pi / 16)))
    return [(cx + rx * math.cos(a0 + da * i / n) * c - ry * math.sin(a0 + da * i / n) * s,
             cy + rx * math.cos(a0 + da * i / n) * s + ry * math.sin(a0 + da * i / n) * c) for i in range(1, n)] + [p1]


def _bez(p0, p1, p2, p3):
    out = []
    for i in range(1, STEPS + 1):
        t = i / STEPS
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def path_d(d: str) -> list[tuple[list, bool, bool]]:
    """Path data -> [(points, closed, curved)] per subpath."""
    toks = [(m[1], m[2]) for m in _TOKEN.finditer(d or "")]
    subs, pts, curved = [], [], False
    cur = start = (0.0, 0.0)
    last_c = last_q = None
    i, cmd = 0, None

    def take(n):
        nonlocal i
        vals = []
        while len(vals) < n and i < len(toks) and toks[i][1]:
            vals.append(float(toks[i][1]))
            i += 1
        return vals if len(vals) == n else None

    def flush(closed):
        nonlocal pts, curved
        if len(pts) > 1 or (pts and closed):
            subs.append((pts, closed, curved))
        pts, curved = [], False

    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
            if cmd in "Zz":
                flush(True)
                cur = start
                pts = []
                last_c = last_q = None
                continue
        elif cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}.get(c)
        v = take(n) if n else None
        if v is None:
            break
        if not pts and c != "M":
            pts = [cur]
        if c == "M":
            flush(False)
            cur = start = (v[0] + ox, v[1] + oy)
            pts = [cur]
            cmd = "l" if rel else "L"
        elif c == "L":
            cur = (v[0] + ox, v[1] + oy)
            pts.append(cur)
        elif c == "H":
            cur = (v[0] + ox, cur[1])
            pts.append(cur)
        elif c == "V":
            cur = (cur[0], v[0] + oy)
            pts.append(cur)
        elif c in "CS":
            if c == "C":
                p1, p2, p3 = (v[0] + ox, v[1] + oy), (v[2] + ox, v[3] + oy), (v[4] + ox, v[5] + oy)
            else:
                p1 = (2 * cur[0] - last_c[0], 2 * cur[1] - last_c[1]) if last_c else cur
                p2, p3 = (v[0] + ox, v[1] + oy), (v[2] + ox, v[3] + oy)
            pts += _bez(cur, p1, p2, p3)
            cur, last_c, curved = p3, p2, True
        elif c in "QT":
            if c == "Q":
                q, p3 = (v[0] + ox, v[1] + oy), (v[2] + ox, v[3] + oy)
            else:
                q = (2 * cur[0] - last_q[0], 2 * cur[1] - last_q[1]) if last_q else cur
                p3 = (v[0] + ox, v[1] + oy)
            p1 = (cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1]))
            p2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
            pts += _bez(cur, p1, p2, p3)
            cur, last_q, curved = p3, q, True
        elif c == "A":
            p1 = (v[5] + ox, v[6] + oy)
            pts += _arc(cur, v[0], v[1], v[2], bool(v[3]), bool(v[4]), p1)
            cur, curved = p1, True
        if c not in "CS":
            last_c = None
        if c not in "QT":
            last_q = None
    flush(False)
    return subs


def _ellipse(cx, cy, rx, ry):
    return [(cx + rx * math.cos(math.tau * i / 48), cy + ry * math.sin(math.tau * i / 48)) for i in range(48)]


def shape(e) -> tuple[str, list[tuple[list, bool, bool]]]:
    """An element -> (kind, subpaths in its own coordinates)."""
    t = tag(e)
    if t == "path":
        subs = path_d(e.get("d"))
        simple = len(subs) == 1 and len(subs[0][0]) == 2 and not subs[0][2]
        return ("line" if simple else "curve"), subs
    if t == "rect":
        x, y, w, h = num(e.get("x")), num(e.get("y")), num(e.get("width")), num(e.get("height"))
        return "rect", ([([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, False)] if w > 0 and h > 0 else [])
    if t == "line":
        return "line", [([(num(e.get("x1")), num(e.get("y1"))), (num(e.get("x2")), num(e.get("y2")))], False, False)]
    if t in ("polyline", "polygon"):
        v = [float(x) for x in _NUM.findall(e.get("points") or "")]
        pts = list(zip(v[0::2], v[1::2]))
        return "curve", ([(pts, t == "polygon", False)] if len(pts) > 1 else [])
    if t == "circle":
        r = num(e.get("r"))
        return "curve", ([(_ellipse(num(e.get("cx")), num(e.get("cy")), r, r), True, True)] if r > 0 else [])
    if t == "ellipse":
        rx, ry = num(e.get("rx")), num(e.get("ry"))
        return "curve", ([(_ellipse(num(e.get("cx")), num(e.get("cy")), rx, ry), True, True)] if rx > 0 and ry > 0 else [])
    return "", []


def tf(m, pts) -> np.ndarray:
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    return np.stack([m[0] * p[:, 0] + m[2] * p[:, 1] + m[4], m[1] * p[:, 0] + m[3] * p[:, 1] + m[5]], axis=1)


# ------------------------------------------------------------------ reading
def read(path: Path) -> dict:
    """Parse an SVG.  -> dict(width, height, unit_m, paths=[...], texts=[...], notes)."""
    root = ET.parse(str(path)).getroot()
    rules = css_rules(root)
    ids = {e.get("id"): e for e in root.iter() if e.get("id")}
    w, wu = length(root.get("width"))
    h, hu = length(root.get("height"))
    vb = [float(x) for x in _NUM.findall(root.get("viewBox") or "")]
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        if not w or not h or wu == "%" or hu == "%":
            raise ValueError("SVG has neither a viewBox nor a size")
        vb = [0.0, 0.0, w, h]
    unit_m = UNIT_M[wu] * w / vb[2] if w and wu in UNIT_M else None
    out = dict(width=vb[2], height=vb[3], unit_m=unit_m, paths=[], texts=[], skipped={},
               size=[root.get("width"), root.get("height")], viewbox=vb)
    base = (1, 0, 0, 1, -vb[0], -vb[1])

    def text_runs(e, m, st, x, y):
        """Text content of <text> / <tspan> as runs that start at their own x / y."""
        s = dict(st)
        s.update(style_of(e, rules))
        if e.get("x") is not None:
            x = num(_NUM.findall(e.get("x"))[0] if _NUM.findall(e.get("x")) else 0)
        if e.get("y") is not None:
            y = num(_NUM.findall(e.get("y"))[0] if _NUM.findall(e.get("y")) else 0)
        x += num(e.get("dx"))
        y += num(e.get("dy"))
        runs = []
        if e.text and e.text.strip():
            runs.append((re.sub(r"\s+", " ", e.text.strip()), x, y, s))
        for ch in e:
            if tag(ch) in ("tspan", "textPath", "a"):
                sub = text_runs(ch, m, s, x, y)
                if sub and runs and ch.get("x") is None and ch.get("y") is None and ch.get("dy") is None:
                    runs[-1] = (runs[-1][0] + " " + sub[0][0], *runs[-1][1:])   # inline span: same line
                    sub = sub[1:]
                runs += sub
            if ch.tail and ch.tail.strip():
                tail = re.sub(r"\s+", " ", ch.tail.strip())
                if runs:
                    runs[-1] = (runs[-1][0] + " " + tail, *runs[-1][1:])
                else:
                    runs.append((tail, x, y, s))
        return runs

    def walk(e, m, st, ident, depth=0):
        t = tag(e)
        if t in SKIP or depth > 60:
            return
        s = {k: v for k, v in st.items() if k in INHERITED}
        own = style_of(e, rules)
        s.update(own)
        if own.get("display") == "none" or own.get("visibility") == "hidden":
            return
        if e.get("transform"):
            m = mul(m, parse_transform(e.get("transform")))
        ident = dict(ident)
        for k, v in e.attrib.items():
            k = k.split("}")[-1]
            if k in ("id", "class") or k.startswith("data-"):
                ident[k] = v
        if t in ("g", "svg", "a", "switch"):
            if t == "svg" and depth > 0:
                m = mul(m, (1, 0, 0, 1, num(e.get("x")), num(e.get("y"))))
            for ch in e:
                walk(ch, m, s, ident, depth + 1)
            return
        if t == "use":
            ref = next((v for k, v in e.attrib.items() if k.split("}")[-1] == "href"), "")
            target = ids.get(ref.lstrip("#"))
            if target is not None and depth < 20:
                m2 = mul(m, (1, 0, 0, 1, num(e.get("x")), num(e.get("y"))))
                if tag(target) == "symbol":
                    for ch in target:
                        walk(ch, m2, s, ident, depth + 1)
                else:
                    walk(target, m2, s, ident, depth + 1)
            return
        if t == "text":
            for text, x, y, ts in text_runs(e, m, s, 0.0, 0.0):
                out["texts"].append(dict(text=text, x=x, y=y, m=m, style=ts))
            return
        kind, subs = shape(e)
        if not kind:
            if t:
                out["skipped"][t] = out["skipped"].get(t, 0) + 1
            return
        if subs:
            out["paths"].append(dict(kind=kind, subs=[(tf(m, p), c, cv) for p, c, cv in subs], style=s,
                                     ident=dict(ident, tag=t), scale=math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))))

    walk(root, base, {}, {})
    return out


# ------------------------------------------------------------------ rows and rendering
def _rows(file_id: str, doc: dict) -> tuple[list[tuple], list[tuple], list[dict]]:
    prows, trows, draw = [], [], []
    seq = 0
    for p in doc["paths"]:
        st = p["style"]
        fill, fraw = colour(st.get("fill", "black"))
        stroke, sraw = colour(st.get("stroke"))
        if num(st.get("fill-opacity"), 1.0) == 0:
            fill = None
        width = num(st.get("stroke-width"), 1.0) * p["scale"]
        dash = st.get("stroke-dasharray")
        dash = None if not dash or dash == "none" else " ".join(_NUM.findall(dash))
        a = dict(p["ident"])
        if fraw:
            a["fill_raw"] = fraw
        if sraw:
            a["stroke_raw"] = sraw
        if st.get("fill-rule") == "evenodd":
            a["evenodd"] = True
        for k, (pts, closed, curved) in enumerate(p["subs"]):
            if seq >= MAX_PATHS:
                break
            g = wkb(pts, closed)
            if g is None:
                continue
            aa = dict(a, subpath=k) if len(p["subs"]) > 1 else a
            if curved:
                aa = dict(aa, bezier=True)
            prows.append((file_id, 0, seq, p["kind"], stroke, fill, width if stroke else None, dash, closed, len(pts),
                          g, js(aa)))
            seq += 1
        draw.append(dict(subs=[s[0] for s in p["subs"]], closed=[s[1] for s in p["subs"]], fill=fill, stroke=stroke,
                         width=width))
    for i, t in enumerate(doc["texts"]):
        st, m = t["style"], t["m"]
        k = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
        size = num(st.get("font-size"), 16.0) * k
        est = 0.55 * size * len(t["text"])
        anchor = st.get("text-anchor", "start")
        ax, ay = (float(v) for v in tf(m, [(t["x"], t["y"])])[0])
        rot = int(round(-math.degrees(math.atan2(m[1], m[0])))) % 360
        dx = {"middle": -est / 2, "end": -est}.get(anchor, 0.0)
        ux, uy = math.cos(math.radians(-rot)), math.sin(math.radians(-rot))      # direction the text runs (y down)
        corners = [(ax + dx * ux + a * ux - b * uy, ay + dx * uy + a * uy + b * ux)
                   for a in (0, est) for b in (-0.8 * size, 0.2 * size)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        fill, _ = colour(st.get("fill", "black"))
        trows.append((file_id, 0, i, t["text"], min(xs), min(ys), max(xs), max(ys),
                      (st.get("font-family") or "").strip("'\" ") or None, size,
                      point_wkb((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2), i, rot))
        draw.append(dict(text=t["text"], at=(ax + dx * ux, ay + dx * uy), size=size, rot=rot, fill=fill or "#000000"))
    return prows, trows, draw


def _font(px: int):
    """DejaVu Sans as shipped with matplotlib (covers accented letters); PIL's built-in font otherwise."""
    try:
        import matplotlib
        return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"), px)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def rasterise(draw: list[dict], width: float, height: float, out: Path) -> tuple[int, int, float]:
    """Draw fills, strokes and text in document order.  -> (width px, height px, pixels per unit)."""
    s = RENDER_LONG_SIDE / max(width, height)
    W, H = max(1, round(width * s)), max(1, round(height * s))
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for item in draw:
        if "text" in item:
            px = max(6, round(item["size"] * s))
            font = _font(px)
            x, y = item["at"][0] * s, item["at"][1] * s
            if item["rot"] == 0:
                d.text((x, y), item["text"], fill=item["fill"], font=font, anchor="ls")
            else:
                box = d.textbbox((0, 0), item["text"], font=font, anchor="ls")
                pad = max(abs(v) for v in box) + 2
                tile = Image.new("L", (2 * pad, 2 * pad), 0)
                ImageDraw.Draw(tile).text((pad, pad), item["text"], fill=255, font=font, anchor="ls")
                tile = tile.rotate(item["rot"], center=(pad, pad))
                im.paste(item["fill"], (round(x) - pad, round(y) - pad), tile)
            continue
        polys = [[(float(x) * s, float(y) * s) for x, y in pts] for pts in item["subs"]]
        if item["fill"]:
            fills = [p for p in polys if len(p) >= 3]
            if len(fills) == 1:
                d.polygon(fills[0], fill=item["fill"])
            elif fills:                               # several subpaths: holes by even-odd on a local mask
                xs = [x for p in fills for x, _ in p]
                ys = [y for p in fills for _, y in p]
                x0, y0 = max(0, math.floor(min(xs))), max(0, math.floor(min(ys)))
                x1, y1 = min(W, math.ceil(max(xs)) + 1), min(H, math.ceil(max(ys)) + 1)
                if x1 > x0 and y1 > y0:
                    mask = Image.new("1", (x1 - x0, y1 - y0), 0)
                    for p in fills:
                        one = Image.new("1", mask.size, 0)
                        ImageDraw.Draw(one).polygon([(x - x0, y - y0) for x, y in p], fill=1)
                        mask = ImageChops.logical_xor(mask, one)
                    im.paste(item["fill"], (x0, y0), mask)
        if item["stroke"]:
            lw = max(1, round(item["width"] * s))
            for p, closed in zip(polys, item["closed"]):
                if len(p) >= 2:
                    d.line(p + [p[0]] if closed else p, fill=item["stroke"], width=lw, joint="curve" if lw > 2 else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "PNG", compress_level=3)
    return W, H, s


def ingest(db: DB, file_id: str, path: Path) -> dict:
    """Parse one SVG into page / pdf_path / pdf_text and draw its page image.  -> meta."""
    from ..pipeline import store_dir
    doc = read(path)
    for table in ("pdf_path", "pdf_text", "page", "frame"):
        db.run(f"delete from jstore.{table} where file_id = :f", f=file_id)
    prows, trows, draw = _rows(file_id, doc)
    n_paths = db.copy("jstore.pdf_path", PATH_COLUMNS, prows)
    n_texts = db.copy("jstore.pdf_text", TEXT_COLUMNS, trows)
    from .raster import scale_texts
    scales = scale_texts(r[3] for r in trows)
    rel = Path("renders") / file_id / "p0.png"
    render_note = "jstore rasteriser: fills, strokes, text; no gradients, patterns, clipping or embedded images"
    try:
        iw, ih, s = rasterise(draw, doc["width"], doc["height"], store_dir() / rel)
        image, to_image = str(rel), arr([s, 0, 0, 0, s, 0])
    except Exception as e:  # noqa: BLE001 - the vectors are stored; only the picture is missing
        image, iw, ih, to_image, render_note = None, None, None, None, f"not rendered: {e!r}"[:200]
    pmeta = dict(scales=scales, paths=n_paths, texts=n_texts, viewbox=doc["viewbox"], size=doc["size"], render=render_note,
                 text_boxes="estimated from font size")
    if doc["unit_m"]:
        pmeta["unit_m"] = doc["unit_m"]
    if doc["skipped"]:
        pmeta["elements_not_read"] = doc["skipped"]
    db.run("""insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image, meta)
              values (:f, 0, :n, :w, :h, 'px', :img, :iw, :ih, cast(:t as double precision[]), cast(:m as jsonb))""",
           f=file_id, n=path.stem, w=doc["width"], h=doc["height"], img=image, iw=iw, ih=ih, t=to_image, m=js(pmeta))
    meta = dict(paths=n_paths, texts=n_texts, scales=scales, render=render_note, unit_m=doc["unit_m"],
                data_attributes=sorted({k for p in doc["paths"] for k in p["ident"] if k.startswith("data-")}))
    if doc["skipped"]:
        meta["elements_not_read"] = doc["skipped"]
    if trusted_scale(scales):
        meta["scale"] = trusted_scale(scales)["text"]
    return meta
