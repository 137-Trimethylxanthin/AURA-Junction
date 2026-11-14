"""IFC solver: counts, areas, lengths and volumes from an IFC model, plus a 2D storey plan for evidence.

The question rules are a port of the measured probe (.omc/research/stoa/probes/ifcm.py, 1,691/1,699 on the
full ifc_models set). `load()` needs IfcOpenShell (optional extra `ifc`) and extracts everything into plain
data, so `IfcModel.from_json()` + `answer()` run without IfcOpenShell (the served app reads model.json).

Public interface (mirrors solvers.swiss):
    load(ifc_path, storey=None, drawing_id=None, geometry=True) -> IfcModel
    IfcModel.to_json() / IfcModel.from_json(d)
    answer(model, family, scope=None) -> ToolResult dict  {result_id, family, scope, answer, value, numbers,
                                                            evidence{boxes, rooms, rows}, summary}
    find_room(model, text, storey=None) -> room dict | None   (the dataset's 'LongName (Name)' matching)
    capabilities(model) -> list[str]
Families: door, window, room, wall, column, beam, stair, sanitary (counts); area (IfcSpace net floor area);
wall_length, wall_side_area, wall_volume, slab_volume (per-storey totals).
Scopes: {kind:'floor'} = the drawing's storey (or {kind:'floor', storey:'Level 2'}); {kind:'model'} = whole model;
{kind:'room', room_id}; {kind:'label', label} (rooms whose LongName equals it); {kind:'region', rect, label}.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCALE_MAX_PX = 1600  # longest side of the rendered storey plan
SCALE_PX_PER_M = 40.0
MARGIN = 0.6

# probe CLS (order matters: the first regex that matches the question wins)
CLS = {'door': 'IfcDoor', 'window': 'IfcWindow', 'room': 'IfcSpace', 'wall': 'IfcWall', 'column': 'IfcColumn',
       'beam': 'IfcBeam', 'stair': 'IfcStair', 'sanitary': 'IfcSanitaryTerminal', 'light': 'IfcLightFixture'}
COUNT_FAMILIES = tuple(CLS)
TOTAL_FAMILIES = {'wall_length': ('IfcWall', 'Length'), 'wall_side_area': ('IfcWall', 'NetSideArea'),
                  'wall_volume': ('IfcWall', 'NetVolume'), 'slab_volume': ('IfcSlab', 'NetVolume')}
TOTAL_UNIT = {'wall_length': 'm', 'wall_side_area': 'm2', 'wall_volume': 'm3', 'slab_volume': 'm3'}
FAMILIES = COUNT_FAMILIES + ('area',) + tuple(TOTAL_FAMILIES)
BOX_SUBJECTS = ('door', 'window')  # contract Box subjects with 2D footprints
PREFIX = {'door': 'door', 'window': 'win'}
PLURAL = {'door': 'doors', 'window': 'windows', 'room': 'rooms (spaces)', 'wall': 'walls', 'column': 'columns',
          'beam': 'beams', 'stair': 'stairs', 'sanitary': 'sanitary fixtures',
          'light': 'light fixtures'}


@dataclass
class IfcModel:
    id: str
    source: str
    schema: str
    project: str
    storey: str | None  # the drawing's storey (default scope)
    storeys: list[dict]  # name, elevation
    unit_scale: float
    elements: dict[str, list[dict]]  # family -> [{guid, name, storey, qto?}] (counts and totals)
    rooms: list[dict]  # room_id(guid), num(Name), name(LongName), storey, area, area_source, polygon
    footprints: dict[str, list[dict]] = field(default_factory=dict)  # family -> [{guid, polygon}] on `storey`
    frame: dict | None = None  # min, max (metres), scale px/m, px

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> IfcModel:
        return cls(**d)

    def storey_or_default(self, scope: dict) -> str | None:
        return scope.get('storey') or self.storey

    def T(self, x: float, y: float) -> tuple[float, float]:
        f = self.frame
        return round((x - f['min'][0]) * f['scale'], 1), round((f['max'][1] - y) * f['scale'], 1)

    def pct(self, x: float, y: float) -> list[float]:
        px, py = self.T(x, y)
        return [round(px / self.frame['px'][0] * 100, 2), round(py / self.frame['px'][1] * 100, 2)]


# ============================================================================================
# Loading (needs IfcOpenShell)
# ============================================================================================

def _footprint(shape_geom):
    """Union of the shape's triangles projected on XY (world coordinates, metres)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    v, fc = shape_geom.verts, shape_geom.faces
    tris = []
    for i in range(0, len(fc), 3):
        a, b, c = fc[i], fc[i + 1], fc[i + 2]
        p = Polygon([(v[3 * a], v[3 * a + 1]), (v[3 * b], v[3 * b + 1]), (v[3 * c], v[3 * c + 1])])
        if p.area > 1e-9:
            tris.append(p.buffer(1e-6))
    if not tris:
        return None
    u = unary_union(tris).buffer(-1e-6)
    if u.is_empty:
        return None
    if u.geom_type != 'Polygon':
        parts = [g for g in getattr(u, 'geoms', []) if g.geom_type == 'Polygon']
        if not parts:
            return None
        u = max(parts, key=lambda g: g.area)
    return u.simplify(0.005)


def load(ifc_path: str | Path, storey: str | None = None, drawing_id: str | None = None,
         geometry: bool = True) -> IfcModel:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.shape as us
    import ifcopenshell.util.unit

    ifc_path = Path(ifc_path)
    f = ifcopenshell.open(str(ifc_path))

    @functools.lru_cache(None)
    def storey_of(e):
        s = ue.get_container(e) or ue.get_aggregate(e)
        while s is not None and not s.is_a('IfcBuildingStorey'):
            s = ue.get_aggregate(s)
        return s

    def q(e, names):
        ps = ue.get_psets(e, qtos_only=True)
        for qs in ps.values():
            for n in names:
                if n in qs:
                    return qs[n]
        return None

    gs = ifcopenshell.geom.settings()  # probe settings (local coordinates) for the area fallback

    def geo_area(e):
        try:
            sh = ifcopenshell.geom.create_shape(gs, e)
            a = us.get_footprint_area(sh.geometry)
            if not a:  # 0 for some spaces (duplex hallways); the projected outline still has an area
                fp = _footprint(sh.geometry)
                a = fp.area if fp is not None else a
            return a
        except Exception:  # noqa: BLE001 - the probe treats any geometry failure as "no area"
            return None

    def sname(e):
        s = storey_of(e)
        return s.Name if s is not None else None

    def nested_stair(e):
        a = ue.get_aggregate(e)
        return a is not None and a.is_a('IfcStair')

    elements: dict[str, list[dict]] = {}
    for fam, c in CLS.items():
        if c in ('IfcSanitaryTerminal', 'IfcLightFixture') and f.schema == 'IFC2X3':
            # IFC2X3 has no such entity: a flow terminal typed by the matching type (probe rule for sanitary;
            # the same rule for light fixtures is new, see eval/results/ifc.json)
            xs = [x for x in f.by_type('IfcFlowTerminal')
                  if ue.get_type(x) and ue.get_type(x).is_a(c + 'Type')]
        else:
            xs = list(f.by_type(c))
        if c == 'IfcStair':
            xs = [x for x in xs if not nested_stair(x)]
        elements[fam] = [{'guid': x.GlobalId, 'name': x.Name, 'storey': sname(x), 'id': x.id()} for x in xs]
    for fam, (c, qn) in TOTAL_FAMILIES.items():
        elements[fam] = [{'guid': x.GlobalId, 'name': x.Name, 'storey': sname(x), 'qto': q(x, [qn])}
                         for x in f.by_type(c)]

    storeys = sorted(({'name': s.Name, 'elevation': float(s.Elevation) if s.Elevation is not None else None}
                      for s in f.by_type('IfcBuildingStorey')),
                     key=lambda s: (s['elevation'] is None, s['elevation'] or 0))

    ws = ifcopenshell.geom.settings()
    ws.set('use-world-coords', True)

    def world_fp(e):
        try:
            return _footprint(ifcopenshell.geom.create_shape(ws, e).geometry)
        except Exception:  # noqa: BLE001 - elements without a body representation have no footprint
            return None

    def ring(p):
        c = list(p.exterior.coords)[:-1]
        return [[round(x, 4), round(y, 4)] for x, y in c]

    rooms = []
    for s in f.by_type('IfcSpace'):
        a = q(s, ['NetFloorArea'])
        src = 'qto'
        if a is None:
            a = geo_area(s); src = 'geometry'
        st = sname(s)
        poly = None
        if geometry and (storey is None or st == storey):
            fp = world_fp(s)
            poly = ring(fp) if fp is not None else None
        rooms.append({'room_id': s.GlobalId, 'num': s.Name or '', 'name': s.LongName or '', 'storey': st,
                      'area': a, 'area_source': src, 'polygon': poly})

    footprints: dict[str, list[dict]] = {}
    frame = None
    if geometry and storey is not None:
        by_id = {e.id(): e for c in ('IfcDoor', 'IfcWindow', 'IfcWall', 'IfcColumn', 'IfcStair')
                 for e in f.by_type(c)}
        for fam in ('door', 'window', 'wall', 'column', 'stair'):
            out = []
            for el in elements[fam]:
                if el['storey'] != storey:
                    continue
                fp = world_fp(by_id[el['id']]) if el['id'] in by_id else None
                out.append({'guid': el['guid'], 'polygon': ring(fp) if fp is not None else None})
            footprints[fam] = out
        pts = [p for r in rooms if r['storey'] == storey and r['polygon'] for p in r['polygon']]
        pts += [p for v in footprints.values() for g in v if g['polygon'] for p in g['polygon']]
        if pts:
            mn = [min(p[0] for p in pts) - MARGIN, min(p[1] for p in pts) - MARGIN]
            mx = [max(p[0] for p in pts) + MARGIN, max(p[1] for p in pts) + MARGIN]
            scale = min(SCALE_PX_PER_M, SCALE_MAX_PX / max(mx[0] - mn[0], mx[1] - mn[1]))
            frame = {'min': [round(mn[0], 4), round(mn[1], 4)], 'max': [round(mx[0], 4), round(mx[1], 4)],
                     'scale': round(scale, 4),
                     'px': [round((mx[0] - mn[0]) * scale), round((mx[1] - mn[1]) * scale)]}
    for el in [e for v in elements.values() for e in v]:
        el.pop('id', None)

    proj = f.by_type('IfcProject')
    project = (proj[0].LongName or proj[0].Name or ifc_path.stem) if proj else ifc_path.stem
    if drawing_id is None:
        slug = re.sub(r'[^a-z0-9]+', '-', (storey or 'model').lower()).strip('-')
        drawing_id = f'ifc-{ifc_path.stem}' + (f'--{slug}' if storey else '')
    return IfcModel(id=drawing_id, source=ifc_path.name, schema=f.schema, project=str(project), storey=storey,
                    storeys=storeys, unit_scale=float(ifcopenshell.util.unit.calculate_unit_scale(f)),
                    elements=elements, rooms=rooms, footprints=footprints, frame=frame)


# ============================================================================================
# Answers (plain data, no IfcOpenShell)
# ============================================================================================

def find_room(model: IfcModel, text: str, storey: str | None = None) -> dict | None:
    """The probe's room matching: 'LongName (Name)', Name or LongName, exactly one space on the storey."""
    st = storey or model.storey
    sp = [r for r in model.rooms if r['storey'] == st
          and (f"{r['name']} ({r['num']})" == text or r['num'] == text or r['name'] == text)]
    return sp[0] if len(sp) == 1 else None


def _rid(model, family, scope) -> str:
    key = json.dumps([model.id, family, scope], sort_keys=True, ensure_ascii=False)
    return 'r-' + hashlib.sha1(key.encode()).hexdigest()[:12]


def _abstain(rid, family, scope, reason, summary):
    return {'result_id': rid, 'family': family, 'scope': scope, 'answer': {'kind': 'abstain', 'reason': reason},
            'value': None, 'numbers': [], 'evidence': {'boxes': [], 'rooms': [], 'rows': []}, 'summary': summary}


def _bbox_pct(model: IfcModel, poly) -> dict:
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    x0, y0 = model.pct(min(xs), max(ys)); x1, y1 = model.pct(max(xs), min(ys))
    return {'x': x0, 'y': y0, 'w': round(x1 - x0, 2), 'h': round(y1 - y0, 2)}


def room_poly_pct(model: IfcModel, r: dict) -> list[list[float]] | None:
    if not r['polygon'] or model.frame is None:
        return None
    pts = []
    for x, y in r['polygon']:
        p = model.pct(x, y)
        if not pts or pts[-1] != p:
            pts.append(p)
    while len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts if len(pts) >= 3 else None


def label_at(model: IfcModel, r: dict) -> list[float] | None:
    if not r['polygon'] or model.frame is None:
        return None
    from shapely.geometry import Polygon
    p = Polygon(r['polygon']).representative_point()
    return model.pct(p.x, p.y)


def boxes(model: IfcModel, family: str) -> list[dict] | None:
    """Door/window boxes on the drawing's storey, reading order; None when any element has no footprint."""
    fps = model.footprints.get(family)
    if fps is None or model.frame is None or any(g['polygon'] is None for g in fps):
        return None
    bs = sorted((_bbox_pct(model, g['polygon']) | {'guid': g['guid']} for g in fps), key=lambda b: (b['y'], b['x']))
    return [{'id': f'{PREFIX[family]}-{k + 1:02d}', 'x': b['x'], 'y': b['y'], 'w': b['w'], 'h': b['h'],
             'subject': family, 'note': f"IFC {b['guid']}"} for k, b in enumerate(bs)]


def _rows(els):
    return [{'id': e['guid'], 'name': e['name']} for e in els]


def answer(model: IfcModel, family: str, scope: dict | None = None) -> dict:
    scope = scope or {'kind': 'floor'}
    rid = _rid(model, family, scope)
    k = scope.get('kind', 'floor')
    st = model.storey_or_default(scope)

    if family in COUNT_FAMILIES:
        els = model.elements[family]
        if k == 'model':
            where = f"in the model '{model.project}'"
        elif k == 'floor':
            if st is None or st not in {s['name'] for s in model.storeys}:
                return _abstain(rid, family, scope, 'unclear_question', f'No storey {st!r} in this model.')
            els = [e for e in els if e['storey'] == st]
            where = f"on '{st}'"
        else:
            return _abstain(rid, family, scope, 'not_supported_yet',
                            f'{PLURAL[family]} are counted per storey or for the whole model only.')
        n = len(els)
        ev = {'boxes': [], 'rooms': [], 'rows': []}
        how = 'listed by IFC GlobalId (no 2D boxes)'
        if family == 'room':
            ev['rooms'] = [{'room_id': r['room_id'], 'included': True, 'area_m2': round(r['area'] or 0, 2)}
                           for r in model.rooms if k == 'model' or r['storey'] == st]
            how = 'shown as room polygons where the storey plan has them'
        elif family in BOX_SUBJECTS and k == 'floor' and st == model.storey and (b := boxes(model, family)):
            ev['boxes'] = b
            how = 'boxed on the storey plan from their IFC footprints'
        else:
            ev['rows'] = _rows(els)
        return {'result_id': rid, 'family': family, 'scope': scope,
                'answer': {'kind': 'count', 'value': n, 'subject': family}, 'value': n, 'numbers': [n],
                'evidence': ev, 'summary': f'{n} {PLURAL[family]} {where} in the IFC model, {how}.'}

    if family == 'area':
        if k == 'room':
            rr = [r for r in model.rooms if r['room_id'] == scope.get('room_id')]
            if not rr and scope.get('room'):
                fr = find_room(model, scope['room'], st)
                rr = [fr] if fr else []
            if len(rr) != 1:
                return _abstain(rid, family, scope, 'unclear_question', 'No single room matches on this storey.')
        elif k == 'floor':
            rr = [r for r in model.rooms if r['storey'] == st]
        elif k == 'model':
            rr = list(model.rooms)
        elif k == 'label':
            rr = [r for r in model.rooms if r['storey'] == st and r['name'] == scope.get('label')]
        elif k == 'region':
            rect = scope['rect']
            rr = []
            for r in model.rooms:
                la = label_at(model, r) if r['storey'] == st else None
                if la and rect['x'] <= la[0] <= rect['x'] + rect['w'] and rect['y'] <= la[1] <= rect['y'] + rect['h']:
                    rr.append(r)
        else:
            return _abstain(rid, family, scope, 'not_supported_yet', f'Scope {k!r} is not used for IFC areas.')
        if k == 'room' and rr[0]['area'] is None:
            return _abstain(rid, family, scope, 'unclear_question', 'The room has no area quantity and no geometry.')
        parts = [round(r['area'] or 0, 2) for r in rr]
        value = round(sum(parts), 2)
        geo = sum(1 for r in rr if r['area_source'] == 'geometry')
        return {'result_id': rid, 'family': family, 'scope': scope,
                'answer': {'kind': 'area', 'value': value, 'unit': 'm2'}, 'value': value,
                'numbers': [value, len(parts)] + ([geo] if geo else []) + parts,
                'evidence': {'boxes': [], 'rows': [],
                             'rooms': [{'room_id': r['room_id'], 'included': True, 'area_m2': a}
                                       for r, a in zip(rr, parts, strict=True)]},
                'summary': f'Sum of {len(parts)} IfcSpace net floor area{"s" if len(parts) != 1 else ""} = {value} m²'
                           + (f'; {geo} from the space geometry (no NetFloorArea quantity).' if geo else '.')}

    if family in TOTAL_FAMILIES:
        if k != 'floor':
            return _abstain(rid, family, scope, 'not_supported_yet', 'Wall and slab totals are per storey.')
        els = [e for e in model.elements[family] if e['storey'] == st]
        tot = sum(e['qto'] or 0 for e in els)
        if family == 'wall_length':
            tot *= model.unit_scale  # probe: Qto Length is in model units (molio: mm)
        value = round(tot, 3)
        missing = sum(1 for e in els if e['qto'] is None)
        unit = TOTAL_UNIT[family]
        return {'result_id': rid, 'family': family, 'scope': scope,
                'answer': {'kind': 'quantity', 'value': value, 'unit': unit}, 'value': value,
                'numbers': [value, len(els)] + ([missing] if missing else []),
                'evidence': {'boxes': [], 'rooms': [], 'rows': [
                    {'id': e['guid'], 'name': e['name'], 'value': e['qto']} for e in els]},
                'summary': f"{family.replace('_', ' ')} on '{st}' = {value} {unit}, summed over {len(els)} elements' "
                           f'IFC quantities' + (f' ({missing} without the quantity).' if missing else '.')}

    raise ValueError(f'unknown family {family!r}; expected one of {FAMILIES}')


def capabilities(model: IfcModel) -> list[str]:
    """Contract Subjects this drawing answers (rows-only families are listed by `families(model)`)."""
    caps = [s for s in BOX_SUBJECTS if model.elements.get(s)]
    if model.rooms:
        caps.append('room')
    return caps


def families(model: IfcModel) -> list[str]:
    return [f for f in FAMILIES if f == 'area' and model.rooms or model.elements.get(f)]
