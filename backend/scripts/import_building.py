"""LOCAL: import every storey of an IFC building into the served library, so the floors appear as one building.

The backend groups library drawings named `ifc-<model>--<storey>` that share a model into one project
(`_synthesize_projects` in aura/main.py); the frontend then shows the floor switcher, and the 3D view opens
each floor. Floors are labelled with the IFC storey name and ordered by storey elevation.

Swiss Dwellings has one floor per building in this dataset, so only IFC models carry several storeys.

Usage (from backend/):
  uv run --extra ifc python -I scripts/import_building.py --data ../../junction-dataset --list
  uv run --extra ifc python -I scripts/import_building.py --data ../../junction-dataset --model bsc-esplanades_1807-ep-ar-v18
0 model calls. Writes build/drawings/ifc-<model>--<storey>/ (git-ignored).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / 'scripts'))

from preprocess import IFC_ROOT, OUT, ifc_credit, ifc_gold_rows, write_json

from aura.render import ifc as rifc
from aura.render import png as rpng
from aura.solvers import ifc

SERVED = OUT / 'drawings'


def slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-') or 'storey'


def question_lines(data: Path, model: str) -> list[tuple[int, dict]]:
    """Dataset questions about this model (its plan PDFs or the model itself), with their line numbers."""
    out = []
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if f'/{IFC_ROOT}/{model}' in line:
                r = json.loads(line)
                if Path(r['path']).name.startswith((f'{model}.', f'{model}_')):
                    out.append((i, r))
    return out


def list_models(data: Path) -> None:
    """IFC models that have dataset questions, most questions first (good candidates to import)."""
    cnt = collections.Counter()
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for line in f:
            if f'/{IFC_ROOT}/' in line:
                name = Path(json.loads(line)['path']).name
                cnt[re.split(r'_plan-|\.ifc$|\.pdf$', name)[0]] += 1
    present = {p.stem for p in (data / 'data' / IFC_ROOT).glob('*.ifc')}
    for model, n in cnt.most_common():
        if model in present:
            print(f'{n:5d} questions  {model}')


def import_model(data: Path, model_name: str, min_walls: int) -> list[dict]:
    path = data / 'data' / IFC_ROOT / f'{model_name}.ifc'
    if not path.is_file():
        raise SystemExit(f'not found: {path}')
    storeys = ifc.load(path, geometry=False).storeys
    # Lowest storey first; storeys without an elevation keep their file order after the others.
    ordered = sorted(enumerate(storeys), key=lambda t: (t[1]['elevation'] is None, t[1]['elevation'] or 0, t[0]))
    qlines = question_lines(data, model_name)
    credit = ifc_credit(data, model_name)
    measured = dt.datetime.now(dt.UTC).isoformat(timespec='seconds')
    used: set[str] = set()
    report = []
    for order, (_, st) in enumerate(ordered):
        name = st['name'] or f'Storey {order + 1}'
        sl = slug(name)
        while sl in used:
            sl += '-b'
        used.add(sl)
        did = f'ifc-{model_name}--{sl}'
        model = ifc.load(path, storey=st['name'], drawing_id=did)
        walls = [g for g in model.footprints.get('wall', []) if g['polygon']]
        if len(walls) < min_walls:
            print(f'  skip {name!r}: {len(walls)} wall footprints')
            continue
        gold = ifc_gold_rows(model, qlines)
        facts = ({'gold_total': len(gold), 'correct': sum(g['match'] for g in gold), 'measured_at': measured,
                  'languages': ['en']} if gold else None)
        d = SERVED / did
        d.mkdir(parents=True, exist_ok=True)
        img = rifc.raster(model)
        rpng.thumbnail(img).save(d / 'thumb.png', optimize=True)
        drawing = rifc.build_drawing(model, facts=facts, thumbnail='thumb.png', credit=credit)
        drawing['floor_label'] = name
        drawing['floor_order'] = order
        ev = {s: ifc.boxes(model, s) or [] for s in ifc.BOX_SUBJECTS}
        drawing['evidence'] = ev
        drawing['gold'] = [{k: v for k, v in g.items() if k not in ('solver_family', 'solver_value', 'match')}
                           for g in gold]
        write_json(d / 'drawing.json', drawing)
        (d / 'plan.svg').write_text(rifc.render_svg(model), encoding='utf-8')
        write_json(d / 'model.json', model.to_json())
        write_json(d / 'gold.json', gold)
        write_json(d / 'evidence.json', ev)
        g = f"{facts['correct']}/{facts['gold_total']}" if facts else 'no dataset questions'
        print(f'  {name!r:28s} rooms {len(drawing["rooms"]):3d}  walls {len(walls):4d}  gold {g}')
        report.append({'id': did, 'label': name, 'order': order})
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='dataset root (has questions.jsonl and data/)')
    ap.add_argument('--model', action='append', default=[], help='IFC model stem; repeat for several buildings')
    ap.add_argument('--list', action='store_true', help='list IFC models that have dataset questions')
    ap.add_argument('--min-walls', type=int, default=3, help='skip storeys with fewer wall footprints (roofs, sites)')
    a = ap.parse_args()
    data = Path(a.data)
    if a.list:
        list_models(data)
        return
    if not a.model:
        raise SystemExit('give --model <stem> (see --list)')
    for m in a.model:
        print(m)
        floors = import_model(data, m, a.min_walls)
        print(f'  -> {len(floors)} floors in build/drawings'
              + ('' if len(floors) >= 2 else ' (fewer than 2: not grouped as a building)'))


if __name__ == '__main__':
    main()
