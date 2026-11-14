"""Pick the 12 library drawings: random.Random(46639) over Swiss drawings with >= 30 gold lines
(excluding the bundled swiss-46639) -> build/drawings/library.json.

Usage (from backend/): uv run python -I scripts/pick_library.py [--data ../data/junction-dataset]
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DATA = BACKEND.parent / 'data' / 'junction-dataset'
BUNDLED = 'site11420_bldg17737_floor46639'


IFC_MIN = 15


def pick_ifc(data: Path, n: int) -> list[dict]:
    """One storey plan per IFC model: plan PDFs with >= IFC_MIN English questions, seeded."""
    cnt, storeys = collections.Counter(), collections.defaultdict(collections.Counter)
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if '/ifc_models/' in r['path'] and r['path'].endswith('.pdf') and r['question'].startswith(('How', 'What')):
                cnt[r['path']] += 1
                m = re.search(r"on '([^']*)'|plan of '([^']*)'", r['question'])
                if m:
                    storeys[r['path']][m.group(1) or m.group(2)] += 1
    by_model = collections.defaultdict(list)
    for p, k in sorted(cnt.items()):
        if k >= IFC_MIN and storeys[p]:
            by_model[re.match(r'(.*?)_plan-', Path(p).name).group(1)].append(p)
    rng = random.Random(46639)

    def entry(model):
        p = rng.choice(sorted(by_model[model]))
        storey = storeys[p].most_common(1)[0][0]
        slug = re.sub(r'[^a-z0-9]+', '-', storey.lower()).strip('-')
        return {'id': f'ifc-{model}--{slug}', 'model': model, 'storey': storey, 'plan': Path(p).name,
                'gold_lines': cnt[p]}

    # First the original seeded draw; a pick whose plan would render < IFC_MIN_WALLS of its walls is replaced by
    # the next model of a seeded order over the models not yet drawn (deterministic, recorded in library.json).
    first = rng.sample(sorted(by_model), n)
    picks = [entry(m) for m in first]
    rest = sorted(set(by_model) - set(first))
    reserve = iter(rng.sample(rest, len(rest)))
    out, rejected = [], []
    for e in picks:
        while True:
            ratio = wall_ratio(data, e)
            e['walls_rendered'] = ratio
            if ratio >= IFC_MIN_WALLS:
                out.append(e)
                break
            rejected.append({'id': e['id'], 'walls_rendered': ratio})
            nxt = next(reserve, None)
            if nxt is None:
                break
            e = entry(nxt)
    return out, rejected


IFC_MIN_WALLS = 0.9


def wall_ratio(data: Path, e: dict) -> float:
    """Share of the storey's IfcWall elements whose footprint IfcOpenShell can build (needs the ifc extra)."""
    sys.path.insert(0, str(BACKEND))
    from aura.solvers import ifc
    m = ifc.load(data / 'data' / 'ifc_models' / f"{e['model']}.ifc", storey=e['storey'], drawing_id=e['id'])
    walls = m.footprints.get('wall', [])
    return round(sum(1 for g in walls if g['polygon']) / len(walls), 3) if walls else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(DEFAULT_DATA))
    ap.add_argument('--n', type=int, default=12)
    ap.add_argument('--min-gold', type=int, default=30)
    ap.add_argument('--n-ifc', type=int, default=4)
    ap.add_argument('--out', default=str(BACKEND / 'build' / 'drawings' / 'library.json'))
    a = ap.parse_args()
    data = Path(a.data)
    lines = collections.Counter()
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for l in f:
            r = json.loads(l)
            if '/swiss_dwellings/' in r['path']:
                lines[Path(r['path']).stem] += 1
    cands = sorted(s for s, n in lines.items() if n >= a.min_gold and s != BUNDLED
                   and (data / 'data' / 'swiss_dwellings' / f'{s}.dxf').exists())
    picks = random.Random(46639).sample(cands, a.n)
    lib = {'seed': 46639, 'rule': f'Swiss drawings with >= {a.min_gold} gold lines, excluding the bundled one',
               'candidates': len(cands), 'bundled': {'id': 'swiss-46639', 'stem': BUNDLED, 'gold_lines': lines[BUNDLED]},
               'drawings': [{'id': 'swiss-' + re.search(r'floor(\d+)', s).group(1), 'stem': s, 'gold_lines': lines[s]}
                         for s in picks]}
    lib['ifc'], lib['ifc_rejected'] = pick_ifc(data, a.n_ifc)
    lib['ifc_rule'] = (f'IFC storey plans with >= {IFC_MIN} English questions (rendered plan PDFs exist only for '
                       f'models whose licence allows it), one per model, random.Random(46639); a pick whose walls '
                       f'render below {IFC_MIN_WALLS:.0%} is replaced by the next model in a seeded order of the '
                       f'models not yet drawn')
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(lib, indent=1) + '\n', encoding='utf-8')
    print(f'{len(cands)} candidates; picked', [d['id'] for d in lib['drawings']])
    print('ifc picked', [(d['model'], d['storey']) for d in lib['ifc']])


if __name__ == '__main__':
    main()
