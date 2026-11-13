"""Guard for regenerating the bundled frontend drawing (plan P2).

Compares the backend build of swiss-46639 with the current frontend file and exits 1 unless:
  - the existing room_ids keep num, name, unit and area_m2 (new rooms are allowed and listed);
  - win-*, door-*, basin-* ids, their order and their boxes match within +-0.3 Pct;
  - rect is present on the same rooms (rect w_m/h_m differences up to 5 mm are reported, not failed);
  - the only answer changes, recomputed from each file the way the UI does, are: floor area (must match gold
    within 2 %), apartment B (within 2 % of gold) and WC / bathtub / shower (newly answered, must match gold).
With --write OUT it writes the merged file for L3 to copy: the backend drawing with the current file's images,
gold and rect values kept (so the what-if defaults do not move) and no thumbnail.

Usage (from backend/):
  uv run python -I scripts/diff_drawing.py build/drawings/swiss-46639/drawing.json \
      ../frontend/src/data/drawing.swiss-46639.json [--write OUT]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

BOX_TOL = 0.3  # Pct
RECT_M_TOL = 0.005  # metres
GUARDED_EVIDENCE = ('window', 'door', 'washbasin')
NEW_SUBJECTS = ('wc', 'bathtub', 'shower')


def ui_answer(d: dict, g: dict):
    """What the UI shows for gold row `g`, recomputed from drawing `d` (None = cannot answer)."""
    rooms, scope, ans = d['rooms'], g['scope'], g['answer']
    k = scope['kind']
    if ans['kind'] == 'count':
        s = ans['subject']
        if s == 'apartment':
            return len({r['unit'] for r in rooms if r.get('unit')})
        if s == 'room':
            return sum(1 for r in rooms if k == 'label' and r['name'] == scope['label'])
        ev = d.get('evidence', {})
        return len(ev[s]) if s in ev else None
    if k == 'room':
        return next((r['area_m2'] for r in rooms if r['room_id'] == scope['room_id']), None)
    if k == 'label':
        sel = [r for r in rooms if r['name'] == scope['label']]
    elif k == 'unit':
        sel = [r for r in rooms if r.get('unit') == scope['unit'] and not r['outdoor']]
    else:
        sel = [r for r in rooms if not r['outdoor']]
    return round(sum(r['area_m2'] for r in sel), 2)


def matches(v, g) -> bool:
    if v is None:
        return False
    return v == g['value'] if g['tolerance'] == 'exact' else abs(v - g['value']) <= 0.02 * abs(g['value'])


def allowed_change(g: dict) -> bool:
    a, s = g['answer'], g['scope']
    if a['kind'] == 'count' and a.get('subject') in NEW_SUBJECTS:
        return True
    if a['kind'] == 'area' and s['kind'] == 'floor':
        return True
    return a['kind'] == 'area' and s == {'kind': 'unit', 'unit': 'B'}


def diff(new: dict, cur: dict) -> tuple[list[str], list[str]]:
    """Returns (failures, notes)."""
    fail, note = [], []
    nr = {r['room_id']: r for r in new['rooms']}
    cr = {r['room_id']: r for r in cur['rooms']}
    for rid, c in cr.items():
        n = nr.get(rid)
        if n is None:
            fail.append(f'room {rid} missing')
            continue
        for f in ('num', 'name', 'unit', 'area_m2'):
            if n.get(f) != c.get(f):
                fail.append(f'room {rid} {f}: {c.get(f)!r} -> {n.get(f)!r}')
        if n['points'] != c['points']:
            note.append(f'room {rid} polygon points differ')
        if n['label_at'] != c['label_at']:
            note.append(f'room {rid} label_at {c["label_at"]} -> {n["label_at"]}')
        if ('rect' in c) != ('rect' in n):
            fail.append(f'room {rid} rect present: {"rect" in c} -> {"rect" in n}')
        elif 'rect' in c:
            a, b = c['rect'], n['rect']
            for f in ('x', 'y', 'w', 'h'):
                if abs(a[f] - b[f]) > BOX_TOL:
                    fail.append(f'room {rid} rect.{f}: {a[f]} -> {b[f]}')
            for f in ('w_m', 'h_m'):
                dm = abs(a[f] - b[f])
                if dm > RECT_M_TOL:
                    fail.append(f'room {rid} rect.{f}: {a[f]} -> {b[f]} (> 5 mm)')
                elif dm > 0:
                    note.append(f'room {rid} rect.{f}: {a[f]} -> {b[f]} ({dm * 1000:.0f} mm, kept from current)')
    added = [r for r in nr if r not in cr]
    if added:
        note.append('new rooms: ' + ', '.join(f"{r} ({nr[r]['name'] or 'unnamed'}, {nr[r]['area_m2']} m²"
                                                f"{', outline area' if nr[r].get('area_source') == 'outline' else ''})"
                                                for r in added))
    nev, cev = new.get('evidence', {}), cur.get('evidence', {})
    for s in GUARDED_EVIDENCE:
        a, b = cev.get(s, []), nev.get(s, [])
        if [x['id'] for x in a] != [x['id'] for x in b]:
            fail.append(f'evidence {s}: ids/order {[x["id"] for x in a]} -> {[x["id"] for x in b]}')
            continue
        for x, y in zip(a, b):
            for f in ('x', 'y', 'w', 'h'):
                if abs(x[f] - y[f]) > BOX_TOL:
                    fail.append(f'evidence {x["id"]}.{f}: {x[f]} -> {y[f]}')
            for f in ('room_id', 'unit', 'note'):
                if x.get(f) != y.get(f):
                    note.append(f'evidence {x["id"]}.{f}: {x.get(f)!r} -> {y.get(f)!r}')
    for s in NEW_SUBJECTS:
        if s in nev:
            note.append(f'evidence {s}: {len(nev[s])} boxes (new)')
    for g in cur.get('gold', []):
        before, after = ui_answer(cur, g), ui_answer(new, g)
        if before == after:
            continue
        tag = f"{g['dataset_qid']} {g['question_en']}: {before} -> {after} (gold {g['value']})"
        if not allowed_change(g):
            fail.append('answer changed: ' + tag)
        elif not matches(after, g):
            fail.append('allowed answer change does not match gold: ' + tag)
        else:
            note.append('answer change: ' + tag)
    return fail, note


def merge(new: dict, cur: dict) -> dict:
    out = copy.deepcopy(new)
    out['images'] = cur['images']
    out.pop('thumbnail', None)
    out['gold'] = cur['gold']
    cr = {r['room_id']: r for r in cur['rooms']}
    for r in out['rooms']:
        c = cr.get(r['room_id'])
        if c is None:
            r.pop('rect', None)  # only the existing what-if rooms stay resizable
        elif 'rect' in c:
            r['rect'] = c['rect']
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('new')
    ap.add_argument('current')
    ap.add_argument('--write', help='write the merged drawing here (only when the diff passes)')
    a = ap.parse_args()
    new = json.loads(Path(a.new).read_text(encoding='utf-8'))
    cur = json.loads(Path(a.current).read_text(encoding='utf-8'))
    fail, note = diff(new, cur)
    for n in note:
        print('note:', n)
    for f in fail:
        print('FAIL:', f)
    if fail:
        print(f'{len(fail)} failure(s); nothing written')
        sys.exit(1)
    if a.write:
        merged = merge(new, cur)
        f2, _ = diff(merged, cur)
        assert not f2, f2
        Path(a.write).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('wrote', a.write)
    print('OK')


if __name__ == '__main__':
    main()
