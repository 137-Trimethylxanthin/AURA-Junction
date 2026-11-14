"""Reconcile the claims of one drawing: one preferred value per entity and property, conflicts and gaps as issues.

Policy
  * a printed value beats a stored quantity, which beats a value read by OCR, which beats a measured one;
  * within one method, vector CAD beats a model (IFC), beats vector PDF, beats raster;
  * a value that differs by more than 2 % from two better-ranked values that agree with each other is 'deprecated'
    and reported (value_conflict, with the numbers);
  * values that are kept but spread by more than 2 % are reported too, nothing is hidden;
  * a room that one source file shows and another file of the same drawing does not is reported (missing_in_source).
"""
from __future__ import annotations

import collections

from ..db import DB

TOLERANCE = 0.02
METHOD = {"printed": 0, "stored_quantity": 1, "ocr": 2, "measured_outline": 3, "measured_geometry": 3, "detected": 5}
FORMAT = {"dxf": 0, "dwg": 0, "ifc": 1, "pdf": 2, "svg": 2, "png": 3, "jpg": 3}


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1e-9)


def _order(c: dict) -> tuple:
    return (METHOD.get(c["method"], 4), FORMAT.get(c["format"], 4), -(c["confidence"] or 0), c["claim_id"])


def run(db: DB, drawing_key: str) -> dict:
    claims = db.run("""select c.claim_id, c.entity_id, c.property, c.value_num, c.value_text, c.method, c.confidence,
                              c.file_id, f.format, f.path, e.kind, e.number, e.project_id
                       from jstore.claim c join jstore.entity e using (entity_id)
                       left join jstore.source_file f on f.file_id = c.file_id
                       where e.drawing_key = :d""", d=drawing_key)
    groups = collections.defaultdict(list)
    for c in claims:
        groups[c["entity_id"], c["property"]].append(c)
    rank, issues = {}, []

    def show(c):
        v = c["value_num"] if c["value_num"] is not None else c["value_text"]
        return f"{v} ({c['method']}, {(c['path'] or '?').rsplit('/', 1)[-1]})"

    for (eid, prop), cs in groups.items():
        cs.sort(key=_order)
        nums = [c for c in cs if c["value_num"] is not None]
        dead = set()
        for i, c in enumerate(nums):
            better = nums[:i]
            if any(_agree(a["value_num"], b["value_num"]) and not _agree(a["value_num"], c["value_num"])
                   and not _agree(b["value_num"], c["value_num"])
                   for j, a in enumerate(better) for b in better[j + 1:]
                   if (a["file_id"], a["method"]) != (b["file_id"], b["method"])):
                dead.add(c["claim_id"])
        live = [c for c in cs if c["claim_id"] not in dead]
        for c in cs:
            rank[c["claim_id"]] = "deprecated" if c["claim_id"] in dead else "normal"
        rank[live[0]["claim_id"]] = "preferred"
        who = f"{cs[0]['kind']} {cs[0]['number'] or eid}"
        if dead:
            issues.append((cs[0]["project_id"], eid, prop, "value_conflict",
                           f"{who} {prop}: " + "; ".join(show(c) for c in cs if c["claim_id"] in dead) +
                           " set aside, differs by more than 2 % from " +
                           " and ".join(show(c) for c in nums if c["claim_id"] not in dead)))
        vals = [c["value_num"] for c in live if c["value_num"] is not None]
        if vals and not _agree(min(vals), max(vals)):
            issues.append((cs[0]["project_id"], eid, prop, "value_conflict",
                           f"{who} {prop}: sources differ by more than 2 %: " + "; ".join(
                               show(c) for c in live if c["value_num"] is not None) + f"; kept {show(live[0])}"))
    if rank:
        db.run("""update jstore.claim c set rank = v.rank
                  from (select unnest(cast(:ids as bigint[])) as claim_id, unnest(cast(:ranks as text[])) as rank) v
                  where c.claim_id = v.claim_id and c.rank is distinct from v.rank""",
               ids=list(rank), ranks=list(rank.values()))
    # rooms seen in one source file and not in another that shows rooms
    src = db.run("""select e.entity_id, e.number, e.label, e.project_id, s.file_id, f.path
                    from jstore.entity e join jstore.entity_source s using (entity_id)
                    join jstore.source_file f on f.file_id = s.file_id
                    where e.drawing_key = :d and e.kind = 'room' and e.status = 'active'
                    group by 1, 2, 3, 4, 5, 6""", d=drawing_key)
    files = {r["file_id"]: r["path"].rsplit("/", 1)[-1] for r in src}
    seen = collections.defaultdict(set)
    for r in src:
        seen[r["entity_id"]].add(r["file_id"])
    meta = {r["entity_id"]: r for r in src}
    if len(files) > 1:
        for eid, have in seen.items():
            for fid in sorted(set(files) - have):
                r = meta[eid]
                issues.append((r["project_id"], eid, None, "missing_in_source",
                               f"room {r['number'] or ''} {r['label'] or ''}".rstrip() +
                               f" is in {', '.join(files[x] for x in sorted(have))} but was not found in {files[fid]}"))
    for p, eid, prop, kind, detail in issues:
        db.run("""insert into jstore.issue (project_id, drawing_key, entity_id, property, kind, detail)
                  values (:p, :d, :e, :prop, :k, :t)""", p=p, d=drawing_key, e=eid, prop=prop, k=kind, t=detail[:2000])
    return dict(claims=len(rank), issues=len(issues))
