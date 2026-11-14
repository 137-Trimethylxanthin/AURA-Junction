"""Raster (presenter-only) path: parsing, Pct boxes, estimate labelling. No live calls: a fake client."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aura.solvers import raster

TEXT = json.dumps([
    {'label': 'window', 'box_2d': [500, 100, 520, 200]},
    {'label': 'door', 'box_2d': [100, 300, 180, 380]},
    {'label': 'window', 'box_2d': [80, 600, 100, 700]},
    {'label': 'stairs', 'box_2d': [0, 0, 10, 10]},
    {'label': 'door', 'box_2d': [1, 2]},
])


class FakeClient:
    def __init__(self):
        self.calls = []

    async def generate(self, contents, **kw):
        self.calls.append((contents, kw))
        return SimpleNamespace(text='```json\n' + TEXT + '\n```', model=kw['model'], input_tokens=1100,
                               output_tokens=900, cost_usd=0.0083, latency_ms=5000)


@pytest.fixture
async def result():
    c = FakeClient()
    r = await raster.detect(c, b'\x89PNG fake', bucket='measurement', presenter=True)
    contents, kw = c.calls[0]
    assert kw['purpose'] == 'raster_detect' and kw['model'] == raster.DETECT_MODEL
    assert kw['config'] == {'responseMimeType': 'application/json'}
    parts = contents[0]['parts']
    assert 'inline_data' in parts[0] and parts[1]['text'] == raster.DETECT_PROMPT
    return r


async def test_detect_parses_and_orders(result):
    assert result['counts'] == {'door': 1, 'window': 2}
    w = result['boxes']['window']
    assert [b['id'] for b in w] == ['win-01', 'win-02']
    assert (w[0]['x'], w[0]['y'], w[0]['w'], w[0]['h']) == (60.0, 8.0, 10.0, 2.0)  # top one first
    assert json.loads(json.dumps(result)) == result


async def test_answer_is_an_estimate(result):
    a = raster.answer(result, 'window')
    assert a['answer'] == {'kind': 'count', 'value': 2, 'subject': 'window'}
    assert a['method'] == 'vision' and a['estimate'] and 'gold' not in a
    assert 'estimate' in a['summary'] and len(a['evidence']['boxes']) == 2
    reg = raster.answer(result, 'window', {'kind': 'region', 'rect': {'x': 50, 'y': 0, 'w': 50, 'h': 50}, 'label': 'A'})
    assert reg['value'] == 1
    assert raster.answer(result, 'area')['answer']['reason'] == 'not_supported_yet'
    assert raster.answer(result, 'door', {'kind': 'unit', 'unit': 'A'})['answer']['kind'] == 'abstain'


def test_parse_garbage_and_drawing():
    assert raster.parse_items('not json') == []
    d = raster.build_raster_drawing('up-1', 'Upload', 800, 600, '/uploads/up-1/image.png')
    assert d['kind'] == 'raster' and d['frame']['px'] == [800, 600] and d['capabilities'] == ['door', 'window']
    assert 'null' not in json.dumps(d)
