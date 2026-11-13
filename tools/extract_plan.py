# Export one swiss_dwellings DXF to SVG-ready JSON for the mockup. Usage: python -I extract_plan.py <dxf> <out.json>
import ezdxf, sys, json, re, collections
from shapely.geometry import Polygon, Point, box

doc = ezdxf.readfile(sys.argv[1]); msp = doc.modelspace()
S = 40.0  # px per metre
pts = [p for e in msp.query('LWPOLYLINE') for p in e.get_points('xy')]
minx = min(p[0] for p in pts) - 0.6; maxy = max(p[1] for p in pts) + 0.6
maxx = max(p[0] for p in pts) + 0.6; miny = min(p[1] for p in pts) - 0.6
W = round((maxx - minx) * S); H = round((maxy - miny) * S)
T = lambda x, y: (round((x - minx) * S, 1), round((maxy - y) * S, 1))

def path(points, closed):
    q = [T(*p) for p in points]
    d = 'M' + ' L'.join(f'{x} {y}' for x, y in q)
    return d + (' Z' if closed else '')

def bb(poly, pad=0.0):
    x0, y0, x1, y1 = poly.bounds
    a = T(x0 - pad, y1 + pad); b = T(x1 + pad, y0 - pad)
    return dict(x=a[0], y=a[1], w=round(b[0] - a[0], 1), h=round(b[1] - a[1], 1))

walls = [path(list(e.get_points('xy')), True) for e in msp.query('LWPOLYLINE[layer=="WALL"]')]
lines = collections.defaultdict(list)
for layer in ('DOOR', 'WINDOW', 'FIXTURE'):
    for e in msp.query(f'*[layer=="{layer}"]'):
        t = e.dxftype()
        if t == 'LWPOLYLINE':
            lines[layer].append(path(list(e.get_points('xy')), e.closed))
        elif t == 'ARC':
            c = e.dxf.center; r = e.dxf.radius
            p0 = T(e.start_point.x, e.start_point.y); p1 = T(e.end_point.x, e.end_point.y)
            sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
            lines[layer].append(f'M{p0[0]} {p0[1]} A{r*S:.1f} {r*S:.1f} 0 {1 if sweep>180 else 0} 0 {p1[0]} {p1[1]}')
        elif t == 'CIRCLE':
            c = T(e.dxf.center.x, e.dxf.center.y); r = e.dxf.radius * S
            lines[layer].append(f'M{c[0]-r:.1f} {c[1]} a{r:.1f} {r:.1f} 0 1 0 {2*r:.1f} 0 a{r:.1f} {r:.1f} 0 1 0 {-2*r:.1f} 0')

# rooms + labels
texts = [(e.dxf.text.strip(), e.dxf.insert[0], e.dxf.insert[1]) for e in msp.query('TEXT[layer=="ROOM_TEXT"]')]
rooms_poly = [Polygon(list(e.get_points('xy'))).buffer(0) for e in msp.query('LWPOLYLINE[layer=="ROOM_OUTLINE"]')]
units = [Polygon(list(e.get_points('xy'))).buffer(0) for e in msp.query('LWPOLYLINE[layer=="UNIT_OUTLINE"]')]
NUM = re.compile(r'\d+\.\d+')
unit_name = {}
for t, x, y in texts:
    if t.startswith('App.'):
        for i, u in enumerate(units):
            if u.buffer(0.05).contains(Point(x, y)): unit_name[i] = t.split()[-1]
rooms = []
for t, x, y in texts:
    if not NUM.fullmatch(t): continue
    poly = next((r for r in rooms_poly if r.contains(Point(x, y))), None)
    if poly is None: continue
    near = sorted(texts, key=lambda q: abs(q[1] - x) + abs(q[2] - y))
    area = next((float(q[0].split()[0]) for q in near if q[0].endswith('m²') and q[2] < y and abs(q[1]-x) < 0.1), None)
    name = next((q[0] for q in near if not NUM.fullmatch(q[0]) and not q[0].endswith('m²') and not q[0].startswith('App.') and q[2] > y and abs(q[1]-x) < 0.1), '')
    ui = next((i for i, u in enumerate(units) if u.buffer(0.05).contains(Point(x, y))), None)
    ext = list(poly.exterior.coords)
    lx, ly = T(x, y)
    rooms.append(dict(num=t, name=name, area=area, unit=unit_name.get(ui), outdoor=bool(re.match('Terrasse', name)),
                      d=path(ext, True), lx=lx, ly=ly, geo=round(poly.area, 2)))

# windows: outer bboxes (same rule as probe)
wb = [box(*Polygon(list(e.get_points('xy'))).bounds) for e in msp.query('LWPOLYLINE[layer=="WINDOW"]') if len(list(e.get_points())) > 2]
outer = [b for i, b in enumerate(wb) if not any(j != i and o.buffer(1e-3).contains(b) and o.area > b.area for j, o in enumerate(wb))]
windows = [bb(b, 0.12) for b in outer]
# doors: polylines with >=4 points (probe rule)
dpolys = [Polygon(list(e.get_points('xy'))) for e in msp.query('LWPOLYLINE[layer=="DOOR"]') if len(e) >= 4]
doors = [bb(p, 0.08) for p in dpolys]
# fixtures: classify rects by inner marks (probe rule)
inner = []; rects = []
for e in msp.query('*[layer=="FIXTURE"]'):
    if e.dxftype() == 'CIRCLE':
        inner.append(('dot' if e.dxf.radius < 0.05 else 'burner', Point(e.dxf.center[0], e.dxf.center[1]))); continue
    p = list(e.get_points('xy'))
    if e.closed and len(p) == 4: rects.append(Polygon(p))
    else: inner.append(({48: 'ell', 69: 'tub'}.get(len(p), 'line'), Polygon(p).centroid if len(p) > 2 else Point(*p[0])))
fixtures = collections.defaultdict(list)
for r in rects:
    ins = collections.Counter(k for k, pt in inner if r.buffer(0.01).contains(pt))
    kind = 'bathtub' if ins['tub'] else 'washbasin' if (ins['ell'] and ins['dot']) else 'wc' if ins['ell'] else 'kitchen' if ins['burner'] else 'shower' if ins['dot'] else 'other'
    fixtures[kind].append(bb(r, 0.1))

out = dict(w=W, h=H, scale=S, walls=walls, doorLines=lines['DOOR'], windowLines=lines['WINDOW'], fixtureLines=lines['FIXTURE'],
           units=[dict(name=unit_name.get(i), d=path(list(u.exterior.coords), True)) for i, u in enumerate(units)],
           rooms=rooms, windows=windows, doors=doors, fixtures=fixtures)
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False, separators=(',', ':'))
A = sum(r['area'] for r in rooms if r['unit'] == 'A' and not r['outdoor'])
B = sum(r['area'] for r in rooms if r['unit'] == 'B' and not r['outdoor'])
print('size', W, H, 'windows', len(windows), 'doors', len(doors), {k: len(v) for k, v in fixtures.items()},
      'rooms', len(rooms), 'A', round(A, 2), 'B', round(B, 2), 'units', unit_name)
print([(r['num'], r['name'], r['area'], r['unit']) for r in rooms])
