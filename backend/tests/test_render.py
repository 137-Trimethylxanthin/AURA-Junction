"""Renderers: Pct placement of a known rectangle, SVG frame, Drawing JSON shape, boxes inside 0-100."""
from __future__ import annotations

import json

import pytest

from aura.render.drawing import build_drawing
from aura.render.svg import render_svg
from aura.solvers import swiss
from tests.test_solver import BUNDLED_JSON, LIBRARY, SWISS, needs_data, synthetic_dxf


@pytest.fixture(scope='module')
def syn(tmp_path_factory):
    return swiss.load(synthetic_dxf(tmp_path_factory.mktemp('dxf')))


def test_frame_of_synthetic(syn):
    # LWPOLYLINE extents x [0, 10], y [-0.2, 10] (the window sits below the wall) + 0.6 m margin at 40 px/m
    assert syn.frame['dxf_min'] == [0.0, -0.2] and syn.frame['dxf_max'] == [10.0, 10.0]
    assert syn.frame['px'] == [round(11.2 * 40), round(11.4 * 40)]


def test_known_rectangle_lands_at_expected_pct(syn):
    """Window bbox x 2..4, y -0.2..0, padded 0.12 m -> expected px within 1 px."""
    W, H = syn.frame['px']
    b = swiss.answer(syn, 'window')['evidence']['boxes'][0]
    minx, maxy = 0 - 0.6, 10 + 0.6
    want = {'x': (2 - 0.12 - minx) * 40, 'y': (maxy - (0 + 0.12)) * 40,
                'w': (2 + 2 * 0.12) * 40, 'h': (0.2 + 2 * 0.12) * 40}
    got = {'x': b['x'] / 100 * W, 'y': b['y'] / 100 * H, 'w': b['w'] / 100 * W, 'h': b['h'] / 100 * H}
    for k, v in want.items():
        assert abs(got[k] - v) <= 1.0, (k, got[k], v)


def test_room_polygon_and_rect(syn):
    r = syn.room('1.01')
    W, H = syn.frame['px']
    pts = swiss.room_poly_pct(syn, r)
    assert len(pts) == 4 and len({tuple(p) for p in pts}) == 4
    rect = swiss.room_rect(syn, r)
    assert rect['w_m'] == 5.0 and rect['h_m'] == 4.0
    assert abs(rect['x'] / 100 * W - 0.6 * 40) <= 1 and abs(rect['y'] / 100 * H - (10.6 - 4) * 40) <= 1
    assert swiss.room_rect(syn, syn.room('1.03')) is None  # outdoor rooms are not resizable


def test_svg_and_drawing_shape(syn):
    svg = render_svg(syn)
    assert svg.startswith('<svg') and f'viewBox="0 0 {syn.W} {syn.H}"' in svg and '1.01' in svg
    d = build_drawing(syn)
    assert d['contract_version'].startswith('1.') and d['kind'] == 'cad_dxf'
    assert d['images']['rebuilt'] == '/drawings/swiss-1/plan.svg'
    assert d['units'] == [{'id': 'A', 'label': 'Apartment A'}]
    assert 'null' not in json.dumps(d)
    assert {r['room_id'] for r in d['rooms']} == {'1.01', '1.02', '1.03'}


def _in_frame(b):
    return 0 <= b['x'] and 0 <= b['y'] and b['x'] + b['w'] <= 100 and b['y'] + b['h'] <= 100


def test_synthetic_boxes_in_frame(syn):
    for s in swiss.BOX_SUBJECTS:
        assert all(_in_frame(b) for b in syn.boxes()[s])


@pytest.mark.dataset
@needs_data
def test_all_boxes_and_rooms_within_0_100_on_13_drawings():
    if not LIBRARY.exists():
        pytest.skip('run scripts/pick_library.py first')
    lib = json.loads(LIBRARY.read_text(encoding='utf-8'))
    for e in [lib['bundled']] + lib['drawings']:
        m = swiss.load(SWISS / f"{e['stem']}.dxf")
        for s in swiss.BOX_SUBJECTS:
            for b in m.boxes()[s]:
                assert _in_frame(b), (e['id'], b)
        d = build_drawing(m)
        for r in d['rooms']:
            assert all(0 <= x <= 100 and 0 <= y <= 100 for x, y in r['points'] + [r['label_at']]), (e['id'], r['room_id'])


@pytest.mark.dataset
@needs_data
def test_46639_drawing_matches_bundled_frame():
    m = swiss.load(SWISS / 'site11420_bldg17737_floor46639.dxf')
    d = build_drawing(m)
    b = json.loads(BUNDLED_JSON.read_text(encoding='utf-8'))
    assert d['frame']['dxf_min'] == b['frame']['dxf_min'] and d['frame']['dxf_max'] == b['frame']['dxf_max']
    assert d['frame']['px'] == b['frame']['px']
    assert (d['m_per_pct_x'], d['m_per_pct_y']) == (b['m_per_pct_x'], b['m_per_pct_y'])
    assert (d['title'], d['subtitle']) == (b['title'], b['subtitle'])
    mine = {r['room_id']: r for r in d['rooms']}
    for r in b['rooms']:
        g = mine[r['room_id']]
        assert (g['name'], g['area_m2'], g.get('unit'), g['outdoor'], g['label_at'], g['points']) == \
               (r['name'], r['area_m2'], r.get('unit'), r['outdoor'], r['label_at'], r['points']), r['room_id']
