"""LOCAL preprocessing: dataset -> build/drawings/<id>/{drawing.json, plan.svg, original.png, thumb.png,
model.json, gold.json} for swiss-46639 plus the library picks, and numbered overlays in
build/overlays/<id>.png. 0 model calls.

Usage (from backend/): uv run python -I scripts/preprocess.py [--data ../data/junction-dataset] [--ids swiss-46639 ...]
Run scripts/pick_library.py first (it writes build/drawings/library.json).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from aura.render import ifc as rifc
from aura.render import png as rpng
from aura.render.drawing import build_drawing
from aura.render.svg import render_svg
from aura.solvers import ifc, swiss

DEFAULT_DATA = BACKEND.parent / 'data' / 'junction-dataset'
OUT = BACKEND / 'build'

COUNT_FAMS = (('How many apartments', 'apartment'), ('How many doors', 'door'), ('How many windows', 'window'),
              ('How many toilets', 'wc'), ('How many washbasins', 'washbasin'), ('How many bathtubs', 'bathtub'),
              ('How many showers', 'shower'))
PLURAL = {'apartment': 'apartments', 'door': 'doors', 'window': 'windows', 'wc': 'WCs', 'washbasin': 'washbasins',
          'bathtub': 'bathtubs', 'shower': 'showers'}


def route(q: str):
    """English dataset question -> (solver family, scope, answer kind, our paraphrase). Mirrors eval/swiss_full.py."""
    for prefix, subj in COUNT_FAMS:
        if q.startswith(prefix):
            verb = 'are on' if subj == 'apartment' else 'are drawn on'
            return subj, {'kind': 'floor'}, {'kind': 'count', 'subject': subj}, f'How many {PLURAL[subj]} {verb} this floor plan'
    if q.startswith('How many rooms labelled'):
        lab = re.search(r"'(.*)'", q).group(1)
        return 'room', {'kind': 'label', 'label': lab}, {'kind': 'count', 'subject': 'room'}, f'How many rooms are labelled {lab}'
    if q.startswith('What is the net floor area of room'):
        rid = re.search(r'room (\S+)', q).group(1)
        return 'area', {'kind': 'room', 'room_id': rid}, {'kind': 'area'}, f'What is the net floor area of room {rid}'
    if q.startswith('What is the total net area in m² of all rooms labelled'):
        lab = re.search(r"'(.*)'", q).group(1)
        return 'area', {'kind': 'label', 'label': lab}, {'kind': 'area'}, f'What is the total area of rooms labelled {lab}'
    if q.startswith('What is the net floor area of apartment'):
        u = re.search(r'apartment (\S+) in', q).group(1)
        return 'area', {'kind': 'unit', 'unit': u}, {'kind': 'area'}, f'What is the net floor area of apartment {u}'
    if q.startswith('What is the total net floor area'):
        return 'area', {'kind': 'floor'}, {'kind': 'area'}, 'What is the net floor area of all numbered indoor rooms'
    return None


def question_lang(q: str) -> str | None:
    """Language of a dataset question line from its first word (the Swiss set uses en/de/fr/it)."""
    w = q.split()[0] if q.split() else ''
    return {'How': 'en', 'What': 'en', 'Wie': 'de', 'Combien': 'fr', 'Quelle': 'fr',
            'Quanti': 'it', 'Quante': 'it', 'Qual': 'it'}.get(w)


def gold_rows(model, qlines):
    """Gold rows in the bundled format (our paraphrase, never the dataset text), with our solver's value."""
    rows = []
    for i, (line, r) in enumerate(qlines):
        if not r['question'].startswith(('How', 'What')):
            continue
        rt = route(r['question'])
        if rt is None:
            continue
        fam, scope, kind, para = rt
        res = swiss.answer(model, fam, scope)
        pred = res['value']
        tol = 'exact' if r['type'] == 'count' else '2pct'
        ok = pred is not None and (pred == r['answer'] if tol == 'exact' else abs(pred - r['answer']) <= 0.02 * abs(r['answer']))
        row = {'dataset_qid': f'questions.jsonl:{line}'}
        nxt = qlines[i + 1] if i + 1 < len(qlines) else None
        if nxt and nxt[0] == line + 1 and not nxt[1]['question'].startswith(('How', 'What')):
            lang = question_lang(nxt[1]['question'])
            if lang:
                row[f'dataset_qid_{lang}'] = f'questions.jsonl:{nxt[0]}'
        row.update(question_en=para, answer=kind, scope=scope, value=r['answer'], tolerance=tol,
                   solver_family=fam, solver_value=pred, match=bool(ok))
        rows.append(row)
    return rows


def write_json(p: Path, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def process(stem: str, data: Path, qlines, overlays: bool = True) -> dict:
    sd = data / 'data' / 'swiss_dwellings'
    model = swiss.load(sd / f'{stem}.dxf')
    d = OUT / 'drawings' / model.id
    d.mkdir(parents=True, exist_ok=True)
    gold = gold_rows(model, qlines)
    measured = dt.datetime.now(dt.UTC).isoformat(timespec='seconds')
    facts = {'gold_total': len(gold), 'correct': sum(g['match'] for g in gold), 'measured_at': measured,
                 'languages': ['en']}  # one row per English dataset line; the de/fr/it twin asks the same fact

    al = rpng.align(sd / f'{stem}.png', model)
    orig = rpng.original_crop(sd / f'{stem}.png', model, al)
    orig.save(d / 'original.png', optimize=True)
    rpng.thumbnail(orig).save(d / 'thumb.png', optimize=True)
    boxes = [b for s in swiss.BOX_SUBJECTS for b in model.boxes()[s]]
    drawing = build_drawing(model, facts=facts)
    # Extra sections (clients ignore them), as in frontend/src/data/drawing.swiss-46639.json, for tools.py.
    drawing['evidence'] = {s: model.boxes()[s] for s in swiss.BOX_SUBJECTS}
    drawing['texts'] = [{'id': b['id'], 'text': b['note'], 'x': b['x'], 'y': b['y'], 'w': b['w'], 'h': b['h']}
                        for b in swiss.text_boxes(model, model.texts)]
    drawing['gold'] = [{k: v for k, v in g.items() if k not in ('solver_family', 'solver_value', 'match')}
                       for g in gold]
    write_json(d / 'drawing.json', drawing)
    (d / 'plan.svg').write_text(render_svg(model), encoding='utf-8')
    write_json(d / 'model.json', model.to_json())
    write_json(d / 'gold.json', gold)
    write_json(d / 'evidence.json', {s: model.boxes()[s] for s in swiss.BOX_SUBJECTS})
    if overlays:
        (OUT / 'overlays').mkdir(parents=True, exist_ok=True)
        rpng.overlay(orig, boxes, drawing['rooms']).save(OUT / 'overlays' / f'{model.id}.png', optimize=True)
    counts = {s: len(model.boxes()[s]) for s in swiss.BOX_SUBJECTS}
    return {'id': model.id, 'stem': stem, 'gold': facts, 'counts': counts, 'rooms': len(model.rooms),
                'align': {'s': round(al['s'], 3), 'sx': round(al['sx'], 3), 'sy': round(al['sy'], 3),
                           'printed': None if al['printed_s'] is None else round(al['printed_s'], 3)}}


IFC_ROOT = 'ifc_models'
# New drawing kinds go to staging until L2's loader supports them; L2 moves them into build/drawings.
IFC_STAGING = 'ifc_drawings'


def ifc_gold_rows(model, qlines):
    """Gold rows for an IFC storey plan: our paraphrase, the solver's value, contract-style scope."""
    sys.path.insert(0, str(BACKEND / 'eval'))
    from ifc_full import route as ifc_route
    storeys = {s['name'] for s in model.storeys}
    rows = []
    for i, (line, r) in enumerate(qlines):
        if not r['question'].startswith(('How', 'What')):
            continue
        rt = ifc_route(r['question'], storeys)
        if rt is None or rt[1] is None:
            continue
        _, fam, scope = rt
        if scope.get('storey') not in (None, model.storey):
            continue
        res = ifc.answer(model, fam, scope)
        pred = res['value']
        tol = 'exact' if r['type'] == 'count' else '2pct'
        ok = pred is not None and (pred == r['answer'] if tol == 'exact' else abs(pred - r['answer']) <= 0.02 * abs(r['answer']))
        if fam == 'area' and scope['kind'] == 'room':
            room = ifc.find_room(model, scope['room'])
            cscope = {'kind': 'room', 'room_id': room['room_id']} if room else {'kind': 'room', 'room': scope['room']}
            para = f"What is the net floor area of room {scope['room']}"
        elif scope['kind'] == 'model':
            cscope, para = {'kind': 'model'}, f'How many {ifc.PLURAL[fam]} are in the whole model'
        elif fam in ifc.COUNT_FAMILIES:
            cscope, para = {'kind': 'floor'}, f'How many {ifc.PLURAL[fam]} are on this storey'
        else:
            cscope = {'kind': 'floor'}
            para = {'area': 'What is the net floor area of all rooms on this storey'}.get(
                fam, f"What is the total {fam.replace('_', ' ')} on this storey")
        kind = res['answer']['kind'] if res['answer']['kind'] != 'abstain' else ('count' if tol == 'exact' else 'area')
        ans = {'kind': kind}
        if kind == 'count':
            ans['subject'] = fam
        if kind == 'quantity':
            ans['unit'] = ifc.TOTAL_UNIT[fam]
        row = {'dataset_qid': f'questions.jsonl:{line}'}
        nxt = qlines[i + 1] if i + 1 < len(qlines) else None
        if nxt and nxt[0] == line + 1 and not nxt[1]['question'].startswith(('How', 'What')):
            row['dataset_qid_local'] = f'questions.jsonl:{nxt[0]}'
        row.update(question_en=para, answer=ans, scope=cscope, value=r['answer'], tolerance=tol,
                   solver_family=fam, solver_value=pred, match=bool(ok))
        rows.append(row)
    return rows


def ifc_credit(data: Path, model: str) -> dict:
    """The model's row of data/ifc_models/LICENSE.md: | file | title | licence | credit | source |."""
    for line in (data / 'data' / IFC_ROOT / 'LICENSE.md').read_text(encoding='utf-8').splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) >= 5 and cells[0] == f'{model}.ifc':
            names = {'LicenseRef-KIT-unrestricted': 'KIT IFC Examples: for unrestricted use', 'CC-BY-4.0': 'CC BY 4.0',
                     'MIT': 'MIT License'}
            return {'text': f'{cells[1]}: {cells[3]}. Plan rendered by AURA from the IFC footprints.',
                    'license': cells[2], 'license_name': names.get(cells[2], cells[2]), 'url': cells[4]}
    raise SystemExit(f'no licence row for {model}.ifc')


def attach_original(drawing: dict, model, data: Path, model_name: str, out_dir: Path,
                    maps: dict | None = None) -> str:
    """Write original.png from the dataset's own plan sheet of this storey. Aligned from this storey's matched
    elements; failing that, with the mapping of an exactly aligned sibling storey (`maps`, per model)."""
    from aura.render.ifc_original import find_sheet, render_original

    sheet = find_sheet(data / 'data' / IFC_ROOT, model_name, model.storey or '')
    if sheet is None:
        return 'no dataset sheet'
    try:
        img, fit = render_original(model, *sheet, sibling_map=(maps or {}).get(model_name))
    except ValueError as e:
        return f'not aligned ({e})'
    img.save(out_dir / 'original.png', optimize=True)
    drawing['images']['original'] = f"{drawing['images']['rebuilt'].rsplit('/', 1)[0]}/original.png"
    exact = fit['method'] == 'fit' and fit['residual_px'] <= 2
    drawing['images']['original_alignment'] = 'exact' if exact else 'approximate'
    if exact and maps is not None:
        maps.setdefault(model_name, fit['model_map'])
    how = (f"{fit['matched']} elements, worst {fit['residual_px']} px" if fit['method'] == 'fit'
           else f"mapping of a sibling storey, checked on {fit['matched']} elements")
    return f"original from {sheet[0].name} ({how})"


def process_ifc(entry: dict, data: Path, qlines) -> dict:
    model = ifc.load(data / 'data' / IFC_ROOT / f"{entry['model']}.ifc", storey=entry['storey'],
                     drawing_id=entry['id'])
    d = OUT / IFC_STAGING / model.id  # staging: build/drawings is the served library (lead's rule)
    d.mkdir(parents=True, exist_ok=True)
    gold = ifc_gold_rows(model, qlines)
    measured = dt.datetime.now(dt.UTC).isoformat(timespec='seconds')
    facts = {'gold_total': len(gold), 'correct': sum(g['match'] for g in gold), 'measured_at': measured,
                 'languages': ['en']}
    img = rifc.raster(model)
    rpng.thumbnail(img).save(d / 'thumb.png', optimize=True)
    drawing = rifc.build_drawing(model, facts=facts, thumbnail='thumb.png', credit=ifc_credit(data, entry['model']))
    ev = {s: ifc.boxes(model, s) or [] for s in ifc.BOX_SUBJECTS}
    drawing['evidence'] = ev
    drawing['gold'] = [{k: v for k, v in g.items() if k not in ('solver_family', 'solver_value', 'match')} for g in gold]
    print(f"  {model.id}: {attach_original(drawing, model, data, entry['model'], d)}")
    write_json(d / 'drawing.json', drawing)
    (d / 'plan.svg').write_text(rifc.render_svg(model), encoding='utf-8')
    write_json(d / 'model.json', model.to_json())
    write_json(d / 'gold.json', gold)
    write_json(d / 'evidence.json', ev)
    (OUT / 'overlays').mkdir(parents=True, exist_ok=True)
    rpng.overlay(img, ev['window'] + ev['door'], drawing['rooms']).save(OUT / 'overlays' / f'{model.id}.png', optimize=True)
    return {'id': model.id, 'gold': facts, 'counts': {s: len(v) for s, v in ev.items()}, 'rooms': len(drawing['rooms']),
                'frame': model.frame['px']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(DEFAULT_DATA))
    ap.add_argument('--ids', nargs='*', help='drawing ids (default: swiss-46639 + library.json)')
    a = ap.parse_args()
    data = Path(a.data)
    lib = json.loads((OUT / 'drawings' / 'library.json').read_text(encoding='utf-8'))
    entries = [lib['bundled']] + lib['drawings']
    if a.ids:
        entries = [e for e in entries if e['id'] in a.ids]
    stems = {e['stem'] for e in entries}
    qby = {s: [] for s in stems}
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for i, l in enumerate(f, 1):
            r = json.loads(l)
            s = Path(r['path']).stem
            if s in qby and '/swiss_dwellings/' in r['path']:
                qby[s].append((i, r))
    summary = []
    for e in entries:
        rep = process(e['stem'], data, qby[e['stem']])
        summary.append(rep)
        g = rep['gold']
        print(f"{rep['id']:12s} gold {g['correct']}/{g['gold_total']} rooms {rep['rooms']} {rep['counts']} align {rep['align']}")
    if not a.ids or any(i.startswith('ifc-') for i in a.ids):
        ifc_entries = [e for e in lib.get('ifc', []) if not a.ids or e['id'] in a.ids]
        plans = {e['plan']: e for e in ifc_entries}
        iq = {e['id']: [] for e in ifc_entries}
        with open(data / 'questions.jsonl', encoding='utf-8') as f:
            for i, l in enumerate(f, 1):
                if '/ifc_models/' in l:
                    r = json.loads(l)
                    e = plans.get(Path(r['path']).name)
                    if e:
                        iq[e['id']].append((i, r))
        for e in ifc_entries:
            rep = process_ifc(e, data, iq[e['id']])
            summary.append(rep)
            g = rep['gold']
            print(f"{rep['id']:60s} gold {g['correct']}/{g['gold_total']} rooms {rep['rooms']} {rep['counts']} px {rep['frame']}")
    tot = sum(r['gold']['correct'] for r in summary), sum(r['gold']['gold_total'] for r in summary)
    print(f'TOTAL gold {tot[0]}/{tot[1]} = {100 * tot[0] / tot[1]:.2f} %')
    # index.json lists every built drawing (bundled, Swiss library, IFC library); a partial run updates its rows
    for sub, entries_ in (('drawings', [lib['bundled']] + lib['drawings']), (IFC_STAGING, lib.get('ifc', []))):
        ip = OUT / sub / 'index.json'
        order = [e['id'] for e in entries_]
        idx = {r['id']: r for r in (json.loads(ip.read_text(encoding='utf-8')) if ip.exists() else [])}
        idx.update({r['id']: {'id': r['id'], 'facts': r['gold']} for r in summary if r['id'] in order})
        if any(r['id'] in order for r in summary):  # only touch the index this run built for
            ip.parent.mkdir(parents=True, exist_ok=True)
            write_json(ip, [idx[i] for i in order if i in idx])


if __name__ == '__main__':
    main()
