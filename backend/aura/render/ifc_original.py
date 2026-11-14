"""The dataset's own storey plan as the "original" of an IFC drawing, aligned exactly to our frame.

The dataset ships `<model>_plan-NN-<storey>.svg/.pdf` sheets rendered from the IFC with IfcConvert. Every room
(`IfcSpace`) in the SVG carries its IFC GUID, which is also our room id, so the sheet-to-frame transform is
fitted from matched room outlines (least squares per axis) instead of being guessed. The PDF (same page as the
SVG viewBox) is then rasterised at that scale and placed into a canvas of the frame's size.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

NUM = re.compile(r'-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?')


def slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def find_sheet(ifc_dir: Path, model: str, storey: str) -> tuple[Path, Path] | None:
    """The plan sheet (svg, pdf) of this storey: `<model>_plan-NN-<slug(storey)>.svg`, when both exist."""
    want = slug(storey)
    for svg in sorted(ifc_dir.glob(f'{model}_plan-*.svg')):
        m = re.match(rf'{re.escape(model)}_plan-\d+-(.+)$', svg.stem)
        if m and m.group(1) == want and svg.with_suffix('.pdf').is_file():
            return svg, svg.with_suffix('.pdf')
    return None


def space_boxes(svg_text: str) -> tuple[tuple[float, float, float, float], dict[str, tuple[float, float, float, float]]]:
    """(viewBox, {guid: (minx, miny, maxx, maxy)}) of the sheet's rooms, walls and columns, in sheet units (mm).
    Each element group carries its IFC GUID."""
    root = ET.fromstring(svg_text)
    vb = tuple(float(v) for v in root.get('viewBox', '0 0 0 0').replace(',', ' ').split())
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for g in root.iter():
        cls = g.get('class') or ''
        guid = g.get('data-guid')
        if not guid or not cls.startswith(('IfcSpace', 'IfcWall', 'IfcColumn')):
            continue
        xs: list[float] = []
        ys: list[float] = []
        for p in g.iter():
            nums = [float(n) for n in NUM.findall(p.get('d', ''))]
            xs += nums[0::2]
            ys += nums[1::2]
        if xs and ys:
            boxes[guid] = (min(xs), min(ys), max(xs), max(ys))
    return vb, boxes  # type: ignore[return-value]


def _fit(pairs: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares px = a·mm + b; returns (a, b, worst residual in px)."""
    n = len(pairs)
    sx = sum(m for m, _ in pairs)
    sy = sum(p for _, p in pairs)
    sxx = sum(m * m for m, _ in pairs)
    sxy = sum(m * p for m, p in pairs)
    den = n * sxx - sx * sx
    if n < 2 or abs(den) < 1e-12:
        raise ValueError('degenerate fit')
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return a, b, max(abs(a * m + b - p) for m, p in pairs)


def fit_transform(model, sheet_boxes: dict[str, tuple[float, float, float, float]]):
    """(ax, bx, ay, by, residual_px, matched) from rooms and walls present in both the model's storey and the
    sheet, matched by GUID. Door and window symbols are left out (their swings and glazing marks differ between
    renderers). Robust to outlines that differ between the two renderers (consensus, then least squares)."""
    elements = [(r['room_id'], r['polygon']) for r in model.rooms if r.get('storey') == model.storey and r.get('polygon')]
    for fam in ('wall', 'column'):
        elements += [(g['guid'], g['polygon']) for g in model.footprints.get(fam, []) if g.get('polygon')]
    obs = []  # (sheet box, frame box) per matched element
    for guid, poly in elements:
        if guid not in sheet_boxes:
            continue
        pts = [model.T(x, y) for x, y in poly]
        obs.append((sheet_boxes[guid], (min(p[0] for p in pts), min(p[1] for p in pts),
                                        max(p[0] for p in pts), max(p[1] for p in pts))))
    if len(obs) < 2:
        raise ValueError(f'only {len(obs)} elements match the sheet')
    # Consensus: each element's box alone fixes scale and offset on both axes; keep the candidate most elements
    # agree with (within 3 px), then refit on those. A wrong outline, however far off, cannot pull the result.
    best: list = []
    for s_box, o_box in obs:
        if s_box[2] - s_box[0] < 1e-6 or s_box[3] - s_box[1] < 1e-6:
            continue
        cax = (o_box[2] - o_box[0]) / (s_box[2] - s_box[0])
        cay = (o_box[3] - o_box[1]) / (s_box[3] - s_box[1])
        cand = (cax, o_box[0] - cax * s_box[0], cay, o_box[1] - cay * s_box[1])
        inliers = [e for e in obs if _box_err(e, cand) <= 3.0]
        if len(inliers) > len(best):
            best = inliers
    # Two outlines can agree by chance (roof storeys clip rooms and walls differently); ask for three.
    if len(best) < 3:
        raise ValueError(f'only {len(best)} elements agree on one transform')
    obs = best
    ax, bx, _rx = _fit([(s[0], o[0]) for s, o in obs] + [(s[2], o[2]) for s, o in obs])
    ay, by, _ry = _fit([(s[1], o[1]) for s, o in obs] + [(s[3], o[3]) for s, o in obs])
    if ax <= 0 or ay <= 0:
        raise ValueError('sheet is mirrored relative to the plan')
    return ax, bx, ay, by, max(_box_err(e, (ax, bx, ay, by)) for e in obs), len(obs)


def _box_err(e, t) -> float:
    """Worst corner distance (px) between an element's frame box and its sheet box mapped by t."""
    (s, o), (ax, bx, ay, by) = e, t
    return max(abs(ax * s[0] + bx - o[0]), abs(ax * s[2] + bx - o[2]),
               abs(ay * s[1] + by - o[1]), abs(ay * s[3] + by - o[3]))


def model_map(model, t) -> tuple[float, float, float, float]:
    """The frame↔sheet transform t expressed in model metres: sheet_x = kx·X + cx, sheet_y = ky·Y + cy."""
    ax, bx, ay, by = t
    (p0x, p0y), (p1x, p1y) = model.T(0.0, 0.0), model.T(1.0, 1.0)
    m0x, m1x = (p0x - bx) / ax, (p1x - bx) / ax
    m0y, m1y = (p0y - by) / ay, (p1y - by) / ay
    return m1x - m0x, m0x, m1y - m0y, m0y


def transform_from_map(model, mmap) -> tuple[float, float, float, float]:
    """Frame px = a·mm + b for this storey's frame, from a model-space sheet mapping of the same model."""
    kx, cx, ky, cy = mmap
    xa, xb = model.T((0 - cx) / kx, 0.0)[0], model.T((100 - cx) / kx, 0.0)[0]
    ya, yb = model.T(0.0, (0 - cy) / ky)[1], model.T(0.0, (100 - cy) / ky)[1]
    return (xb - xa) / 100, xa, (yb - ya) / 100, ya


def _borrowed(model, boxes, mmap):
    """Use a sibling sheet's mapping (one IfcConvert export draws every storey with the same mapping), after
    checking it against this storey: at least two matched elements must line up on at least one axis."""
    ax, bx, ay, by = transform_from_map(model, mmap)
    if ax <= 0 or ay <= 0:
        raise ValueError('borrowed mapping is mirrored for this storey')
    agree = 0
    for guid, poly in [(r['room_id'], r['polygon']) for r in model.rooms if r.get('storey') == model.storey and r.get('polygon')] + \
            [(g['guid'], g['polygon']) for g in model.footprints.get('wall', []) if g.get('polygon')]:
        if guid not in boxes:
            continue
        pts = [model.T(x, y) for x, y in poly]
        sb = boxes[guid]
        ex = max(abs(ax * sb[0] + bx - min(p[0] for p in pts)), abs(ax * sb[2] + bx - max(p[0] for p in pts)))
        ey = max(abs(ay * sb[1] + by - min(p[1] for p in pts)), abs(ay * sb[3] + by - max(p[1] for p in pts)))
        agree += min(ex, ey) <= 3.0
    if agree < 2:
        raise ValueError('a sibling sheet\'s mapping does not fit this storey')
    return ax, bx, ay, by, agree


def render_original(model, svg: Path, pdf: Path, sibling_map=None) -> tuple[Image.Image, dict]:
    """The sheet rasterised into the frame (W×H px, white elsewhere), plus how it was aligned:
    method 'fit' (from this storey's own elements; carries `model_map` for siblings) or 'borrowed'."""
    import pypdfium2 as pdfium

    vb, boxes = space_boxes(svg.read_text(encoding='utf-8'))
    try:
        ax, bx, ay, by, resid, matched = fit_transform(model, boxes)
        info = {'method': 'fit', 'matched': matched, 'residual_px': round(resid, 2),
                'model_map': model_map(model, (ax, bx, ay, by))}
    except ValueError:
        if sibling_map is None:
            raise
        ax, bx, ay, by, agree = _borrowed(model, boxes, sibling_map)
        info = {'method': 'borrowed', 'matched': agree, 'residual_px': None}
    W, H = model.frame['px']
    doc = pdfium.PdfDocument(str(pdf))
    page = doc[0]
    pt_per_mm = 72 / 25.4
    # One render scale for both axes (the sheet is drawn at one scale); the fit's axes agree to rounding.
    scale = ((ax + ay) / 2) / pt_per_mm
    img = page.render(scale=scale).to_pil().convert('RGB')
    doc.close()
    # Sheet point (mm) m lands at render pixel (m - vb0)·a, and at frame pixel a·m + b.
    off_x = round(ax * vb[0] + bx)
    off_y = round(ay * vb[1] + by)
    canvas = Image.new('RGB', (W, H), 'white')
    canvas.paste(img, (off_x, off_y))
    return canvas, info
