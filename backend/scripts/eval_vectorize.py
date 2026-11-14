"""Evaluate a raster-plan vectorizer.

    cd backend && uv run python scripts/eval_vectorize.py --module aura.vectorize [--tag a]

The module must expose `vectorize_image(path) -> dict` and `plan_svg(vec) -> str` (see aura/vectorize.py for
the dict shape: walls as {"outer","holes"} or plain rings, rooms as {"points","area_px"}).

1. Swiss crop (design/assets/original-crop.png, 1221x1220): rooms vs design/assets/mock.json clips (best-IoU
   match per truth room), walls vs the #2f343c paths of design/assets/plan.svg (pixel IoU).
2. Synthetic plan (PIL): L-shaped room + two rectangles, 8 px walls, door gaps, dimension lines, text.
3. Runtime and peak memory (tracemalloc for Python allocations + ru_maxrss of the process).
Writes scripts/out/vectorize_<tag>_swiss.svg and overlay PNGs.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import resource
import sys
import time
import tracemalloc
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
ASSETS = ROOT / 'design' / 'assets'
OUT = BACKEND / 'scripts' / 'out'


def _fill(shape, polys, holes_ok=True) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for p in polys:
        if isinstance(p, dict):
            rings = [p.get('outer') or p.get('points'), *p.get('holes', [])]
        else:
            rings = [p]
        tmp = np.zeros(shape, np.uint8)
        for r in rings:
            if r and len(r) >= 3:
                tmp ^= cv2.fillPoly(np.zeros(shape, np.uint8), [np.round(np.array(r, float)).astype(np.int32)], 1)
        m |= tmp
    return m


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def _truth_rooms(w: int, h: int) -> list[dict]:
    d = json.loads((ASSETS / 'mock.json').read_text())
    out = []
    for r in d['rooms']:
        pts = re.findall(r'([\d.]+)%\s+([\d.]+)%', r['clip'])
        out.append({'name': f"{r['num']} {r['name']}", 'pts': [[float(x) / 100 * w, float(y) / 100 * h] for x, y in pts]})
    return out


def _svg_walls(svg: str) -> list[dict]:
    """Each #2f343c path as {"outer", "holes"} (sub-paths after the first are XORed: even-odd)."""
    out = []
    for d in re.findall(r'<path d="([^"]*)" fill="#2f343c"', svg):
        rings = []
        for sub in re.split(r'(?=M)', d):
            nums = [float(v) for v in re.findall(r'-?[\d.]+', sub)]
            if len(nums) >= 6:
                rings.append([nums[i:i + 2] for i in range(0, len(nums) - 1, 2)])
        if rings:
            out.append({'outer': rings[0], 'holes': rings[1:]})
    return out


def _room_rings(vec) -> list:
    return [r['points'] if isinstance(r, dict) else r for r in vec.get('rooms', [])]


def _overlay(img_path: Path, vec: dict, dst: Path):
    im = cv2.cvtColor(np.asarray(Image.open(img_path).convert('RGB')), cv2.COLOR_RGB2BGR)
    lay = im.copy()
    rng = np.random.default_rng(1)
    for r in _room_rings(vec):
        col = tuple(int(c) for c in rng.integers(60, 230, 3))
        cv2.fillPoly(lay, [np.round(np.array(r)).astype(np.int32)], col)
    out = cv2.addWeighted(lay, 0.45, im, 0.55, 0)
    for w in vec.get('walls', []):
        for ring in [w['outer'], *w.get('holes', [])] if isinstance(w, dict) else [w]:
            cv2.polylines(out, [np.round(np.array(ring)).astype(np.int32)], True, (0, 0, 255), 2)
    for r in _room_rings(vec):
        p = np.round(np.array(r)).astype(np.int32)
        cv2.polylines(out, [p], True, (255, 0, 0), 1)
        for q in p:
            cv2.circle(out, tuple(int(v) for v in q), 3, (255, 0, 0), -1)
    for w in vec.get('windows', []):
        cv2.polylines(out, [np.round(np.array(w)).astype(np.int32)], True, (0, 200, 0), 2)
    for d in vec.get('doors', []):
        if isinstance(d, dict):
            cv2.rectangle(out, (int(d['x']), int(d['y'])), (int(d['x'] + d['w']), int(d['y'] + d['h'])), (0, 160, 255), 2)
    cv2.imwrite(str(dst), out)


def _run(mod, path: Path):
    tracemalloc.start()
    t0 = time.perf_counter()
    vec = mod.vectorize_image(path)
    svg = mod.plan_svg(vec)
    dt = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    return vec, svg, dt, peak


def eval_swiss(mod, tag: str) -> dict:
    img = ASSETS / 'original-crop.png'
    vec, svg, dt, peak = _run(mod, img)
    w, h = vec['width'], vec['height']
    (OUT / f'vectorize_{tag}_swiss.svg').write_text(svg, encoding='utf-8')
    (OUT / f'vectorize_{tag}_swiss.json').write_text(json.dumps(vec), encoding='utf-8')
    _overlay(img, vec, OUT / f'vectorize_{tag}_swiss_overlay.png')
    shape = (h, w)
    preds = [_fill(shape, [r]) for r in _room_rings(vec)]
    rows = []
    for t in _truth_rooms(w, h):
        tm = _fill(shape, [t['pts']])
        best = max((_iou(tm, p) for p in preds), default=0.0)
        rows.append((t['name'], best))
    ious = [b for _, b in rows]
    nonrect = sum(1 for r in _room_rings(vec) if len(r) > 4)
    truth_walls = _fill(shape, _svg_walls((ASSETS / 'plan.svg').read_text()))
    pred_walls = _fill(shape, _svg_walls(svg))
    mock = json.loads((ASSETS / 'mock.json').read_text())
    tb = lambda b: (b['x'] / 100 * w, b['y'] / 100 * h, (b['x'] + b['w']) / 100 * w, (b['y'] + b['h']) / 100 * h)
    pb = []
    for o in [*vec.get('windows', []), *vec.get('doors', [])]:
        if isinstance(o, dict):
            pb.append((o['x'], o['y'], o['x'] + o['w'], o['y'] + o['h']))
        else:
            xs, ys = [q[0] for q in o], [q[1] for q in o]
            pb.append((min(xs), min(ys), max(xs), max(ys)))
    hit = lambda a, b: a[0] <= b[2] + 3 and b[0] <= a[2] + 3 and a[1] <= b[3] + 3 and b[1] <= a[3] + 3
    truth_open = [tb(b) for b in mock['windows'] + mock['doors']]
    res = {
        'openings_recall': f"{sum(any(hit(t, q) for q in pb) for t in truth_open)}/{len(truth_open)}",
        'openings_precision': f"{sum(any(hit(q, t) for t in truth_open) for q in pb)}/{len(pb)}",
        'rooms_mean_iou': round(float(np.mean(ious)), 3),
        'rooms_iou_gt_0.7': f'{sum(i > 0.7 for i in ious)}/{len(ious)}',
        'pred_rooms': len(preds), 'pred_nonrect': nonrect,
        'wall_iou': round(_iou(truth_walls, pred_walls), 3),
        'windows': len(vec.get('windows', [])), 'doors': len(vec.get('doors', [])),
        'runtime_s': round(dt, 2), 'peak_py_mb': round(peak, 1),
    }
    print('\n[swiss] per truth room best IoU:')
    for n, b in rows:
        print(f'  {b:5.2f}  {n}')
    return res


def synth(path: Path) -> dict:
    """L room (0,0)-(300,0)-(300,150)-(150,150)-(150,300)-(0,300) offset by (60,60), interior faces;
    rooms B and C to the right. Walls 8 px drawn centred on the face + 4."""
    W, H = 640, 480
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)
    T = 8
    ox, oy = 60, 60

    def wall(x0, y0, x1, y1):
        d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1) - 1, max(y0, y1) - 1], fill=(20, 20, 20))

    # L room interior: x 60..360 (top part y 60..210), x 60..210 (y 210..360)
    L = [(ox, oy), (ox + 300, oy), (ox + 300, oy + 150), (ox + 150, oy + 150), (ox + 150, oy + 300), (ox, oy + 300)]
    # outer walls around L
    wall(ox - T, oy - T, ox + 300 + T, oy)            # top
    wall(ox - T, oy, ox, oy + 300 + T)                # left
    wall(ox, oy + 300, ox + 150 + T, oy + 300 + T)    # bottom of L
    wall(ox + 150, oy + 150, ox + 150 + T, oy + 300)  # inner vertical of L
    wall(ox + 150, oy + 150, ox + 300 + T, oy + 150 + T)  # inner horizontal of L (also top of room C)
    # room B: right of L top arm: x 368..560, y 60..210 ; separating wall x 360..368 with a door gap
    wall(ox + 300, oy - T, ox + 300 + T, oy + 40)
    wall(ox + 300, oy + 80, ox + 300 + T, oy + 150)   # door gap y 100..140
    wall(ox + 300 + T, oy - T, 560 + T, oy)
    wall(560, oy, 560 + T, oy + 300 + T)
    # room C: below B: x 218..560, y 218..360 ; wall between B and C at y 210..218 (from L wall to right)
    wall(ox + 300 + T, oy + 150, 560, oy + 150 + T)
    d.rectangle([420, oy + 150, 459, oy + 150 + T - 1], fill='white')  # door gap B-C
    wall(ox + 150 + T, oy + 300, 560 + T, oy + 300 + T)
    # entrance door gap in the L's left wall
    d.rectangle([ox - T, oy + 200, ox - 1, oy + 239], fill='white')
    # door swing arc + leaf (thin)
    d.arc([ox - 40, oy + 160, ox + 40, oy + 240], 270, 360, fill=(40, 40, 40), width=1)
    d.line([ox, oy + 200, ox + 40, oy + 200], fill=(40, 40, 40), width=1)
    # dimension lines outside + inside, text
    d.line([ox - T, 30, 560 + T, 30], fill=(0, 0, 0), width=1)
    for x in (ox - T, ox + 300, 560 + T):
        d.line([x, 24, x, 36], fill=(0, 0, 0), width=1)
    d.line([20, oy - T, 20, oy + 300 + T], fill=(0, 0, 0), width=1)
    d.line([ox + 20, oy + 120, ox + 260, oy + 120], fill=(90, 90, 90), width=1)  # inner dimension line
    font = ImageFont.load_default()
    d.text((ox + 60, oy + 60), 'Living 24.5 m2', fill='black', font=font)
    d.text((420, 120), 'Bed', fill='black', font=font)
    d.text((300, 280), 'Bath 6.0', fill='black', font=font)
    d.text((250, 18), '12.40', fill='black', font=font)
    # a furniture rectangle (thin)
    d.rectangle([450, 260, 520, 330], outline=(60, 60, 60), width=1)
    im.save(path)
    from shapely.geometry import Polygon
    return {'L': Polygon(L), 'size': (W, H)}


def eval_synth(mod, tag: str) -> dict:
    from shapely.geometry import Polygon
    p = OUT / f'synth_{tag}.png'
    truth = synth(p)
    vec, _svg, dt, _peak = _run(mod, p)
    _overlay(p, vec, OUT / f'synth_{tag}_overlay.png')
    L = truth['L']
    best, bi = None, 0.0
    for r in _room_rings(vec):
        q = Polygon(r).buffer(0)
        i = q.intersection(L).area / q.union(L).area
        if i > bi:
            best, bi = r, i
    ok = best is not None
    nv = len(best) if ok else 0
    area_err = abs(Polygon(best).area - L.area) / L.area if ok else 1.0
    return {'L_iou': round(bi, 3), 'L_vertices': nv, 'L_area_err_pct': round(area_err * 100, 2),
            'L_pass': bool(ok and 5 <= nv <= 8 and area_err <= 0.05), 'synth_rooms': len(vec.get('rooms', [])),
            'runtime_s': round(dt, 2)}


PHOTOS = {'b1': (Path('/root/test-data/b1.jpg'), 'Bedroom #1-#4, Living, 2 baths/kitchen area (~7-8)'),
          'b2': (Path('/root/test-data/b2.jpeg'), 'Bath x2, Kitchen, Living, Bedroom #1-#3 (7)')}


def eval_photos(mod, tag: str) -> dict:
    """Phone photos of paper plans (not in the repo; skipped when missing): rooms found, walls bbox."""
    res = {}
    for key, (p, truth) in PHOTOS.items():
        if not p.exists():
            res[key] = 'missing'
            continue
        vec, svg, dt, peak = _run(mod, p)
        (OUT / f'vectorize_{tag}_{key}.svg').write_text(svg, encoding='utf-8')
        _overlay(p, vec, OUT / f'vectorize_{tag}_{key}_overlay.png')
        xs = [q[0] for w in vec.get('walls', []) for q in (w['outer'] if isinstance(w, dict) else w)]
        res[key] = {'rooms': len(vec.get('rooms', [])), 'truth': truth,
                    'walls': len(vec.get('walls', [])), 'doors': len(vec.get('doors', [])),
                    'windows': len(vec.get('windows', [])),
                    'walls_x_pct': [round(100 * min(xs) / vec['width'], 1),
                                    round(100 * max(xs) / vec['width'], 1)] if xs else None,
                    'runtime_s': round(dt, 2), 'peak_py_mb': round(peak, 1)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module', default='aura.vectorize')
    ap.add_argument('--tag', default=None)
    a = ap.parse_args()
    sys.path.insert(0, str(BACKEND))
    mod = importlib.import_module(a.module)
    tag = a.tag or a.module.rsplit('.', 1)[-1]
    OUT.mkdir(parents=True, exist_ok=True)
    # warm-up (lazy imports inside the module) so runtimes measure the work, not import time
    warm = OUT / f'_warm_{tag}.png'
    Image.new('RGB', (64, 64), 'white').save(warm)
    mod.vectorize_image(warm)
    warm.unlink()
    s = eval_swiss(mod, tag)
    y = eval_synth(mod, tag)
    ph = eval_photos(mod, tag)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print('\n[swiss]', json.dumps(s))
    print('[synth]', json.dumps(y))
    for key, v in ph.items():
        print(f'[{key}]', json.dumps(v, ensure_ascii=False))
    print(f'[process] peak RSS {rss:.0f} MB')


if __name__ == '__main__':
    main()
