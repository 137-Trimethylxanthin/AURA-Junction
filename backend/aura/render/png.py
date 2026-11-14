"""Pillow renders against the dataset PNG: page alignment, original.png, thumbnail, numbered overlays. Alignment is approximate (generalised tools/align.py): the drawing's dark-pixel bbox on
the page, above the legend/title block, is matched to the DXF extents."""

from __future__ import annotations

import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aura.solvers.swiss import MARGIN, SCALE, SwissModel

INSET = 90  # page frame border inset in px (align.py)
COLORS = {'window': (37, 99, 235), 'door': (234, 88, 12), 'washbasin': (22, 163, 74), 'wc': (147, 51, 234),
          'bathtub': (8, 145, 178), 'shower': (219, 39, 119), 'label': (100, 100, 100)}


def _legend_row(a: np.ndarray) -> int:
    """Top row of the legend/title block: the first long horizontal rule in the lower part of the page."""
    H, W = a.shape
    inner = a[:, INSET:W - INSET] < 160
    frac = inner.mean(axis=1)
    for y in range(int(H * 0.6), int(H * 0.97)):
        if frac[y] > 0.8:
            return y
    return int(H * 0.80)


def align(png_path, model: SwissModel) -> dict:
    """Affine map DXF metres -> page px: X = ox + (x - minx) * s, Y = oy + (maxy - y) * s."""
    a = np.array(Image.open(png_path).convert('L'))
    H0, W0 = a.shape
    bottom = _legend_row(a) - 8
    sub = a[INSET:bottom, INSET:W0 - INSET]
    ys, xs = np.where(sub < 200)
    px0, px1 = xs.min() + INSET, xs.max() + INSET
    py0, py1 = ys.min() + INSET, ys.max() + INSET
    (minx, miny), (maxx, maxy) = model.frame['dxf_min'], model.frame['dxf_max']
    sx = (px1 - px0) / (maxx - minx)
    sy = (py1 - py0) / (maxy - miny)
    s = (sx + sy) / 2
    # Prefer the printed scale (A3 sheet, long side 420 mm) when it is legible in the model's texts.
    printed = None
    m = re.search(r'1:(\d+)', model.subtitle or '')
    if m:
        printed = max(W0, H0) / 420.0 * 1000.0 / int(m.group(1))
        if abs(printed - s) / s < 0.03:
            s = printed
    ox = (px0 + px1) / 2 - (maxx - minx) * s / 2
    oy = (py0 + py1) / 2 - (maxy - miny) * s / 2
    return {'ox': float(ox), 'oy': float(oy), 's': float(s), 'sx': float(sx), 'sy': float(sy),
                'printed_s': printed, 'page': [W0, H0], 'bbox': [int(px0), int(py0), int(px1), int(py1)]}


def pct_to_page(model: SwissModel, al: dict, x_pct: float, y_pct: float) -> tuple[float, float]:
    """Rebuilt-frame Pct -> dataset page px."""
    xm = x_pct / 100 * model.W / SCALE + model.frame['dxf_min'][0] - MARGIN
    ym = model.frame['dxf_max'][1] + MARGIN - y_pct / 100 * model.H / SCALE
    return al['ox'] + (xm - model.frame['dxf_min'][0]) * al['s'], al['oy'] + (model.frame['dxf_max'][1] - ym) * al['s']


def original_crop(png_path, model: SwissModel, al: dict) -> Image.Image:
    """The page cropped to the rebuilt frame (extents +- 0.6 m) and resized to the frame px."""
    x0, y0 = pct_to_page(model, al, 0, 0)
    x1, y1 = pct_to_page(model, al, 100, 100)
    page = Image.open(png_path).convert('RGB')
    return page.crop((round(x0), round(y0), round(x1), round(y1))).resize((model.W, model.H), Image.LANCZOS)


def thumbnail(img: Image.Image, width: int = 480) -> Image.Image:
    t = img.copy()
    t.thumbnail((width, width * 4))
    return t


def _font(size: int):
    for name in ('DejaVuSans-Bold.ttf', 'Arial Bold.ttf', 'LiberationSans-Bold.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def overlay(base: Image.Image, boxes: list[dict], rooms: list[dict] | None = None, scale: float = 1.0) -> Image.Image:
    """Numbered evidence boxes (and optional room polygons) drawn on `base` (frame-aligned image)."""
    img = base.convert('RGB').resize((round(base.width * scale), round(base.height * scale)))
    W, H = img.size
    d = ImageDraw.Draw(img, 'RGBA')
    f = _font(max(12, W // 90))
    for r in rooms or []:
        pts = [(x / 100 * W, y / 100 * H) for x, y in r['points']]
        if len(pts) >= 3:
            d.polygon(pts, outline=(180, 83, 60, 255), fill=(180, 83, 60, 30))
    # Boxes with identical geometry (double doors: two leaves at one spot) share one rectangle and one
    # badge listing every number, e.g. "3+4", so no count is hidden under another badge.
    groups: dict[tuple, list[dict]] = {}
    for b in boxes:
        groups.setdefault((b['subject'], b['x'], b['y'], b['w'], b['h']), []).append(b)
    for grp in groups.values():
        b = grp[0]
        c = COLORS.get(b['subject'], (220, 38, 38))
        x0, y0 = b['x'] / 100 * W, b['y'] / 100 * H
        x1, y1 = (b['x'] + b['w']) / 100 * W, (b['y'] + b['h']) / 100 * H
        d.rectangle((x0, y0, x1, y1), outline=c + (255,), width=3, fill=c + (40,))
        label = '+'.join(g['id'].split('-')[-1].lstrip('0') or '0' for g in grp)
        tw = d.textlength(label, font=f)
        d.rectangle((x0, y0 - f.size - 4, x0 + tw + 6, y0), fill=c + (230,))
        d.text((x0 + 3, y0 - f.size - 3), label, fill=(255, 255, 255), font=f)
    return img
