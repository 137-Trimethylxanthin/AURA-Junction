"""CubiCasa5k held-out detection eval (CC30), reproducible. The published numbers are in results/cubicasa.json.

Protocol (frozen 2026-11-14, see .omc/research/gemini/gemini-usecases.md):
- Eligible: CubiCasa images with both a door and a window count question; minus the 3 E2g images -> 3,516.
- Selection: random.Random(2026).sample(sorted(images of style), 10) for each style in alphabetical order.
- Detection: aura.solvers.raster.detect (gemini-4-hydrogen, e2g prompt, temperature 0, JSON, default resolution).
- Score: exact count per subject, Wilson 95 %, MAE, within +-1, over/under, pool-reweighted exact rate.

Modes (from backend/):
  uv run python -I eval/cubicasa.py --select              # selection only, 0 calls
  uv run python -I eval/cubicasa.py --recorded DIR        # re-score recorded det_<name>_resp.json files, 0 calls
  uv run python -I eval/cubicasa.py --live --out FILE     # 30 detection calls (measurement bucket); authorised runs only
The tile check (which made counts worse) and the score prior are not re-implemented here; their recorded runs are
in .omc/research/gemini/experiments/scripts/{cc30_check_score.py,build_e2.py}.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from aura.solvers import raster

DEFAULT_DATA = BACKEND.parent / 'data' / 'junction-dataset'
RECORDED = BACKEND.parent / '.omc' / 'research' / 'gemini' / 'experiments' / 'cc30'
E2G = {'colorful-11774', 'high_quality_architectural-14311', 'high_quality_architectural-2117'}
SEED, PER_STYLE = 2026, 10


def select(data: Path) -> tuple[list[dict], dict[str, int]]:
    q = collections.defaultdict(dict)
    with open(data / 'questions.jsonl', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            r = json.loads(line)
            if '/cubicasa5k/' not in r['path']:
                continue
            name = Path(r['path']).stem
            for subj, prefix in (('door', 'How many doors'), ('window', 'How many windows')):
                if r['question'].startswith(prefix) and subj not in q[name]:
                    q[name][subj] = (i, r['answer'])
    eligible = sorted(n for n, v in q.items() if len(v) == 2 and n not in E2G)
    by = collections.defaultdict(list)
    for n in eligible:
        by[n.rsplit('-', 1)[0]].append(n)
    rng = random.Random(SEED)
    picks = [n for s in sorted(by) for n in rng.sample(sorted(by[s]), PER_STYLE)]
    rows = [{'name': n, 'style': n.rsplit('-', 1)[0], 'doors': q[n]['door'][1], 'windows': q[n]['window'][1],
             'door_line': q[n]['door'][0], 'window_line': q[n]['window'][0]} for n in picks]
    return rows, {s: len(v) for s, v in by.items()}


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(c - h, 3), round(c + h, 3)]


def score(rows: list[dict], pool: dict[str, int]) -> dict:
    out = {}
    for subj, g, p in (('door', 'doors', 'pd'), ('window', 'windows', 'pw')):
        err = [r[p] - r[g] for r in rows]; n = len(rows); k = sum(e == 0 for e in err)
        per = {}
        for st in sorted(pool):
            rs = [r for r in rows if r['style'] == st]
            per[st] = {'exact': sum(r[p] == r[g] for r in rs), 'n': len(rs),
                       'mae': round(sum(abs(r[p] - r[g]) for r in rs) / len(rs), 2)}
        tot = sum(pool.values())
        out[subj] = {'exact': k, 'n': n, 'exact_rate': round(k / n, 4), 'wilson95': wilson(k, n),
                     'mae': round(sum(map(abs, err)) / n, 2), 'over': sum(e > 0 for e in err),
                     'under': sum(e < 0 for e in err), 'within_1': sum(abs(e) <= 1 for e in err),
                     'sum_pred': sum(r[p] for r in rows), 'sum_gold': sum(r[g] for r in rows),
                     'exact_reweighted_to_pool': round(sum(pool[s] / tot * per[s]['exact'] / per[s]['n'] for s in pool), 3),
                     'per_style': per}
    out['both_exact_same_image'] = sum(r['pd'] == r['doors'] and r['pw'] == r['windows'] for r in rows)
    return out


def from_recorded(sel: list[dict], d: Path) -> list[dict]:
    rows = []
    for o in sel:
        resp = json.loads((d / f"det_{o['name']}_resp.json").read_text(encoding='utf-8'))
        parts = resp['candidates'][0]['content']['parts']
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
        c = raster.boxes_from_items(raster.parse_items(text))
        rows.append({**o, 'pd': len(c['door']), 'pw': len(c['window'])})
    return rows


async def live(sel: list[dict], data: Path) -> tuple[list[dict], list[dict]]:
    import os

    from aura.budget import Budget
    from aura.config import get_settings
    from aura.gemini.client import GeminiClient, HttpTransport
    s = get_settings()
    key = os.environ.get('GEMINI_API_KEY') or (s.gemini_api_key.get_secret_value() if s.gemini_api_key else None)
    if not key:
        raise SystemExit('GEMINI_API_KEY is not set')
    client = GeminiClient(s, Budget(s), HttpTransport(key))
    rows, log = [], []
    for o in sel:
        img = (data / 'data' / 'cubicasa5k' / f"{o['name']}.png").read_bytes()
        r = await raster.detect(client, img, bucket='measurement', presenter=True)
        rows.append({**o, 'pd': r['counts']['door'], 'pw': r['counts']['window']})
        log.append({k: r[k] for k in ('model', 'input_tokens', 'output_tokens', 'cost_usd', 'latency_ms')})
    return rows, log


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--select', action='store_true')
    g.add_argument('--recorded', nargs='?', const=str(RECORDED))
    g.add_argument('--live', action='store_true')
    ap.add_argument('--data', default=str(DEFAULT_DATA))
    ap.add_argument('--out')
    a = ap.parse_args()
    data = Path(a.data)
    sel, pool = select(data)
    frozen = RECORDED / 'selection.json'
    if frozen.exists():
        same = [o['name'] for o in sel] == [o['name'] for o in json.loads(frozen.read_text(encoding='utf-8'))]
        print(f'selection equals the frozen cc30/selection.json: {same}')
    print('eligible per style', pool, 'total', sum(pool.values()))
    if a.select:
        print(json.dumps([o['name'] for o in sel]))
        return
    calls, log = 0, []
    if a.live:
        rows, log = asyncio.run(live(sel, data))
        calls = len(log)
    else:
        rows = from_recorded(sel, Path(a.recorded))
    res = {'dataset': 'cubicasa5k', 'mode': 'live' if a.live else 'recorded', 'calls': calls,
           'measured_at': dt.datetime.now(dt.UTC).isoformat(timespec='seconds'), 'selection_seed': SEED,
           'eligible': pool, 'detection': score(rows, pool), 'rows': rows}
    if log:
        lat = sorted(x['latency_ms'] for x in log)
        res['cost_usd_per_image_mean'] = round(sum(x['cost_usd'] for x in log) / len(log), 4)
        res['latency_ms_median'] = lat[len(lat) // 2]
    for s in ('door', 'window'):
        d = res['detection'][s]
        print(f"{s}: exact {d['exact']}/{d['n']} wilson {d['wilson95']} mae {d['mae']} within1 {d['within_1']} "
              f"over/under {d['over']}/{d['under']} reweighted {d['exact_reweighted_to_pool']}")
    print('both exact', res['detection']['both_exact_same_image'], 'calls', calls)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
