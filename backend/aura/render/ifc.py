"""IfcModel (one storey) -> plan.svg rendered from IFC footprints, and the contract Drawing (kind 'ifc')."""

from __future__ import annotations

from aura.solvers import ifc
from aura.solvers.ifc import IfcModel

CREDITS = {
    'bsc': 'buildingSMART Community sample files',
    'kit': 'KIT IFC examples (Karlsruhe Institute of Technology)',
    'rwth': 'RWTH Aachen DigitalHub model',
    'tuwien': 'TU Wien escape-route test model',
    'bimspeed': 'BIM-SPEED project models',
}


def _esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _path(model: IfcModel, poly) -> str:
    q = [model.T(x, y) for x, y in poly]
    return 'M' + ' L'.join(f'{x} {y}' for x, y in q) + ' Z'


def render_svg(model: IfcModel) -> str:
    W, H = model.frame['px']
    svg = [(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="Inter, Helvetica, Arial, sans-serif">'), f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    rooms = [r for r in model.rooms if r['storey'] == model.storey and r['polygon']]
    for r in rooms:
        svg.append(f'<path d="{_path(model, r["polygon"])}" fill="#f6f7f9" stroke="#c9ced6" stroke-width="0.8"/>')
    style = {'stair': 'fill="none" stroke="#6b7280" stroke-width="0.9"',
             'window': 'fill="#ffffff" stroke="#374151" stroke-width="0.9"',
             'door': 'fill="none" stroke="#4b5563" stroke-width="0.9"',
             'wall': 'fill="#2f343c" stroke="#2f343c" stroke-width="0.4"',
             'column': 'fill="#2f343c" stroke="#2f343c" stroke-width="0.4"'}
    for fam in ('stair', 'wall', 'column', 'window', 'door'):
        for g in model.footprints.get(fam, []):
            if g['polygon']:
                svg.append(f'<path d="{_path(model, g["polygon"])}" {style[fam]}/>')
    fs = max(8, min(12, round(model.frame['scale'] * 0.28)))
    for r in rooms:
        la = ifc.label_at(model, r)
        if la is None:
            continue
        x, y = la[0] / 100 * W, la[1] / 100 * H
        if r['name']:
            svg.append(f'<text x="{x:.1f}" y="{y - fs - 2:.1f}" font-size="{fs}" fill="#374151" '
                       f'text-anchor="middle">{_esc(r["name"])}</text>')
        svg.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" font-weight="700" fill="#111827" '
                   f'text-anchor="middle">{_esc(r["num"])}</text>')
        if r['area']:
            svg.append(f'<text x="{x:.1f}" y="{y + fs + 1:.1f}" font-size="{fs - 1}" fill="#4b5563" '
                       f'text-anchor="middle">{r["area"]:.2f} m²</text>')
    svg.append('</svg>')
    return '\n'.join(svg)


def build_drawing(model: IfcModel, *, base_url: str | None = None, source: str = 'library',
                  facts: dict | None = None, thumbnail: str | None = None, credit: dict | None = None) -> dict:
    base = base_url if base_url is not None else f'/drawings/{model.id}'
    f = model.frame
    W, H = f['px']
    rooms = []
    for r in model.rooms:
        if r['storey'] != model.storey:
            continue
        pts = ifc.room_poly_pct(model, r)
        if pts is None:
            continue  # no footprint: the room still counts and sums, it just cannot be lit
        room = {'room_id': r['room_id'], 'num': r['num'], 'name': r['name'],
                'area_m2': None if r['area'] is None else round(r['area'], 2), 'outdoor': False, 'points': pts,
                'label_at': ifc.label_at(model, r)}
        if r['area_source'] == 'geometry':
            room['area_source'] = 'outline'
        rooms.append(room)
    org = model.source.split('-', 1)[0]
    d = {'contract_version': '1.1', 'id': model.id, 'title': f'{model.project} · {model.storey}',
         'subtitle': f'IFC {model.schema} · storey {model.storey}', 'kind': 'ifc', 'status': 'ready',
         'source': source, 'capabilities': ifc.capabilities(model)}
    if thumbnail:
        d['thumbnail'] = f'{base}/{thumbnail}'
    d.update(frame={'dxf_min': f['min'], 'dxf_max': f['max'], 'margin_m': ifc.MARGIN,
                    'm_per_unit': 1, 'y_flip': True, 'px': [W, H]},
             m_per_pct_x=round(W / f['scale'] / 100, 5), m_per_pct_y=round(H / f['scale'] / 100, 5),
             images={'rebuilt': f'{base}/plan.svg'}, rooms=rooms, units=[],
             credit=credit or {'text': f'{CREDITS.get(org, "IFC sample model")} ({model.source}), via the Stoa dataset. '
                             'Plan rendered from the IFC footprints.',
                     'license': 'see the dataset LICENSE.md for this model',
                     'license_name': 'Licence of the source model (see credits)'})
    if facts:
        d['facts'] = facts
    return d


def raster(model: IfcModel):
    """A Pillow raster of the storey plan (walls, columns, rooms), frame-aligned like plan.svg."""
    from PIL import Image, ImageDraw
    W, H = model.frame['px']
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for r in model.rooms:
        if r['storey'] == model.storey and r['polygon'] and len(r['polygon']) >= 3:
            d.polygon([model.T(x, y) for x, y in r['polygon']], fill=(246, 247, 249), outline=(201, 206, 214))
    for fam, fill in (('stair', None), ('wall', (47, 52, 60)), ('column', (47, 52, 60)),
                      ('window', (255, 255, 255)), ('door', None)):
        for g in model.footprints.get(fam, []):
            if g['polygon'] and len(g['polygon']) >= 3:
                d.polygon([model.T(x, y) for x, y in g['polygon']], fill=fill, outline=(75, 85, 99))
    return img
