# plan.json -> plan.svg (base drawing) + mock.json (overlay data in % of the drawing). Usage: python -I build_mock.py <dir>
import json, sys, re, math, os
d = sys.argv[1]
P = json.load(open(os.path.join(d, 'plan.json')))
W, H = P['w'], P['h']

def esc(s): return s.replace('&', '&amp;').replace('<', '&lt;')

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Inter, Helvetica, Arial, sans-serif">',
       f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
for r in P['rooms']:
    if r['outdoor']: svg.append(f'<path d="{r["d"]}" fill="#eef0f3" stroke="none"/>')
for r in P['rooms']:
    svg.append(f'<path d="{r["d"]}" fill="none" stroke="#c9ced6" stroke-width="0.8"/>')
for u in P['units']:
    svg.append(f'<path d="{u["d"]}" fill="none" stroke="#b4533c" stroke-width="1.4" stroke-dasharray="10 4 2 4"/>')
for p in P['fixtureLines']: svg.append(f'<path d="{p}" fill="none" stroke="#6b7280" stroke-width="0.9"/>')
for p in P['windowLines']: svg.append(f'<path d="{p}" fill="#ffffff" stroke="#374151" stroke-width="0.9"/>')
for p in P['doorLines']: svg.append(f'<path d="{p}" fill="none" stroke="#4b5563" stroke-width="0.9"/>')
for p in P['walls']: svg.append(f'<path d="{p}" fill="#2f343c" stroke="#2f343c" stroke-width="0.6"/>')
for r in P['rooms']:
    x, y = r['lx'], r['ly']
    small = (r['area'] or 0) < 8
    fs = 9 if small else 11
    if r['name']: svg.append(f'<text x="{x}" y="{y-14 if not small else y-11}" font-size="{fs}" fill="#374151" text-anchor="middle">{esc(r["name"])}</text>')
    svg.append(f'<text x="{x}" y="{y}" font-size="{fs}" font-weight="700" fill="#111827" text-anchor="middle">{r["num"]}</text>')
    if r['area']: svg.append(f'<text x="{x}" y="{y+(13 if not small else 10)}" font-size="{fs-1}" fill="#4b5563" text-anchor="middle">{r["area"]:.2f} m²</text>')
svg.append('</svg>')
open(os.path.join(d, 'plan.svg'), 'w').write('\n'.join(svg))

# room polygons in % for clip-path, and centroid for hit-testing
def pts(dstr):
    return [tuple(map(float, m.groups())) for m in re.finditer(r'(-?[\d.]+) (-?[\d.]+)', dstr)]
pct = lambda x, y: (round(x / W * 100, 2), round(y / H * 100, 2))
rooms = []
for r in P['rooms']:
    if not r['unit']: continue
    pp = pts(r['d'])
    clip = 'polygon(' + ', '.join(f'{a}% {b}%' for a, b in (pct(*q) for q in pp)) + ')'
    rooms.append(dict(num=r['num'], name=r['name'], area=r['area'], unit=r['unit'], outdoor=r['outdoor'], clip=clip,
                      lx=pct(r['lx'], r['ly'])[0], ly=pct(r['lx'], r['ly'])[1], pts=pp))

def inside(pt, poly):
    x, y = pt; c = False
    for i in range(len(poly)):
        x1, y1 = poly[i]; x2, y2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
    return c

def nearest_room(b):
    cx, cy = b['x'] + b['w'] / 2, b['y'] + b['h'] / 2
    best = None
    for r in rooms:
        if r['outdoor']: continue
        if inside((cx, cy), r['pts']): return r
        dmin = min(math.hypot(px - cx, py - cy) for px, py in r['pts'])
        # distance to polygon edges, approximated by sampling edges
        for i in range(len(r['pts'])):
            (x1, y1), (x2, y2) = r['pts'][i], r['pts'][i - 1]
            for t in (0.25, 0.5, 0.75):
                dmin = min(dmin, math.hypot(x1 + (x2 - x1) * t - cx, y1 + (y2 - y1) * t - cy))
        if best is None or dmin < best[0]: best = (dmin, r)
    return best[1]

def boxes(lst):
    out = []
    order = sorted(lst, key=lambda b: (round((b['y'] + b['h'] / 2) / 60), b['x']))
    for b in order:
        r = nearest_room(b)
        x, y = pct(b['x'], b['y'])
        out.append(dict(x=x, y=y, w=round(b['w'] / W * 100, 2), h=round(b['h'] / H * 100, 2),
                        room=f"{r['name']} {r['num']}", unit=r['unit']))
    return out

mock = dict(w=W, h=H, rooms=[{k: v for k, v in r.items() if k != 'pts'} for r in rooms],
            windows=boxes(P['windows']), doors=boxes(P['doors']), washbasins=boxes(P['fixtures']['washbasin']))
json.dump(mock, open(os.path.join(d, 'mock.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
print('svg bytes', os.path.getsize(os.path.join(d, 'plan.svg')), 'mock bytes', os.path.getsize(os.path.join(d, 'mock.json')))
print([w['room'] for w in mock['windows']])
print([w['room'] for w in mock['doors']])
