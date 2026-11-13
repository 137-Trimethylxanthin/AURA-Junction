"""Swiss solver: gold answers, evidence geometry vs the bundled drawing, evidence counts vs the probe.

Dataset tests need ../data/junction-dataset (run with `-m dataset`); the synthetic tests always run.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import ezdxf
import pytest

from aura.solvers import swiss

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATA = ROOT / 'data' / 'junction-dataset'
SWISS = DATA / 'data' / 'swiss_dwellings'
BUNDLED_DXF = SWISS / 'site11420_bldg17737_floor46639.dxf'
BUNDLED_JSON = ROOT / 'frontend' / 'src' / 'data' / 'drawing.swiss-46639.json'
PROBE = ROOT / '.omc' / 'research' / 'stoa' / 'probes' / 'swiss.py'
LIBRARY = BACKEND / 'build' / 'drawings' / 'library.json'

needs_data = pytest.mark.skipif(not BUNDLED_DXF.exists(), reason='dataset not present')


# ------------------------------------------------------------------------------------------
# synthetic drawing (always runs)
# ------------------------------------------------------------------------------------------

def synthetic_dxf(path: Path) -> Path:
    """Two rooms in one apartment, one terrace, one window, two doors (a double door), a WC and a basin."""
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={'layer': 'WALL'})
    m.add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={'layer': 'ROOM_OUTLINE'})
    m.add_lwpolyline([(5, 0), (10, 0), (10, 4), (5, 4)], close=True, dxfattribs={'layer': 'ROOM_OUTLINE'})
    m.add_lwpolyline([(0, 6), (10, 6), (10, 10), (0, 10)], close=True, dxfattribs={'layer': 'ROOM_OUTLINE'})
    m.add_lwpolyline([(0, 0), (10, 0), (10, 4.5), (0, 4.5)], close=True, dxfattribs={'layer': 'UNIT_OUTLINE'})
    for name, num, area, x, y in (('Zimmer', '1.01', '19.50 m²', 2.5, 2), ('Bad', '1.02', None, 7.5, 2),
                                  ('Terrasse', '1.03', '40.00 m²', 5, 8)):
        m.add_text(name, dxfattribs={'layer': 'ROOM_TEXT', 'insert': (x, y + .5)})
        m.add_text(num, dxfattribs={'layer': 'ROOM_TEXT', 'insert': (x, y)})
        if area:
            m.add_text(area, dxfattribs={'layer': 'ROOM_TEXT', 'insert': (x, y - .5)})
    m.add_text('Whg. A', dxfattribs={'layer': 'ROOM_TEXT', 'insert': (1, 3.5)})
    # window: outer frame + an inner part (the part must not count)
    m.add_lwpolyline([(2, -0.2), (4, -0.2), (4, 0), (2, 0)], close=True, dxfattribs={'layer': 'WINDOW'})
    m.add_lwpolyline([(2.5, -0.15), (3.5, -0.15), (3.5, -0.05), (2.5, -0.05)], close=True, dxfattribs={'layer': 'WINDOW'})
    # double door: two leaves with the same bbox
    for _ in range(2):
        m.add_lwpolyline([(4.95, 1), (5.05, 1), (5.05, 2), (4.95, 2)], close=True, dxfattribs={'layer': 'DOOR'})
    # WC: rect + 48-point ellipse; basin: rect + ellipse + dot
    import math
    ell = lambda cx, cy: [(cx + .15 * math.cos(t * 2 * math.pi / 48), cy + .1 * math.sin(t * 2 * math.pi / 48)) for t in range(48)]
    m.add_lwpolyline([(6, 1), (6.5, 1), (6.5, 1.6), (6, 1.6)], close=True, dxfattribs={'layer': 'FIXTURE'})
    m.add_lwpolyline(ell(6.25, 1.3), close=True, dxfattribs={'layer': 'FIXTURE'})
    m.add_lwpolyline([(8, 1), (8.6, 1), (8.6, 1.5), (8, 1.5)], close=True, dxfattribs={'layer': 'FIXTURE'})
    m.add_lwpolyline(ell(8.3, 1.25), close=True, dxfattribs={'layer': 'FIXTURE'})
    m.add_circle((8.3, 1.4), 0.02, dxfattribs={'layer': 'FIXTURE'})
    p = path / 'site1_bldg1_floor1.dxf'
    doc.saveas(p)
    return p


@pytest.fixture(scope='module')
def syn(tmp_path_factory):
    return swiss.load(synthetic_dxf(tmp_path_factory.mktemp('dxf')))


def test_synthetic_counts(syn):
    assert syn.id == 'swiss-1'
    v = {f: swiss.answer(syn, f)['value'] for f in swiss.BOX_SUBJECTS + ('apartment',)}
    assert v == {'window': 1, 'door': 2, 'washbasin': 1, 'wc': 1, 'bathtub': 0, 'shower': 0, 'apartment': 1}
    doors = swiss.answer(syn, 'door')['evidence']['boxes']
    assert [b['id'] for b in doors] == ['door-01', 'door-02'] and all(b.get('note') for b in doors)


def test_synthetic_areas_and_scopes(syn):
    a = swiss.answer(syn, 'area')  # floor: indoor only; 1.02 has no printed area -> outline area 20.0
    assert a['answer'] == {'kind': 'area', 'value': 39.5, 'unit': 'm2'}
    assert {r['room_id']: r['included'] for r in a['evidence']['rooms']} == {'1.01': True, '1.02': True, '1.03': False}
    assert swiss.answer(syn, 'area', {'kind': 'unit', 'unit': 'A'})['value'] == 39.5
    assert swiss.answer(syn, 'area', {'kind': 'label', 'label': 'Terrasse'})['value'] == 40.0
    assert swiss.answer(syn, 'area', {'kind': 'unit', 'unit': 'Z'})['answer']['kind'] == 'abstain'
    assert swiss.answer(syn, 'room', {'kind': 'label', 'label': 'Bad'})['value'] == 1
    w = swiss.answer(syn, 'area', {'kind': 'unit', 'unit': 'A'}, [{'room_id': '1.01', 'w_m': 4, 'h_m': 4}])
    assert w['value'] == 36.0 and any(r.get('edited') for r in w['evidence']['rooms'])
    assert w['result_id'] != swiss.answer(syn, 'area', {'kind': 'unit', 'unit': 'A'})['result_id']
    # region: the left half holds the window and the double door (box centres), and room 1.01's label
    reg = {'kind': 'region', 'rect': {'x': 0, 'y': 0, 'w': 50, 'h': 100}, 'label': 'Area 1'}
    assert swiss.answer(syn, 'window', reg)['value'] == 1
    assert swiss.answer(syn, 'wc', reg)['value'] == 0


def test_model_json_roundtrip(syn):
    m2 = swiss.SwissModel.from_json(json.loads(json.dumps(syn.to_json())))
    for f in swiss.FAMILIES:
        assert swiss.answer(m2, f) == swiss.answer(syn, f)


def test_find_text(syn):
    r = swiss.find_text(syn, r'Whg\.')
    b = r['evidence']['boxes'][0]
    assert r['answer']['kind'] == 'text' and b['id'].startswith('text-') and b['note'] == 'Whg. A'
    assert all(0 <= b['x'] and b['x'] + b['w'] <= 100 for b in swiss.text_boxes(syn, syn.texts))
    assert swiss.find_text(syn, r'EI\d+')['answer']['kind'] == 'abstain'


# ------------------------------------------------------------------------------------------
# dataset: swiss-46639 gold (38 lines: 19 EN + their FR twins)
# ------------------------------------------------------------------------------------------

def _route(q):
    from scripts.preprocess import route  # same routing the preprocess and eval use
    return route(q)


def _qlines(stem):
    out = []
    with open(DATA / 'questions.jsonl', encoding='utf-8') as f:
        for i, l in enumerate(f, 1):
            if stem in l:
                r = json.loads(l)
                if Path(r['path']).stem == stem:
                    out.append((i, r))
    return out


def _ok(pred, r):
    return pred is not None and (pred == r['answer'] if r['type'] == 'count' else abs(pred - r['answer']) <= 0.02 * abs(r['answer']))


@pytest.fixture(scope='module')
def m46639():
    return swiss.load(BUNDLED_DXF)


@pytest.mark.dataset
@needs_data
def test_46639_all_38_gold_lines(m46639):
    lines = _qlines(BUNDLED_DXF.stem)
    assert len(lines) == 38
    checked = 0
    for k in range(0, 38, 2):
        (le, en), (lf, fr) = lines[k], lines[k + 1]
        assert en['question'].startswith(('How', 'What')) and fr['answer'] == en['answer']
        fam, scope, _, _ = _route(en['question'])
        pred = swiss.answer(m46639, fam, scope)['value']
        assert _ok(pred, en), (le, en['question'], en['answer'], pred)
        assert _ok(pred, fr), (lf, fr['question'], fr['answer'], pred)
        checked += 2
    assert checked == 38


@pytest.mark.dataset
@needs_data
def test_46639_key_values(m46639):
    v = lambda f, s=None: swiss.answer(m46639, f, s)['value']
    assert (v('window'), v('door'), v('wc'), v('washbasin'), v('bathtub'), v('shower'), v('apartment')) == (12, 22, 5, 5, 2, 2, 2)
    assert v('area', {'kind': 'room', 'room_id': '3.04'}) == 17.04
    assert v('area', {'kind': 'unit', 'unit': 'A'}) == 139.34
    assert abs(v('area', {'kind': 'unit', 'unit': 'B'}) - 90.54) <= 0.02 * 90.54
    # The probe's floor rule sums 20 indoor rooms incl. 3.23/3.24 (no printed area, outline area) = 261.96;
    # gold is 258.0 and the dataset tolerance is 2 %.
    assert abs(v('area') - 258.0) <= 0.02 * 258.0
    assert v('room', {'kind': 'label', 'label': 'Chambre à coucher'}) == 6
    assert v('area', {'kind': 'label', 'label': 'Chambre à coucher'}) == 88.09


@pytest.mark.dataset
@needs_data
def test_46639_evidence_equals_bundled(m46639):
    bundled = json.loads(BUNDLED_JSON.read_text(encoding='utf-8'))
    for subj in ('window', 'door', 'washbasin'):
        assert m46639.boxes()[subj] == bundled['evidence'][subj], subj


@pytest.mark.dataset
@needs_data
def test_46639_rects_match_bundled(m46639):
    bundled = {r['room_id']: r for r in json.loads(BUNDLED_JSON.read_text(encoding='utf-8'))['rooms']}
    want = {k: r['rect'] for k, r in bundled.items() if 'rect' in r}
    got = {r['room_id']: swiss.room_rect(m46639, r) for r in m46639.rooms if r['room_id'] in bundled}
    got = {k: v for k, v in got.items() if v}
    assert set(got) == set(want)
    for k, w in want.items():
        g = got[k]
        assert (g['x'], g['y'], g['w'], g['h']) == (w['x'], w['y'], w['w'], w['h']), k
        # the mockup's metre sizes were rounded by hand; geometry agrees within 3 mm
        assert abs(g['w_m'] - w['w_m']) <= 0.003 and abs(g['h_m'] - w['h_m']) <= 0.003, (k, g, w)


# ------------------------------------------------------------------------------------------
# dataset: library gold and evidence counts vs the probe
# ------------------------------------------------------------------------------------------

def _library():
    if not LIBRARY.exists():
        pytest.skip('run scripts/pick_library.py first')
    lib = json.loads(LIBRARY.read_text(encoding='utf-8'))
    return [lib['bundled']] + lib['drawings']


@pytest.mark.dataset
@needs_data
def test_library_gold_at_least_97pct():
    ok = n = 0
    for e in _library()[1:]:
        m = swiss.load(SWISS / f"{e['stem']}.dxf")
        for _, r in _qlines(e['stem']):
            rt = _route(r['question']) if r['question'].startswith(('How', 'What')) else None
            if rt is None:
                continue
            ok += _ok(swiss.answer(m, rt[0], rt[1])['value'], r); n += 1
    assert n >= 12 * 15
    assert ok / n >= 0.97, (ok, n)


def _probe_load():
    """The probe's own load(), extracted from its source (the probe runs at import)."""
    if not PROBE.exists():
        pytest.skip('probe source not present')
    tree = ast.parse(PROBE.read_text(encoding='utf-8'))
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
            or (isinstance(n, ast.FunctionDef) and n.name == 'load')
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'OUT' for t in n.targets))]
    ns: dict = {}
    # trusted local source: the measured probe whose counts this test compares against
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(PROBE), 'exec'), ns)  # noqa: S102
    return ns['load']


@pytest.mark.dataset
@needs_data
def test_evidence_counts_equal_probe_on_13_drawings():
    probe_load = _probe_load()
    for e in _library():
        p = SWISS / f"{e['stem']}.dxf"
        d = probe_load(str(p))
        m = swiss.load(p)
        want = {'window': d['windows'], 'door': d['doors'], 'washbasin': d['fix']['washbasin'], 'wc': d['fix']['wc'],
                    'bathtub': d['fix']['bathtub'], 'shower': d['fix']['shower']}
        got = {s: len(m.boxes()[s]) for s in swiss.BOX_SUBJECTS}
        assert got == want, (e['id'], got, want)
        assert swiss.answer(m, 'apartment')['value'] == (len(d['applabel']) or d['units'])
        assert {r['room_id'] for r in m.rooms} == set(d['rinfo'])
        ids = [b['id'] for s in swiss.BOX_SUBJECTS for b in m.boxes()[s]]
        assert len(ids) == len(set(ids))


@pytest.mark.dataset
@needs_data
def test_served_model_json_answers_equal_fresh_load():
    """The server loads build/drawings/<id>/model.json; its answers must equal a fresh DXF load."""
    for e in _library():
        mj = BACKEND / 'build' / 'drawings' / e['id'] / 'model.json'
        if not mj.exists():
            pytest.skip('run scripts/preprocess.py first')
        served = swiss.SwissModel.from_json(json.loads(mj.read_text(encoding='utf-8')))
        fresh = swiss.load(SWISS / f"{e['stem']}.dxf")
        scopes = [{'kind': 'floor'}] + [{'kind': 'unit', 'unit': u} for u in sorted(fresh.applabel)]
        for f in swiss.FAMILIES:
            for sc in scopes:
                assert swiss.answer(served, f, sc) == swiss.answer(fresh, f, sc), (e['id'], f, sc)
