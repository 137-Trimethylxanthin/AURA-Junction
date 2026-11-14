"""SwissModel -> contract Drawing JSON (1.1, additive fields only)."""

from __future__ import annotations

from aura.solvers import swiss
from aura.solvers.swiss import MARGIN, SwissModel

CREDIT = {
    'text': 'Swiss Dwellings v3.0.0 by Matthias Standfest et al. (Archilyse AG), doi:10.5281/zenodo.7788422, '
         'CC BY 4.0. Rendered from the dataset geometry; labels and numbering are synthetic.',
    'license': 'CC BY 4.0',
    'license_name': 'CC BY 4.0',
    'url': 'https://doi.org/10.5281/zenodo.7788422',
}


def build_drawing(model: SwissModel, *, base_url: str | None = None, source: str = 'library',
                  original: str | None = 'original.png', thumbnail: str | None = 'thumb.png',
                  facts: dict | None = None) -> dict:
    """Contract Drawing for `model`. Image URLs are `<base_url>/<file>` (default /drawings/<id>)."""
    base = base_url if base_url is not None else f'/drawings/{model.id}'
    f = model.frame
    mx = round((f['dxf_max'][0] - f['dxf_min'][0] + 2 * MARGIN) / 100, 5)
    my = round((f['dxf_max'][1] - f['dxf_min'][1] + 2 * MARGIN) / 100, 5)
    rooms = []
    for r in model.rooms:
        area, _ = swiss._room_area(r, {})
        room = {'room_id': r['room_id'], 'num': r['num'], 'name': r['name'] or ''}
        if r['unit']:
            room['unit'] = r['unit']
        room.update(area_m2=round(area or 0, 2), outdoor=r['outdoor'],
                    points=swiss.room_poly_pct(model, r), label_at=swiss.label_at(model, r))
        if r['area'] is None:
            room['area_source'] = 'outline'  # no printed area: the outline's geometric area (probe rule)
        rect = swiss.room_rect(model, r)
        if rect:
            room['rect'] = rect
        rooms.append(room)
    images = {'rebuilt': f'{base}/plan.svg'}
    if original:
        images['original'] = f'{base}/{original}'
        images['original_alignment'] = 'approximate'
    d = {'contract_version': '1.1', 'id': model.id, 'title': model.title}
    if model.subtitle:
        d['subtitle'] = model.subtitle
    d.update(kind='cad_dxf', status='ready', source=source, capabilities=swiss.capabilities(model))
    if thumbnail:
        d['thumbnail'] = f'{base}/{thumbnail}'
    d.update(frame={'dxf_min': f['dxf_min'], 'dxf_max': f['dxf_max'], 'margin_m': MARGIN, 'm_per_unit': 1,
                        'y_flip': True, 'px': f['px']},
             m_per_pct_x=mx, m_per_pct_y=my, images=images, rooms=rooms,
             units=[{'id': k, 'label': f'Apartment {k}'} for k in sorted(model.applabel)],
             credit=CREDIT)
    if facts:
        d['facts'] = facts
    return d
