"""Doors, windows and fixtures detected twice on one page image: counted once.

The detector asks about overlapping views of a page.  Boxes of the same thing are merged there, but a box that is far
smaller than the one it lies in was left apart (a window and the box around the whole run of windows, a door and its
leaf, a shower and its cubicle): two entities for one drawn thing, drawn once by the plan (plan_tidy) and counted
twice by every count.  jstore.gemini_detect.nested_duplicates names the copy to drop; new interpretations drop it at
detection time, this module does the same for what is in the store:

    of_drawing(db, key) -> {entity_id dropped: entity_id kept}          (nothing is changed)
    prune(db, key, dry_run) -> the same, and the dropped entities get status 'superseded' and
                               attrs.duplicate_detection_of = the entity kept (the views and counts read active ones)
    restore(db, key | None) -> n entities put back

Only entities that come from a detection on a page image (entity_source.ref_table = 'raster_detection', role
'symbol') are looked at: what a CAD or IFC file draws twice stays two entities (is_duplicate in the views).
"""
from __future__ import annotations

from .gemini_detect import nested_duplicates

KINDS = ("door", "window", "toilet", "washbasin", "sink", "bathtub", "shower")


def of_drawing(db, key: str) -> dict:
    rows = db.run("""select e.entity_id, e.kind, s.file_id, coalesce(s.page_no, 0) as page_no, d.x0, d.y0, d.x1, d.y1,
                            d.score, (d.attrs->>'votes')::int as votes
                     from jstore.entity e
                     join jstore.entity_source s on s.entity_id = e.entity_id and s.ref_table = 'raster_detection'
                          and s.role = 'symbol'
                     join jstore.raster_detection d on d.id = cast(s.ref_key as bigint) and d.file_id = s.file_id
                     where e.drawing_key = :k and e.status = 'active' and e.kind = any(cast(:kinds as text[]))
                     order by e.entity_id""", k=key, kinds=list(KINDS))
    pages: dict = {}
    for r in rows:
        pages.setdefault((r["file_id"], r["page_no"]), []).append(r)
    out = {}
    for items in pages.values():
        dup = nested_duplicates([(r["kind"], [r["x0"], r["y0"], r["x1"], r["y1"]], r["votes"], r["score"]) for r in items])
        out.update({items[i]["entity_id"]: items[j]["entity_id"] for i, j in dup.items()})
    return out


def keys(db) -> list[str]:
    """Drawings that have detected doors, windows or fixtures."""
    return [r["k"] for r in db.run(
        """select distinct e.drawing_key as k from jstore.entity e join jstore.entity_source s using (entity_id)
           where e.status = 'active' and e.kind = any(cast(:kinds as text[])) and s.ref_table = 'raster_detection'
             and s.role = 'symbol' order by 1""", kinds=list(KINDS))]


def prune(db, key: str, dry_run: bool = False) -> dict:
    drop = of_drawing(db, key)
    if not dry_run:
        for eid, keep in drop.items():
            db.run("""update jstore.entity set status = 'superseded',
                             attrs = attrs || jsonb_build_object('duplicate_detection_of', cast(:o as text))
                      where entity_id = :e and status = 'active'""", e=eid, o=str(keep))
    return drop


def restore(db, key: str | None = None) -> int:
    return len(db.run("""update jstore.entity set status = 'active', attrs = attrs - 'duplicate_detection_of'
                         where status = 'superseded' and attrs ? 'duplicate_detection_of'
                           and (cast(:k as text) is null or drawing_key = :k) returning entity_id""", k=key))
