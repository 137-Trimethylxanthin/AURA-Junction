"""Precompute the library Decisions checks -> build/drawings/<id>/checks.json (read by L2's cache at 0 calls).

Format (agreed with L2): {"<subject>": run_check(...) result + "key": check_key(id, subject, ids)}.
One request per (drawing, subject) with at least one box, all boxes of the subject at floor scope.

DRY RUN BY DEFAULT: prints the planned requests and makes no call. Live calls need --live and
GEMINI_API_KEY, use the "measurement" bucket, and must be authorised by the lead (report the count).

Usage (from backend/):
  uv run python -I scripts/precompute_checks.py                       # plan only
  uv run python -I scripts/precompute_checks.py --live [--ids swiss-22513 ...] [--subjects window door washbasin]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DRAWINGS = BACKEND / 'build' / 'drawings'
DEFAULT_SUBJECTS = ('window', 'door', 'washbasin')


def plan(ids: list[str], subjects: list[str]) -> list[tuple[str, str, int]]:
    out = []
    for i in ids:
        d = json.loads((DRAWINGS / i / 'drawing.json').read_text(encoding='utf-8'))
        for s in subjects:
            n = len(d['evidence'].get(s, []))
            if n:
                out.append((i, s, n))
    return out


async def run(ids: list[str], subjects: list[str], log_path: Path) -> int:
    from aura.budget import Budget
    from aura.config import get_settings
    from aura.gemini.check import check_key, default_anchors, run_check
    from aura.gemini.client import GeminiClient, HttpTransport

    settings = get_settings()
    key = os.environ.get('GEMINI_API_KEY') or (settings.gemini_api_key.get_secret_value()
                                                if settings.gemini_api_key else None)
    if not key:
        raise SystemExit('GEMINI_API_KEY is not set')
    client = GeminiClient(settings, Budget(settings), HttpTransport(key))
    requests = 0
    lines: list[str] = []
    try:
        for i in ids:
            ddir = DRAWINGS / i
            d = json.loads((ddir / 'drawing.json').read_text(encoding='utf-8'))
            all_boxes = [b for v in d['evidence'].values() for b in v]
            cp = ddir / 'checks.json'
            checks = json.loads(cp.read_text(encoding='utf-8')) if cp.exists() else {}
            for s in subjects:
                boxes = d['evidence'].get(s, [])
                if not boxes:
                    continue
                res = await run_check(client, drawing_id=i, image_path=ddir / 'original.png', subject=s,
                                      boxes=boxes, anchors=default_anchors(d['rooms'], all_boxes),
                                      bucket='measurement')
                requests += 1
                res['key'] = check_key(i, s, [b['id'] for b in boxes])
                checks[s] = res
                cp.write_text(json.dumps(checks, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
                lines.append(json.dumps({'purpose': 'L1-precompute-check', 'drawing': i, 'subject': s,
                                      'boxes': len(boxes), 'input_tokens': res['input_tokens'],
                                      'cost_usd': res['cost_usd'], 'latency_ms': res['latency_ms'],
                                      'model': res['model'], 'at': res['measured_at']}) + '\n')
                agreed = sum(1 for p in res['per_box'].values()
                             if res['anchors_max'] is not None and p > res['anchors_max'] + 0.05)
                print(f"{i} {s}: {len(boxes)} boxes, agreed {agreed}, anchors_max {res['anchors_max']}, "
                      f"{res['input_tokens']} tok, ${res['cost_usd']:.4f}, {res['latency_ms']} ms")
    finally:  # log whatever was spent, even if a call fails midway
        with log_path.open('a', encoding='utf-8') as log:
            log.writelines(lines)
    return requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true', help='make the Gemini calls (authorised runs only)')
    ap.add_argument('--ids', nargs='*', help='drawing ids (default: the 12 library drawings)')
    ap.add_argument('--subjects', nargs='*', default=list(DEFAULT_SUBJECTS))
    ap.add_argument('--log', default=str(BACKEND / 'build' / 'checks_requests.jsonl'))
    a = ap.parse_args()
    ids = a.ids or [d['id'] for d in json.loads((DRAWINGS / 'library.json').read_text(encoding='utf-8'))['drawings']]
    p = plan(ids, a.subjects)
    print(f'{len(p)} requests planned over {len(ids)} drawings, {sum(n for *_, n in p)} boxes')
    for i, s, n in p:
        print(f'  {i} {s} {n}')
    if not a.live:
        print('dry run: no calls made (pass --live)')
        return
    n = asyncio.run(run(ids, a.subjects, Path(a.log)))
    print(f'{n} requests made; log {a.log}')


if __name__ == '__main__':
    main()
