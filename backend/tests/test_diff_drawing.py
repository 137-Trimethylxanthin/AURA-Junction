"""diff_drawing.py guards the regeneration of the bundled frontend drawing."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.diff_drawing import diff, merge

ROOT = Path(__file__).resolve().parents[2]
CUR = ROOT / 'frontend' / 'src' / 'data' / 'drawing.swiss-46639.json'
NEW = ROOT / 'backend' / 'build' / 'drawings' / 'swiss-46639' / 'drawing.json'


@pytest.fixture(scope='module')
def cur():
    return json.loads(CUR.read_text(encoding='utf-8'))


def test_identical_passes(cur):
    assert diff(copy.deepcopy(cur), cur)[0] == []


def test_swapped_evidence_order_fails(cur):
    new = copy.deepcopy(cur)
    w = new['evidence']['window']
    w[0], w[1] = w[1], w[0]
    assert any('ids/order' in f for f in diff(new, cur)[0])


def test_moved_box_and_renamed_room_fail(cur):
    new = copy.deepcopy(cur)
    new['evidence']['door'][3]['x'] += 0.5
    new['rooms'][0]['name'] = 'X'
    fails = diff(new, cur)[0]
    assert any('door-04.x' in f for f in fails) and any('name' in f for f in fails)


def test_disallowed_answer_change_fails(cur):
    new = copy.deepcopy(cur)
    new['evidence']['washbasin'].pop()
    assert any('answer changed' in f for f in diff(new, cur)[0])


def test_rect_metres_small_diff_is_a_note(cur):
    new = copy.deepcopy(cur)
    r = next(r for r in new['rooms'] if 'rect' in r)
    r['rect']['w_m'] += 0.003
    fail, note = diff(new, cur)
    assert fail == [] and any('rect.w_m' in n for n in note)
    r['rect']['w_m'] += 0.01
    assert any('> 5 mm' in f for f in diff(new, cur)[0])


@pytest.mark.dataset
def test_backend_build_passes_and_merge_keeps_current_rects(cur):
    if not NEW.exists():
        pytest.skip('run scripts/preprocess.py first')
    new = json.loads(NEW.read_text(encoding='utf-8'))
    fail, _ = diff(new, cur)
    assert fail == []
    m = merge(new, cur)
    assert diff(m, cur)[0] == [] and m['images'] == cur['images'] and 'thumbnail' not in m
    assert sum(1 for r in m['rooms'] if 'rect' in r) == 14
