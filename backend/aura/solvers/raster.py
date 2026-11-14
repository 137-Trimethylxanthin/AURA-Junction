"""Raster drawings (presenter-only, T2): one Gemini detection call -> door/window boxes in Pct.

Gate G1 was not met (CubiCasa n=30: doors 18/30 exact, windows 17/30), so answers here are a MODEL ESTIMATE:
no tile-check filter, no gold claim, method 'vision'. The request is the frozen CC30 protocol
(experiments/e2g_req.json): gemini-4-hydrogen, temperature 0, JSON, default resolution, image then prompt.

Interface (for aura/jobs.py, L2):
    result = await detect(client, png_bytes, mime_type='image/png', bucket=..., presenter=..., ip_hash=...)
        -> {'boxes': {'door': [Box], 'window': [Box]}, 'counts', 'model', 'input_tokens', 'output_tokens',
            'cost_usd', 'latency_ms', 'items' (raw box_2d list)}            # JSON-serialisable; store as raster.json
    drawing = build_raster_drawing(drawing_id, title, width_px, height_px, image_url)   # contract Drawing
    answer(result, family, scope=None) -> ToolResult dict, same shape as solvers.swiss.answer
"""
from __future__ import annotations

import hashlib
import json
import re

DETECT_MODEL = 'gemini-4-hydrogen'
DETECT_PROMPT = ('Detect every door and every window on this floor plan. Count doorways fitted with a door '
                 '(a door leaf, usually with a swing arc); open doorways without a door leaf do not count. '
                 'Return a JSON list; each item has "label" ("door" or "window") and "box_2d" = '
                 '[ymin, xmin, ymax, xmax] normalised to 0-1000.')
SUBJECTS = ('door', 'window')
PREFIX = {'door': 'door', 'window': 'win'}
ESTIMATE_NOTE = 'Model estimate: boxes found by Gemini on the image; check them, some may be missing or wrong.'
# Measured on 30 held-out CubiCasa5k plans (eval/results/cubicasa.json): exact-count rate per subject.
# Pooled held-out figures of this exact prompt (CC30 n=30 + the few-shot test's baseline arm n=40;
# eval/results/cubicasa.json detection_pooled).
MEASURED = {'door': {'exact': 40, 'n': 70, 'within_1': 56}, 'window': {'exact': 33, 'n': 70, 'within_1': 48},
            'source': 'CubiCasa5k held-out, two samples pooled (n=30 + n=40), 2026-11-14'}


def build_contents(image: bytes, mime_type: str = 'image/png') -> list[dict]:
    import base64
    return [{'role': 'user', 'parts': [
        {'inline_data': {'mime_type': mime_type, 'data': base64.b64encode(image).decode()}},
        {'text': DETECT_PROMPT}]}]


def parse_items(text: str) -> list[dict]:
    """The model's JSON list -> [{label, box_2d}] (malformed items dropped)."""
    t = text.strip()
    m = re.search(r'\[.*\]', t, re.DOTALL)
    try:
        items = json.loads(m.group(0) if m else t)
    except (ValueError, AttributeError):
        return []
    out = []
    for i in items if isinstance(items, list) else []:
        b = i.get('box_2d') if isinstance(i, dict) else None
        if i.get('label') in SUBJECTS and isinstance(b, list) and len(b) == 4 and all(
                isinstance(v, (int, float)) for v in b):
            out.append({'label': i['label'], 'box_2d': [float(v) for v in b]})
    return out


def boxes_from_items(items: list[dict]) -> dict[str, list[dict]]:
    """box_2d (0-1000, [ymin,xmin,ymax,xmax]) -> contract Boxes in Pct, reading order (top, then left)."""
    out = {}
    for s in SUBJECTS:
        rows = []
        for it in items:
            if it['label'] != s:
                continue
            y0, x0, y1, x1 = (min(max(v / 10, 0.0), 100.0) for v in it['box_2d'])
            x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
            rows.append({'x': round(x0, 2), 'y': round(y0, 2), 'w': round(x1 - x0, 2), 'h': round(y1 - y0, 2)})
        rows.sort(key=lambda b: (b['y'], b['x']))
        out[s] = [{'id': f'{PREFIX[s]}-{k + 1:02d}', **b, 'subject': s, 'note': ESTIMATE_NOTE}
                  for k, b in enumerate(rows)]
    return out


async def detect(client, image: bytes, *, mime_type: str = 'image/png', bucket: str = 'public',
                 presenter: bool = False, ip_hash: str | None = None, model: str = DETECT_MODEL) -> dict:
    """One generateContent call (purpose 'raster_detect', so the budget's raster rules apply)."""
    g = await client.generate(build_contents(image, mime_type), purpose='raster_detect', bucket=bucket,
                              presenter=presenter, ip_hash=ip_hash, model=model,
                              config={'responseMimeType': 'application/json'})
    items = parse_items(g.text)
    boxes = boxes_from_items(items)
    return {'boxes': boxes, 'counts': {s: len(v) for s, v in boxes.items()}, 'items': items,
            'model': g.model, 'input_tokens': g.input_tokens, 'output_tokens': g.output_tokens,
            'cost_usd': g.cost_usd, 'latency_ms': g.latency_ms}


def build_raster_drawing(drawing_id: str, title: str, width_px: int, height_px: int, image_url: str, *,
                         source: str = 'upload', credit: dict | None = None) -> dict:
    """Contract Drawing for an uploaded image: Pct refers to the image itself (frame = image px)."""
    d = {'contract_version': '1.1', 'id': drawing_id, 'title': title, 'kind': 'raster', 'status': 'ready',
         'source': source, 'capabilities': list(SUBJECTS),
         'frame': {'dxf_min': [0, 0], 'dxf_max': [width_px, height_px], 'margin_m': 0, 'm_per_unit': 1,
                   'y_flip': True, 'px': [width_px, height_px]},
         'm_per_pct_x': 0, 'm_per_pct_y': 0,  # no scale on a raster: areas are not answered
         'images': {'rebuilt': image_url}, 'rooms': [], 'units': [],
         'credit': credit or {'text': 'Uploaded image.', 'license': 'user upload',
                             'license_name': 'Your upload'}}
    return d


def _in_rect(b, r) -> bool:
    cx, cy = b['x'] + b['w'] / 2, b['y'] + b['h'] / 2
    return r['x'] <= cx <= r['x'] + r['w'] and r['y'] <= cy <= r['y'] + r['h']


def answer(result: dict, family: str, scope: dict | None = None) -> dict:
    """Door/window counts as a model estimate; everything else abstains (not_supported_yet)."""
    scope = scope or {'kind': 'floor'}
    key = json.dumps([result.get('items'), family, scope], sort_keys=True)
    rid = 'r-' + hashlib.sha1(key.encode()).hexdigest()[:12]
    k = scope.get('kind', 'floor')
    if family not in SUBJECTS or k not in ('floor', 'region'):
        return {'result_id': rid, 'family': family, 'scope': scope,
                'answer': {'kind': 'abstain', 'reason': 'not_supported_yet'}, 'value': None, 'numbers': [],
                'evidence': {'boxes': [], 'rooms': []}, 'method': 'vision', 'estimate': True,
                'summary': f'Image drawings answer only door and window counts (floor or a marked region); '
                           f'{family} by {k} is not supported.'}
    boxes = result['boxes'][family]
    if k == 'region':
        boxes = [b for b in boxes if _in_rect(b, scope['rect'])]
    n = len(boxes)
    m = MEASURED[family]
    return {'result_id': rid, 'family': family, 'scope': scope,
            'answer': {'kind': 'count', 'value': n, 'subject': family}, 'value': n,
            'numbers': [n, m['exact'], m['n']],
            'evidence': {'boxes': boxes, 'rooms': []}, 'method': 'vision', 'estimate': True,
            'measured': m,
            'summary': f'About {n} {family}{"s" if n != 1 else ""}: model estimate from {n} boxes found on the image '
                       f'(exact on {m["exact"]} of {m["n"]} held-out plans, within ±1 on {m["within_1"]}). '
                       'Check the boxes.'}
