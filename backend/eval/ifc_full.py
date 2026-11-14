"""Production IFC solver over every English ifc_models question -> eval/results/ifc.json. 0 model calls.

Usage (from backend/): uv run python -I eval/ifc_full.py [--data ../data/junction-dataset] [--jobs 8]
Routing mirrors the probe (.omc/research/stoa/probes/ifcm.py); the answers come from aura.solvers.ifc.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from aura.solvers import ifc

DEFAULT_DATA = BACKEND.parent / 'data' / 'junction-dataset'
PROBE_RULES = '--probe-rules' in sys.argv  # module-level so worker processes see it


def model_stem(path: str) -> str:
    return re.match(r'(.*?)(_plan-.*)?\.(ifc|pdf|svg)$', os.path.basename(path)).group(1)


def cls_family(q: str) -> str | None:
    for k in ifc.CLS:
        if re.search(k, q, re.IGNORECASE):
            return k
    return None


def route(q: str, storeys: set[str]):
    """(eval family, solver family, scope) or None, as the probe routes the question."""
    sm = re.search(r"on '([^']*)'|plan of '([^']*)'", q)
    sname = (sm.group(1) or sm.group(2)) if sm else None
    has_st = sname in storeys
    if q.startswith('How many') and 'in the model' in q:
        return 'model_count', cls_family(q), {'kind': 'model'}
    if q.startswith('How many') and has_st:
        c = cls_family(q)
        return f'storey_count_{ifc.CLS[c]}', c, {'kind': 'floor', 'storey': sname}
    if q.startswith('What is the net floor area of the room'):
        rn = re.search(r"room '(.*)' on", q).group(1)
        return 'room_area', 'area', {'kind': 'room', 'room': rn, 'storey': sname}
    if 'total net floor area of all rooms' in q:
        return 'storey_area', 'area', {'kind': 'floor', 'storey': sname}
    for key, fam, sf in (('total length of all walls', 'wall_len', 'wall_length'),
                         ('net side area of all walls', 'wall_side', 'wall_side_area'),
                         ('net volume of all walls', 'wall_vol', 'wall_volume'),
                         ('net volume of all slabs', 'slab_vol', 'slab_volume')):
        if key in q:
            return fam, sf, {'kind': 'floor', 'storey': sname}
    return None


def score_model(args):
    p, rows = args
    t = time.time()
    if not Path(p).exists():
        return [{'line': i, 'file': os.path.basename(p), 'family': 'no_ifc', 'ok': False, 'gold': r['answer'], 'pred': None,
                     'q': r['question'][:90]} for i, r in rows], 0.0
    m = ifc.load(p, geometry=False, probe_rules=PROBE_RULES)
    storeys = {s['name'] for s in m.storeys}
    out = []
    for i, r in rows:
        rt = route(r['question'], storeys)
        fam, pred = '?', None
        if rt:
            fam, sf, scope = rt
            if sf is not None and not (PROBE_RULES and sf == 'light'):
                pred = ifc.answer(m, sf, scope)['value']
        if fam == 'room_area' and pred is None:
            st = rt[2]['storey']
            n = sum(1 for x in m.rooms if x['storey'] == st and rt[2]['room'] in
                    (f"{x['name']} ({x['num']})", x['num'], x['name']))
            fam += f'_match{min(n, 2)}' if n != 1 else ''
        ans = r['answer']
        ok = pred is not None and (pred == ans if r['type'] == 'count' else abs(pred - ans) <= 0.02 * abs(ans))
        out.append({'line': i, 'file': os.path.basename(p), 'family': fam, 'ok': bool(ok), 'gold': ans,
                        'pred': None if pred is None else round(pred, 3), 'q': r['question'][:90]})
    return out, time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(DEFAULT_DATA))
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--out', default=str(BACKEND / 'eval' / 'results' / 'ifc.json'))
    ap.add_argument('--probe-rules', action='store_true', help='the probe rules only (no result file written)')
    a = ap.parse_args()
    data = Path(a.data)
    by_model = collections.defaultdict(list)
    langs = collections.Counter()
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            r = json.loads(line)
            if '/ifc_models/' not in r['path']:
                continue
            en = r['question'].startswith(('What', 'How'))
            langs['en' if en else 'other'] += 1
            if en:
                by_model[str(data / 'data' / 'ifc_models' / f"{model_stem(r['path'])}.ifc")].append((i, r))
    t0 = time.time()
    rows, load_s = [], {}
    with ProcessPoolExecutor(a.jobs) as ex:
        for (p, _), (out, secs) in zip(sorted(by_model.items()),
                                       ex.map(score_model, sorted(by_model.items())), strict=True):
            rows.extend(out); load_s[os.path.basename(p)] = round(secs, 1)
    secs = time.time() - t0
    fam = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        fam[r['family']][0] += r['ok']; fam[r['family']][1] += 1
    correct = sum(r['ok'] for r in rows)
    res = {'dataset': 'ifc_models', 'solver': 'aura.solvers.ifc', 'calls': 0, 'cost_usd': 0, 'files': len(by_model),
               'questions': len(rows), 'correct': correct, 'pct': round(100 * correct / len(rows), 2), 'languages': ['en'],
               'coverage': (f"English (How/What) ifc_models questions only; the dataset also has {langs['other']} "
                         'Estonian/Danish lines (not all are exact twins), not scored here'),
               'probe_reference': {'correct': 1691, 'questions': 1699, 'pct': 99.53},
               'probe_rules_reproduced': {'correct': 1691, 'questions': 1699, 'measured_at': '2026-11-14',
                                           'note': 'uv run python -I eval/ifc_full.py --probe-rules (this solver without the two additions)'},
               'rule_additions_after_seeing_misses': [
                   ('light fixtures: IfcLightFixture (IFC4+) or IfcFlowTerminal typed IfcLightFixtureType (IFC2X3); '
                   'fixed the 5 whole-model light-fixture misses'),
                   ('space area fallback: when IfcOpenShell footprint area is 0, use the area of the projected '
                   'triangle outline; fixed 2 duplex hallway areas and 1 storey total')],
               'caveat': ('The two rule additions were written after looking at the 8 probe misses on this same set, '
                       'so 100 % is not a held-out estimate; the probe-rule result is 1,691/1,699.'), 'runtime_s': round(secs, 1),
               'jobs': a.jobs, 'per_model_s': load_s,
               'measured_at': dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
               'families': {k: {'correct': v[0], 'n': v[1]} for k, v in sorted(fam.items())},
               'misses': [{k: v for k, v in r.items() if k != 'ok'} for r in rows if not r['ok']]}
    if not a.probe_rules:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f"files {res['files']} questions {res['questions']} correct {correct} ({res['pct']} %) runtime {secs:.1f}s")
    for k, v in sorted(fam.items()):
        print(f'  {k:28s} {v[0]} / {v[1]}')
    for r in res['misses']:
        print('  MISS', r['family'], r['gold'], r['pred'], r['q'])


if __name__ == '__main__':
    main()
