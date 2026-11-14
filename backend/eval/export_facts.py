"""Measured results -> landing-page facts JSON (L4 runs this into its own tree; L1 only provides it).

Usage (from backend/): uv run python -I eval/export_facts.py --out ../frontend/src/pages/landing/facts.json
Reads eval/results/swiss.json, eval/results/cubicasa.json and build/drawings/{library,index}.json.
Percentages are truncated, never rounded up.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
RESULTS = BACKEND / 'eval' / 'results'
DRAWINGS = BACKEND / 'build' / 'drawings'


def pct(num: float, den: float, dp: int = 1) -> str:
    """Truncated percentage, e.g. 18606/18792 -> '99.0 %' (dp=1)."""
    f = 10 ** dp
    return f'{math.floor(num / den * 100 * f + 1e-9) / f:.{dp}f} %'


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def facts() -> list[dict]:
    out = []
    sw = load(RESULTS / 'swiss.json')
    if sw:
        out.append({
            'id': 'swiss-full', 'label': 'Swiss floor plans answered exactly from the CAD file',
            'value': pct(sw['correct'], sw['questions']), 'numerator': sw['correct'], 'denominator': sw['questions'],
            'n': sw['questions'], 'n_unit': 'questions', 'model_calls': sw['calls'], 'cost_usd': sw['cost_usd'],
            'scope': f"Every English counting and area question on the {sw['files']} Swiss Dwellings floor plans in the dataset",
            'method': 'DXF layers read with ezdxf and shapely; counts and areas computed in code, no model',
            'measured': sw['measured_at'][:10],
            'caveat': 'Each question also has a German, French or Italian twin asking the same fact; only the English '
                   'lines were scored. Areas count as correct within 2 %, as in the dataset.'})
    lib, idx = load(DRAWINGS / 'library.json'), load(DRAWINGS / 'index.json')
    if lib and idx:
        ids = {d['id'] for d in lib['drawings']}
        rows = [r['facts'] for r in idx if r['id'] in ids]
        if rows:
            ok, n = sum(r['correct'] for r in rows), sum(r['gold_total'] for r in rows)
            out.append({
                'id': 'swiss-library', 'label': 'Library drawings: answers matching the dataset',
                'value': pct(ok, n), 'numerator': ok, 'denominator': n, 'n': n, 'n_unit': 'questions', 'model_calls': 0, 'cost_usd': 0,
                'scope': f'The {len(rows)} seeded library drawings (random.Random(46639) over plans with at least 30 questions)',
                'method': 'Same solver as the full-set run, answers compared with the dataset gold',
                'measured': min(r['measured_at'] for r in rows)[:10]})
    ic = load(RESULTS / 'ifc.json')
    if ic:
        pr = ic['probe_rules_reproduced']
        out.append({
            'id': 'ifc-full', 'label': 'IFC building models answered from the model file',
            'value': pct(ic['correct'], ic['questions']), 'numerator': ic['correct'], 'denominator': ic['questions'],
            'n': ic['questions'], 'n_unit': 'questions', 'model_calls': ic['calls'], 'cost_usd': ic['cost_usd'],
            'scope': f"Every English question on the {ic['files']} IFC models in the dataset (counts, room and storey "
                     'areas, wall lengths and volumes)',
            'method': 'IfcOpenShell quantities and geometry, computed in code, no model',
            'measured': ic['measured_at'][:10],
            'caveat': (f"Two rules were added after inspecting the misses on this same set; with the original rules "
                       f"the result is {pr['correct']:,}/{pr['questions']:,} ({pct(pr['correct'], pr['questions'])})."),
        })
    cc = load(RESULTS / 'cubicasa.json')
    if cc:
        det = cc['detection']
        for subj, label in (('door', 'Doors'), ('window', 'Windows')):
            d = det[subj]
            lo, hi = d['wilson95']
            out.append({
                'id': f'cubicasa-{subj}-exact', 'label': f'{label} counted exactly on scanned plans (model estimate)',
                'value': pct(d['exact'], d['n']), 'numerator': d['exact'], 'denominator': d['n'], 'n': d['n'], 'n_unit': 'images',
                'model_calls': 1, 'cost_usd': det['cost_usd_per_image_mean'],
                'scope': '30 held-out CubiCasa5k plans, 10 per drawing style',
                'method': 'One Gemini detection call per image; count = boxes returned',
                'measured': cc['measured_at'],
                'caveat': f"Wilson 95 % interval {lo * 100:.0f}–{hi * 100:.0f} %; within ±1: {d['within_1']}/{d['n']}; "
                       f"{d['exact_reweighted_to_pool'] * 100:.1f} % when reweighted to the dataset's style mix."})
        s = cc['score_prior']['room_and_fixture_families']
        out.append({
            'id': 'cubicasa-score-confident', 'label': 'Room and fixture counts right when the model is confident',
            'value': pct(s['accepted_correct'], s['accepted']), 'numerator': s['accepted_correct'], 'denominator': s['accepted'],
            'n': s['accepted'], 'n_unit': 'accepted answers', 'model_calls': 1, 'cost_usd': cc['score_prior']['cost_usd_per_image'],
            'scope': f"{s['n']} room and fixture questions on 15 held-out CubiCasa5k plans",
            'method': 'Gemini Decisions score per family; answer only at confidence 0.6 or higher',
            'measured': cc['measured_at'],
            'caveat': f"Answers {s['accepted']} of {s['n']} ({pct(s['accepted'], s['n'], 0)}); the rest say 'not sure'."})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    f = facts()
    for x in f:
        assert all(x.get(k) not in (None, '') for k in ('value', 'n', 'scope', 'method', 'measured')), x['id']
    Path(a.out).write_text(json.dumps({'facts': f}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'wrote {len(f)} facts to {a.out}')


if __name__ == '__main__':
    main()
