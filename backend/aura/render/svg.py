"""SwissModel -> plan.svg (port of tools/extract_plan.py + tools/build_mock.py). The SVG viewBox is the
rebuilt frame in px (40 px/m, extents + 0.6 m), so contract Pct values index it directly."""

from __future__ import annotations

from aura.solvers.swiss import SCALE, SwissModel


def _esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _path(model: SwissModel, pts, closed: bool) -> str:
    q = [model.T(x, y) for x, y in pts]
    d = 'M' + ' L'.join(f'{x} {y}' for x, y in q)
    return d + (' Z' if closed else '')


def _line(model: SwissModel, g: dict) -> str:
    if g['kind'] == 'poly':
        return _path(model, g['pts'], g['closed'])
    if g['kind'] == 'arc':
        p0 = model.T(*g['p0']); p1 = model.T(*g['p1']); r = g['r'] * SCALE
        return f"M{p0[0]} {p0[1]} A{r:.1f} {r:.1f} 0 {1 if g['large'] else 0} 0 {p1[0]} {p1[1]}"
    c = model.T(*g['c']); r = g['r'] * SCALE
    return (f'M{c[0] - r:.1f} {c[1]} a{r:.1f} {r:.1f} 0 1 0 {2 * r:.1f} 0 '
            f'a{r:.1f} {r:.1f} 0 1 0 {-2 * r:.1f} 0')


def render_svg(model: SwissModel) -> str:
    W, H = model.W, model.H
    svg = [(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="Inter, Helvetica, Arial, sans-serif">'),
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    rooms = [r for r in model.rooms if r['polygon']]
    for r in rooms:
        if r['outdoor']:
            svg.append(f'<path d="{_path(model, r["polygon"], True)}" fill="#eef0f3" stroke="none"/>')
    for r in rooms:
        svg.append(f'<path d="{_path(model, r["polygon"], True)}" fill="none" stroke="#c9ced6" stroke-width="0.8"/>')
    for u in model.units:
        if u['polygon']:
            svg.append(f'<path d="{_path(model, u["polygon"], True)}" fill="none" stroke="#b4533c" '
                       f'stroke-width="1.4" stroke-dasharray="10 4 2 4"/>')
    for g in model.lines.get('FIXTURE', []):
        svg.append(f'<path d="{_line(model, g)}" fill="none" stroke="#6b7280" stroke-width="0.9"/>')
    for g in model.lines.get('WINDOW', []):
        svg.append(f'<path d="{_line(model, g)}" fill="#ffffff" stroke="#374151" stroke-width="0.9"/>')
    for g in model.lines.get('DOOR', []):
        svg.append(f'<path d="{_line(model, g)}" fill="none" stroke="#4b5563" stroke-width="0.9"/>')
    for w in model.walls:
        svg.append(f'<path d="{_path(model, w, True)}" fill="#2f343c" stroke="#2f343c" stroke-width="0.6"/>')
    for r in model.rooms:
        x, y = model.T(*r['label_xy'])
        area = r['area']
        small = (area or 0) < 8
        fs = 9 if small else 11
        if r['name']:
            svg.append(f'<text x="{x}" y="{y - 14 if not small else y - 11}" font-size="{fs}" fill="#374151" '
                       f'text-anchor="middle">{_esc(r["name"])}</text>')
        svg.append(f'<text x="{x}" y="{y}" font-size="{fs}" font-weight="700" fill="#111827" '
                   f'text-anchor="middle">{_esc(r["num"])}</text>')
        if area:
            svg.append(f'<text x="{x}" y="{y + (13 if not small else 10)}" font-size="{fs - 1}" fill="#4b5563" '
                       f'text-anchor="middle">{area:.2f} m²</text>')
    svg.append('</svg>')
    return '\n'.join(svg)
