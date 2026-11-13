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
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DATA = BACKEND.parent / 'data' / 'junction-dataset'
BUNDLED = 'site11420_bldg17737_floor46639'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(DEFAULT_DATA))
    ap.add_argument('--n', type=int, default=12)
    ap.add_argument('--min-gold', type=int, default=30)
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
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(lib, indent=1) + '\n', encoding='utf-8')
    print(f'{len(cands)} candidates; picked', [d['id'] for d in lib['drawings']])


if __name__ == '__main__':
    main()
