"""Swiss Dwellings DXF solver: counts and areas from the CAD layers, plus evidence geometry.

The counting and area rules are a verbatim port of the measured probe
(.omc/research/stoa/probes/swiss.py, 18,606/18,792 on the full Swiss set). Every count is
`len(items)` of the evidence list built here, so the number and the boxes cannot diverge.

Public interface (used by aura/tools.py):
    load(dxf_path, drawing_id=None) -> SwissModel
    SwissModel.to_json() / SwissModel.from_json(d)      (build/drawings/<id>/model.json)
    answer(model, family, scope=None, overrides=None) -> ToolResult (dict)
    find_text(model, pattern) -> ToolResult
    room_list(model, scope=None) -> list[dict]
Families: window, door, washbasin, wc, bathtub, shower, room, apartment (counts) and area.
Scopes (contract): {kind: floor | unit(unit) | room(room_id) | label(label) | region(rect, label)}.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ezdxf
from shapely.geometry import Point, Polygon, box

# --- probe constants (verbatim) -------------------------------------------------------------
OUT = re.compile(r'balk|balc|logg|terr|terass|sitzpl', re.IGNORECASE)
NUM = re.compile(r'[A-Z]{0,2}\d+(\.\d+)?')
APP_PREFIX = re.compile(r'(App|Whg|Apt|Log)\.')
APP_LABEL = re.compile(r'(App|Whg|Apt|Log)\.?\s')

# --- render frame (tools/extract_plan.py) ---------------------------------------------------
SCALE = 40.0  # px per metre in the rebuilt render
MARGIN = 0.6  # metres around the LWPOLYLINE extents
PAD = {'window': 0.12, 'door': 0.08, 'fixture': 0.1}

FIXTURE_SUBJECTS = ('washbasin', 'wc', 'bathtub', 'shower')
BOX_SUBJECTS = ('window', 'door') + FIXTURE_SUBJECTS
PREFIX = {'window': 'win', 'door': 'door', 'washbasin': 'basin', 'wc': 'wc', 'bathtub': 'tub',
          'shower': 'shower', 'text': 'text'}
FAMILIES = BOX_SUBJECTS + ('room', 'apartment', 'area')
DOUBLE_DOOR_NOTE = 'Drawn twice at the same spot in the CAD file; the dataset counts both.'


@dataclass
class SwissModel:
    """Everything the answers need, in DXF metres. JSON-serialisable (model.json)."""
    id: str
    source: str
    title: str
    subtitle: str
    frame: dict  # dxf_min, dxf_max, margin_m, scale, px
    rooms: list[dict]  # room_id,num,name,area(printed|None),geo,unit_idx,unit,outdoor,polygon,label_xy
    units: list[dict]  # idx, polygon, letters
    applabel: dict[str, int | None]  # apartment letter -> unit index (probe semantics)
    items: dict[str, list[dict]]  # subject -> [{bbox:[x0,y0,x1,y1], pad}]
    texts: list[dict]  # text, x, y, height, layer
    walls: list[list] = field(default_factory=list)
    lines: dict = field(default_factory=dict)  # svg line geometry per layer (render only)
    _boxes: dict | None = field(default=None, repr=False, compare=False)

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop('_boxes', None)
        return d

    @classmethod
    def from_json(cls, d: dict) -> SwissModel:
        d = {k: v for k, v in d.items() if k != '_boxes'}
        m = cls(**d)
        m.applabel = {k: v for k, v in m.applabel.items()}
        return m

    # --- frame transforms (rounding chain of extract_plan.py + build_mock.py) ---
    @property
    def W(self) -> int:
        return self.frame['px'][0]

    @property
    def H(self) -> int:
        return self.frame['px'][1]

    def T(self, x: float, y: float) -> tuple[float, float]:
        """DXF metres -> rebuilt-render px (0.1 px), y down."""
        minx = self.frame['dxf_min'][0] - MARGIN
        maxy = self.frame['dxf_max'][1] + MARGIN
        return round((x - minx) * SCALE, 1), round((maxy - y) * SCALE, 1)

    def pct(self, px: float, py: float) -> tuple[float, float]:
        return round(px / self.W * 100, 2), round(py / self.H * 100, 2)

    def bb_px(self, bbox, pad: float) -> dict:
        x0, y0, x1, y1 = bbox
        a = self.T(x0 - pad, y1 + pad)
        b = self.T(x1 + pad, y0 - pad)
        return {'x': a[0], 'y': a[1], 'w': round(b[0] - a[0], 1), 'h': round(b[1] - a[1], 1)}

    def px_to_pct_box(self, b: dict) -> dict:
        x, y = self.pct(b['x'], b['y'])
        return {'x': x, 'y': y, 'w': round(b['w'] / self.W * 100, 2), 'h': round(b['h'] / self.H * 100, 2)}

    def unit_letter(self, idx: int | None) -> str | None:
        if idx is None:
            return None
        return next((k for k, v in sorted(self.applabel.items()) if v == idx), None)

    def room(self, room_id: str) -> dict | None:
        return next((r for r in self.rooms if r['room_id'] == room_id), None)

    # --- evidence boxes, computed once ---
    def boxes(self) -> dict[str, list[dict]]:
        if self._boxes is None:
            self._boxes = {s: _build_boxes(self, s) for s in BOX_SUBJECTS}
        return self._boxes


# ============================================================================================
# Loading
# ============================================================================================

def _pts(e) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in e.get_points('xy')]


def _largest(poly):
    if poly.geom_type == 'Polygon':
        return poly
    parts = [g for g in getattr(poly, 'geoms', []) if g.geom_type == 'Polygon']
    return max(parts, key=lambda g: g.area) if parts else None


def _ring(poly) -> list[list[float]] | None:
    p = _largest(poly) if poly is not None else None
    if p is None or p.is_empty:
        return None
    c = list(p.exterior.coords)
    if len(c) > 1 and c[0] == c[-1]:
        c = c[:-1]
    return [[round(x, 6), round(y, 6)] for x, y in c]


def _arc_svg(e) -> dict:
    s, t = e.start_point, e.end_point
    sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
    return {'kind': 'arc', 'p0': [s.x, s.y], 'p1': [t.x, t.y], 'r': e.dxf.radius, 'large': sweep > 180}


def load(dxf_path: str | Path, drawing_id: str | None = None) -> SwissModel:
    dxf_path = Path(dxf_path)
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    # --- texts (probe uses ROOM_TEXT only; find_text uses every TEXT) ---
    texts = [(e.dxf.text.strip(), e.dxf.insert[0], e.dxf.insert[1])
             for e in msp.query('TEXT[layer=="ROOM_TEXT"]')]
    all_texts = []
    for e in msp.query('TEXT MTEXT'):
        if e.dxftype() == 'TEXT':
            txt = e.dxf.text.strip()
            ap = e.dxf.get('align_point') if e.dxf.get('halign', 0) or e.dxf.get('valign', 0) else None
            x, y = (ap[0], ap[1]) if ap is not None else (e.dxf.insert[0], e.dxf.insert[1])
            h = float(e.dxf.get('height', 0.25))
            centred = ap is not None
        else:
            txt = e.plain_text().strip()
            x, y = e.dxf.insert[0], e.dxf.insert[1]
            h = float(e.dxf.get('char_height', 0.25))
            centred = False
        all_texts.append({'text': txt, 'x': float(x), 'y': float(y), 'height': h, 'layer': e.dxf.layer,
                              'centred': centred})

    room_polys = [Polygon(_pts(e)).buffer(0) for e in msp.query('LWPOLYLINE[layer=="ROOM_OUTLINE"]')
                  if len(e) >= 3]
    unit_polys = [Polygon(_pts(e)).buffer(0) for e in msp.query('LWPOLYLINE[layer=="UNIT_OUTLINE"]')
                  if len(e) >= 3]

    items: dict[str, list[dict]] = {s: [] for s in BOX_SUBJECTS}

    # --- doors: LWPOLYLINE with >= 4 points (probe rule) ---
    for e in msp.query('LWPOLYLINE[layer=="DOOR"]'):
        if len(e) >= 4:
            items['door'].append({'bbox': list(Polygon(_pts(e)).bounds), 'pad': PAD['door']})

    # --- windows: outer bboxes; polylines whose bbox sits inside another are parts (probe rule) ---
    wb = [box(*Polygon(_pts(e)).bounds) if len(list(e.get_points())) > 2 else None
          for e in msp.query('LWPOLYLINE[layer=="WINDOW"]')]
    wb = [b for b in wb if b is not None]
    outer = [b for i, b in enumerate(wb)
             if not any(j != i and o.buffer(1e-3).contains(b) and o.area > b.area
                        for j, o in enumerate(wb))]
    items['window'] = [{'bbox': list(b.bounds), 'pad': PAD['window']} for b in outer]

    # --- fixtures: closed 4-point rectangles classified by the marks inside them (probe rule) ---
    rects, inner = [], []
    for e in msp.query('*[layer=="FIXTURE"]'):
        if e.dxftype() == 'CIRCLE':
            inner.append(('dot' if e.dxf.radius < 0.05 else 'burner',
                          Point(e.dxf.center[0], e.dxf.center[1])))
            continue
        if e.dxftype() != 'LWPOLYLINE':
            continue
        p = _pts(e)
        if e.closed and len(p) == 4:
            rects.append(Polygon(p))
        else:
            kind = {48: 'ell', 69: 'tub'}.get(len(p), 'line')
            inner.append((kind, Polygon(p).centroid if len(p) > 2 else Point(*p[0])))
    for r in rects:
        ins = collections.Counter(k for k, pt in inner if r.buffer(0.01).contains(pt))
        if ins['tub']:
            kind = 'bathtub'
        elif ins['ell'] and ins['dot']:
            kind = 'washbasin'
        elif ins['ell']:
            kind = 'wc'
        elif ins['burner']:
            kind = 'kitchen'
        elif ins['dot']:
            kind = 'shower'
        else:
            kind = 'other'
        if kind in items:
            items[kind].append({'bbox': list(r.bounds), 'pad': PAD['fixture']})

    # --- rooms: number text, then name above / area below (probe rule, dict keyed by number) ---
    nums = [(t, x, y) for t, x, y in texts if NUM.fullmatch(t)]
    rinfo: dict[str, dict] = {}
    for t, x, y in nums:
        poly = next((r for r in room_polys if r.contains(Point(x, y))), None)
        cand = [(abs(y2 - y) + abs(x2 - x), t2, x2, y2) for t2, x2, y2 in texts
                if (poly is not None and poly.contains(Point(x2, y2)))
                or (abs(x2 - x) < 1.0 and abs(y2 - y) < 0.5)]
        name = None; area = None; bd = 9; ba = 9
        for dd, t2, x2, y2 in cand:
            if t2.endswith('m²') and y2 < y and dd < ba:
                area = float(t2.split()[0]); ba = dd
            elif (not NUM.fullmatch(t2) and not t2.endswith('m²') and not APP_PREFIX.match(t2)
                  and y2 > y and dd < bd):
                name = t2; bd = dd
        geo = poly.area if poly is not None else None
        unit = next((i for i, u in enumerate(unit_polys) if u.buffer(0.05).contains(Point(x, y))), None)
        rinfo[t] = {'name': name, 'area': area, 'geo': geo, 'unit_idx': unit, 'x': x, 'y': y,
                        'polygon': _ring(poly) if poly is not None else None}

    applabel: dict[str, int | None] = {}
    app_xy: dict[str, tuple] = {}
    for t, x, y in texts:
        if APP_LABEL.match(t) or t.startswith(('App.', 'Whg.')):
            u = next((i for i, uu in enumerate(unit_polys) if uu.buffer(0.05).contains(Point(x, y))), None)
            applabel[t.split()[-1]] = u
            app_xy[t.split()[-1]] = (x, y)
    # A label just outside every unit outline (2 drawings of 1,040): give it the nearest unit that has no
    # label, so its rooms carry the letter (the probe would sum 0 m² for that apartment). Counts unchanged.
    for k, v in list(applabel.items()):
        if v is None:
            free = [i for i in range(len(unit_polys)) if i not in applabel.values()]
            if free:
                applabel[k] = min(free, key=lambda i: unit_polys[i].distance(Point(*app_xy[k])))

    def letter(idx):
        return next((k for k, v in sorted(applabel.items()) if v == idx), None) if idx is not None else None

    rooms = []
    for t, v in rinfo.items():
        rooms.append({'room_id': t, 'num': t, 'name': v['name'], 'area': v['area'], 'geo': v['geo'],
                          'unit_idx': v['unit_idx'], 'unit': letter(v['unit_idx']),
                          'outdoor': bool(v['name'] and OUT.search(v['name'])),
                          'polygon': v['polygon'], 'label_xy': [v['x'], v['y']]})

    units = [{'idx': i, 'polygon': _ring(u), 'letters': sorted(k for k, v in applabel.items() if v == i)}
             for i, u in enumerate(unit_polys)]

    # --- frame: LWPOLYLINE extents (extract_plan.py) ---
    pts = [p for e in msp.query('LWPOLYLINE') for p in e.get_points('xy')]
    mn = [min(p[0] for p in pts), min(p[1] for p in pts)]
    mx = [max(p[0] for p in pts), max(p[1] for p in pts)]
    W = round((float(mx[0]) - float(mn[0]) + 2 * MARGIN) * SCALE)
    H = round((float(mx[1]) - float(mn[1]) + 2 * MARGIN) * SCALE)
    frame = {'dxf_min': [round(float(mn[0]), 6), round(float(mn[1]), 6)],
                 'dxf_max': [round(float(mx[0]), 6), round(float(mx[1]), 6)],
                 'margin_m': MARGIN, 'scale': SCALE, 'px': [W, H]}

    # --- render geometry ---
    walls = [_pts(e) for e in msp.query('LWPOLYLINE[layer=="WALL"]')]
    lines = {}
    for layer in ('DOOR', 'WINDOW', 'FIXTURE'):
        out = []
        for e in msp.query(f'*[layer=="{layer}"]'):
            t = e.dxftype()
            if t == 'LWPOLYLINE':
                out.append({'kind': 'poly', 'pts': _pts(e), 'closed': bool(e.closed)})
            elif t == 'ARC':
                out.append(_arc_svg(e))
            elif t == 'CIRCLE':
                out.append({'kind': 'circle', 'c': [e.dxf.center.x, e.dxf.center.y], 'r': e.dxf.radius})
        lines[layer] = out

    ann = [t['text'] for t in all_texts if t['layer'] == 'ANNOTATION']
    title = ann[0] if ann else dxf_path.stem
    site = next((re.search(r'Site (\d+)', a).group(1) for a in ann if re.search(r'Site (\d+)', a)), None)
    scale = next((re.search(r'1:(\d+)', a).group(1) for a in ann if re.search(r'1:(\d+)', a)), None)
    subtitle = ' · '.join(s for s in ((f'Site {site}' if site else None),
                                      (f'1:{scale}' if scale else None)) if s)
    if drawing_id is None:
        m = re.search(r'floor(\d+)', dxf_path.stem)
        drawing_id = f'swiss-{m.group(1)}' if m else dxf_path.stem

    return SwissModel(id=drawing_id, source=dxf_path.name, title=title, subtitle=subtitle, frame=frame,
                      rooms=rooms, units=units, applabel=applabel, items=items, texts=all_texts,
                      walls=walls, lines=lines)


# ============================================================================================
# Evidence geometry
# ============================================================================================

def _inside(pt, poly) -> bool:
    x, y = pt; c = False
    for i in range(len(poly)):
        x1, y1 = poly[i]; x2, y2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def _room_px(model: SwissModel) -> list[dict]:
    """Rooms usable for box attribution (build_mock.py: indoor rooms of an apartment)."""
    out = []
    for r in model.rooms:
        if r['polygon'] is None or r['outdoor']:
            continue
        out.append({'r': r, 'pts': [model.T(x, y) for x, y in r['polygon']]})
    with_unit = [o for o in out if o['r']['unit'] is not None]
    return with_unit or out


def _nearest_room(rooms_px, cx, cy):
    best = None
    for o in rooms_px:
        if _inside((cx, cy), o['pts']):
            return o['r']
        dmin = min(((px - cx) ** 2 + (py - cy) ** 2) ** .5 for px, py in o['pts'])
        pts = o['pts']
        for i in range(len(pts)):
            (x1, y1), (x2, y2) = pts[i], pts[i - 1]
            for t in (0.25, 0.5, 0.75):
                dmin = min(dmin, ((x1 + (x2 - x1) * t - cx) ** 2 + (y1 + (y2 - y1) * t - cy) ** 2) ** .5)
        if best is None or dmin < best[0]:
            best = (dmin, o['r'])
    return best[1] if best else None


def _build_boxes(model: SwissModel, subject: str) -> list[dict]:
    rooms_px = _room_px(model)
    px = [model.bb_px(it['bbox'], it['pad']) for it in model.items.get(subject, [])]
    # stage 1: build_mock.py order (round(cy_px/60), x); stage 2: build-drawing.mjs (top, left, prior)
    order = sorted(px, key=lambda b: (round((b['y'] + b['h'] / 2) / 60), b['x']))
    staged = []
    for i, b in enumerate(order):
        r = _nearest_room(rooms_px, b['x'] + b['w'] / 2, b['y'] + b['h'] / 2)
        pb = model.px_to_pct_box(b)
        staged.append((pb, r, i))
    staged.sort(key=lambda s: (s[0]['y'], s[0]['x'], s[2]))
    out = []
    for k, (pb, r, _) in enumerate(staged):
        bx = dict(id=f'{PREFIX[subject]}-{k + 1:02d}', **pb, subject=subject)
        if r is not None:
            bx['room_id'] = r['room_id']
            if r['unit']:
                bx['unit'] = r['unit']
        out.append(bx)
    for bx in out:
        twins = [o for o in out if (o['x'], o['y'], o['w'], o['h']) == (bx['x'], bx['y'], bx['w'], bx['h'])]
        if len(twins) > 1 and subject == 'door':
            bx['note'] = DOUBLE_DOOR_NOTE
    return out


def room_poly_pct(model: SwissModel, r: dict) -> list[list[float]]:
    """Room polygon as [Pct, Pct][] without repeated points (closed implicitly)."""
    if r['polygon'] is None:
        x, y = r['label_xy']  # no outline in the DXF: a 1 m marker square around the label
        ring = [(x - .5, y + .5), (x + .5, y + .5), (x + .5, y - .5), (x - .5, y - .5)]
    else:
        ring = r['polygon']
    pts = []
    for x, y in ring:
        p = list(model.pct(*model.T(x, y)))
        if not pts or pts[-1] != p:
            pts.append(p)
    while len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def label_at(model: SwissModel, r: dict) -> list[float]:
    return list(model.pct(*model.T(*r['label_xy'])))


def _simplify_ring(ring):
    pts = []
    for p in ring:
        if not pts or abs(pts[-1][0] - p[0]) > 1e-6 or abs(pts[-1][1] - p[1]) > 1e-6:
            pts.append(p)
    while len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) < 1e-6:
                pts.pop(i); changed = True
                break
    return pts


def room_rect(model: SwissModel, r: dict, tol: float = 0.02) -> dict | None:
    """{x,y,w,h (Pct), w_m, h_m} for an indoor apartment room whose outline is an axis-aligned rectangle
    within `tol` metres (some outlines are skewed by ~1 cm), else None. The rect is the bbox."""
    if r['polygon'] is None or r['outdoor'] or not r['unit']:  # what-if is for apartment rooms only
        return None
    p = _simplify_ring(r['polygon'])
    if len(p) != 4:
        return None
    for i in range(4):
        a, b = p[i - 1], p[i]
        if abs(a[0] - b[0]) > tol and abs(a[1] - b[1]) > tol:
            return None
    xs = [q[0] for q in p]; ys = [q[1] for q in p]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    b = model.px_to_pct_box(model.bb_px([x0, y0, x1, y1], 0.0))
    return dict(**b, w_m=round(x1 - x0, 3), h_m=round(y1 - y0, 3))


def text_boxes(model: SwissModel, texts: list[dict]) -> list[dict]:
    """Approximate boxes for printed texts. Texts outside the rebuilt frame (the sheet's title block)
    are left out, so every box lies within 0-100. `note` carries the text."""
    out = []
    for t in texts:
        h = t['height']; w = max(len(t['text']), 1) * h * 0.6
        x0 = t['x'] - w / 2 if t['centred'] else t['x']
        y0 = t['y'] - h / 2 if t['centred'] else t['y']
        b = model.px_to_pct_box(model.bb_px([x0, y0, x0 + w, y0 + h], 0.05))
        if b['x'] < 0 or b['y'] < 0 or b['x'] + b['w'] > 100 or b['y'] + b['h'] > 100:
            continue
        out.append(dict(id=f'text-{len(out) + 1:02d}', **b, subject='label', note=t['text']))
    return out


# ============================================================================================
# Answers
# ============================================================================================

def _room_area(r: dict, ov: dict) -> tuple[float | None, bool]:
    """Probe `ar()`: printed area, else the outline's geometric area. Overrides win (what-if)."""
    if r['room_id'] in ov:
        o = ov[r['room_id']]
        return round(o['w_m'] * o['h_m'], 2), True
    if r['area'] is not None:
        return r['area'], False
    return r['geo'], False


def _in_rect(pt, rect) -> bool:
    return rect['x'] <= pt[0] <= rect['x'] + rect['w'] and rect['y'] <= pt[1] <= rect['y'] + rect['h']


def _scope_rooms(model: SwissModel, scope: dict) -> list[dict] | None:
    """Rooms in the scope, or None when the scope names something that does not exist."""
    k = scope.get('kind', 'floor')
    if k == 'floor':
        return list(model.rooms)
    if k == 'unit':
        u = model.applabel.get(scope.get('unit'))
        if u is None:
            return None
        return [r for r in model.rooms if r['unit_idx'] == u]
    if k == 'room':
        r = model.room(scope.get('room_id'))
        return [r] if r else None
    if k == 'label':
        return [r for r in model.rooms if r['name'] == scope.get('label')]
    if k == 'region':
        return [r for r in model.rooms if _in_rect(label_at(model, r), scope['rect'])]
    raise ValueError(f'unknown scope kind {k!r}')


def _scope_boxes(model: SwissModel, subject: str, scope: dict) -> list[dict] | None:
    boxes = model.boxes()[subject]
    k = scope.get('kind', 'floor')
    if k == 'floor':
        return list(boxes)
    if k == 'region':
        rect = scope['rect']
        return [b for b in boxes if _in_rect((b['x'] + b['w'] / 2, b['y'] + b['h'] / 2), rect)]
    rooms = _scope_rooms(model, scope)
    if rooms is None:
        return None
    ids = {r['room_id'] for r in rooms}
    return [b for b in boxes if b.get('room_id') in ids]


def _result_id(model, family, scope, overrides) -> str:
    key = json.dumps([model.id, family, scope, overrides or []], sort_keys=True, ensure_ascii=False)
    return 'r-' + hashlib.sha1(key.encode()).hexdigest()[:12]


def _scope_text(model: SwissModel, scope: dict) -> str:
    k = scope.get('kind', 'floor')
    return {'floor': 'on this floor', 'unit': f"in apartment {scope.get('unit')}",
            'room': f"in room {scope.get('room_id')}", 'label': f"in rooms labelled '{scope.get('label')}'",
            'region': f"inside {scope.get('label', 'the marked region')}"}[k]


def _abstain(rid, family, scope, reason, summary):
    return {'result_id': rid, 'family': family, 'scope': scope,
                'answer': {'kind': 'abstain', 'reason': reason}, 'value': None, 'numbers': [],
                'evidence': {'boxes': [], 'rooms': []}, 'summary': summary}


def answer(model: SwissModel, family: str, scope: dict | None = None,
           overrides: list[dict] | None = None) -> dict:
    """ToolResult for one question family over one scope.

    Returns {result_id, family, scope, answer, value, numbers, evidence{boxes,rooms}, summary}.
    `answer` is a contract Answer (count/area/abstain); `evidence` follows contract AskResponse.evidence,
    so the UI's recomputation from evidence equals `value`.
    """
    scope = scope or {'kind': 'floor'}
    ov = {o['room_id']: o for o in (overrides or [])}
    rid = _result_id(model, family, scope, overrides)
    where = _scope_text(model, scope)

    if family in BOX_SUBJECTS:
        boxes = _scope_boxes(model, family, scope)
        if boxes is None:
            return _abstain(rid, family, scope, 'unclear_question', f'No such {scope.get("kind")} on this drawing.')
        n = len(boxes)
        doubles = sum(1 for b in boxes if b.get('note'))
        extra = f' ({doubles} are double-door leaves drawn at the same spot, each counted)' if doubles else ''
        return {'result_id': rid, 'family': family, 'scope': scope,
                    'answer': {'kind': 'count', 'value': n, 'subject': family}, 'value': n,
                    'numbers': [n] + ([doubles] if doubles else []),
                    'evidence': {'boxes': boxes, 'rooms': []},
                    'summary': f'{n} {family}{"s" if n != 1 else ""} {where} on the DXF {family} layer{extra}.'}

    if family == 'apartment':
        if scope.get('kind', 'floor') != 'floor':
            rooms = _scope_rooms(model, scope) or []
            letters = sorted({r['unit'] for r in rooms if r['unit']})
        else:
            letters = sorted(model.applabel)
            rooms = [r for r in model.rooms if r['unit'] in letters]
        n = len(letters) if (letters or scope.get('kind', 'floor') != 'floor') else len(model.units)
        ev = [{'room_id': r['room_id'], 'included': True, 'area_m2': _room_area(r, ov)[0] or 0} for r in rooms]
        return {'result_id': rid, 'family': family, 'scope': scope,
                    'answer': {'kind': 'count', 'value': n, 'subject': 'apartment'}, 'value': n, 'numbers': [n],
                    'evidence': {'boxes': [], 'rooms': ev},
                    'summary': f'{n} apartment{"s" if n != 1 else ""} {where}'
                            + (f': {", ".join(letters)}.' if letters else ' (unit outlines, no apartment labels).')}

    rooms = _scope_rooms(model, scope)
    if rooms is None:
        return _abstain(rid, family, scope, 'unclear_question', f'No such {scope.get("kind")} on this drawing.')

    if family == 'room':
        n = len(rooms)
        ev = [{'room_id': r['room_id'], 'included': True, 'area_m2': _room_area(r, ov)[0] or 0} for r in rooms]
        return {'result_id': rid, 'family': family, 'scope': scope,
                    'answer': {'kind': 'count', 'value': n, 'subject': 'room'}, 'value': n, 'numbers': [n],
                    'evidence': {'boxes': [], 'rooms': ev},
                    'summary': f'{n} numbered room{"s" if n != 1 else ""} {where}.'}

    if family == 'area':
        k = scope.get('kind', 'floor')
        # Probe: room and label areas include outdoor rooms; floor, unit and region exclude them.
        drop_outdoor = k in ('floor', 'unit', 'region')
        ev, parts = [], []
        for r in rooms:
            a, edited = _room_area(r, ov)
            if a is None:
                if k == 'room':
                    return _abstain(rid, family, scope, 'unclear_question',
                                    f"Room {r['room_id']} has no printed area and no outline.")
                a = 0
            inc = not (drop_outdoor and r['outdoor'])
            row = {'room_id': r['room_id'], 'included': inc, 'area_m2': round(a, 2)}
            if edited:
                row['edited'] = True
            ev.append(row)
            if inc:
                parts.append(round(a, 2))
        value = round(sum(parts), 2)  # = what the UI recomputes from the rounded rows
        n_out = sum(1 for e in ev if not e['included'])
        return {'result_id': rid, 'family': family, 'scope': scope,
                    'answer': {'kind': 'area', 'value': value, 'unit': 'm2'}, 'value': value,
                    'numbers': [value, len(parts)] + ([n_out] if n_out else []) + parts,
                    'evidence': {'boxes': [], 'rooms': ev},
                    'summary': f'Sum of {len(parts)} net room area{"s" if len(parts) != 1 else ""} {where} = '
                            f'{value} m²' + (f'; {n_out} outdoor left out.' if n_out else '.')}

    raise ValueError(f'unknown family {family!r}; expected one of {FAMILIES}')


def find_text(model: SwissModel, pattern: str) -> dict:
    """Printed text matching `pattern` (regex, case-insensitive) with boxes (subject 'label')."""
    rx = re.compile(pattern, re.IGNORECASE)
    boxes = [b for b in text_boxes(model, model.texts) if rx.search(b['note'])]
    hits = [{'text': b['note']} for b in boxes]
    rid = _result_id(model, 'find_text', {'pattern': pattern}, None)
    return {'result_id': rid, 'family': 'find_text', 'scope': {'kind': 'floor'},
                'answer': {'kind': 'text', 'value': hits[0]['text'], 'subject': 'label'} if hits else
                {'kind': 'abstain', 'reason': 'not_on_drawing'},
                'value': hits[0]['text'] if hits else None, 'numbers': [],
                'texts': [h['text'] for h in hits], 'evidence': {'boxes': boxes, 'rooms': []},
                'summary': f'{len(hits)} printed text{"s" if len(hits) != 1 else ""} match /{pattern}/'
                        + (': ' + '; '.join(h['text'] for h in hits[:5]) if hits else '.')}


def room_list(model: SwissModel, scope: dict | None = None) -> list[dict]:
    rooms = _scope_rooms(model, scope or {'kind': 'floor'}) or []
    return [{'room_id': r['room_id'], 'name': r['name'], 'unit': r['unit'], 'area_m2': _room_area(r, {})[0],
                 'outdoor': r['outdoor']} for r in rooms]


def capabilities(model: SwissModel) -> list[str]:
    """Subjects this drawing can answer from geometry (layers present)."""
    caps = list(BOX_SUBJECTS)
    if model.rooms:
        caps.append('room')
    if model.applabel or model.units:
        caps.append('apartment')
    return caps
