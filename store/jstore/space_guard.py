"""Which enclosed spaces without a label are not rooms.

The free-space analysis of an image plan (raster_plan) makes a room of every enclosed space the sheet prints no label
for.  Most are rooms (a bathroom, a balcony, a store).  Some are not: the strip between a dimension line and the façade,
the gap between two plans on one sheet, a dashed roof outline, the space between a page border and the building.  They
differ in what bounds them: a room is bounded by walls (or by rooms the sheet names), the others mostly by thin lines,
or by the edge of the page.

    wall   share of the outline that runs along wall fill (within WALL_NEAR_M)
    named  share of the outline that runs along rooms with a label (within NAMED_NEAR_M)
    edge   share of the outline that runs along the edge of the page (within EDGE_NEAR of its shorter side)

A space is dropped when  edge >= EDGE_MAX,  or  wall < WALL_MIN,  or  wall < WALL_SURE and named < NAMED_MIN.
Thresholds read off six audited sheets (CubiCasa 108, 10290, 10321, 11553, 10000 and HABS ok0071 sheet 2): every space
the audit called false goes, the balcony and the bathroom of 108 and the lobby of 10290 stay.  Geometry is in metres.
"""
from __future__ import annotations

import shapely

WALL_NEAR_M = 0.35
NAMED_NEAR_M = 0.6
EDGE_NEAR = 0.03        # of the shorter side of the page
WALL_MIN = 0.6
WALL_SURE = 0.75
NAMED_MIN = 0.5
EDGE_MAX = 0.15


def measures(space, walls, named, page=None) -> dict:
    """wall / named / edge shares of one space's outline.  `walls` and `named` are (unions of) polygons or None,
    `page` the page rectangle (all in the frame of `space`)."""
    b = space.boundary if space is not None and not space.is_empty else None
    if b is None or not b.length:
        return dict(wall=0.0, named=0.0, edge=0.0)

    def share(g, d):
        if g is None or g.is_empty:
            return 0.0
        return float(b.intersection(g.buffer(d)).length / b.length)

    edge = 0.0
    if page is not None and not page.is_empty:
        x0, y0, x1, y1 = page.bounds
        d = EDGE_NEAR * min(x1 - x0, y1 - y0)
        inner = shapely.box(x0 + d, y0 + d, x1 - d, y1 - d)
        edge = float(b.difference(inner).length / b.length)
    return dict(wall=share(walls, WALL_NEAR_M), named=share(named, NAMED_NEAR_M), edge=edge)


def why_not_a_room(m: dict) -> str | None:
    """The reason a space with these measures is not a room, or None when it is one."""
    if m["edge"] >= EDGE_MAX:
        return f"runs along the edge of the page ({m['edge']:.0%} of its outline)"
    if m["wall"] < WALL_MIN:
        return f"only {m['wall']:.0%} of its outline is wall"
    if m["wall"] < WALL_SURE and m["named"] < NAMED_MIN:
        return f"{m['wall']:.0%} of its outline is wall and {m['named']:.0%} runs along labelled rooms"
    return None


def false_spaces(rooms: list[dict], walls: list, page=None) -> dict:
    """{entity_id: reason} of the spaces without a label that are not rooms.  `rooms`: dicts with entity_id, label,
    geom (shapely or None) and by (who made the entity); only spaces made by the free-space analysis are judged, and
    only on a sheet that has walls (without walls nothing can be said)."""
    solid = [w for w in walls if w is not None and not w.is_empty]
    if not solid:
        return {}
    wall = shapely.union_all(solid)
    named = [r["geom"] for r in rooms if (r.get("label") or "").strip() and r.get("geom") is not None]
    named = shapely.union_all(named) if named else None
    out = {}
    for r in rooms:
        if (r.get("label") or "").strip() or r.get("geom") is None or r.get("by") != "jstore.interpret.raster_plan":
            continue
        g = r["geom"] if r["geom"].is_valid else r["geom"].buffer(0)
        why = why_not_a_room(measures(g, wall, named, page))
        if why:
            out[r["entity_id"]] = why
    return out


# ------------------------------------------------------------------------------------------------ on the store
def of_drawing(db, key: str) -> dict:
    """{entity_id: reason} of the active spaces without a label of one drawing that are not rooms (nothing is changed)."""
    from shapely import wkb
    rows = db.run("""select entity_id, label, attrs->>'by' as by, encode(st_asbinary(geom), 'hex') as g from jstore.entity
                     where drawing_key = :k and kind = 'room' and status = 'active'""", k=key)
    if not any(r["by"] == "jstore.interpret.raster_plan" and not (r["label"] or "").strip() for r in rows):
        return {}
    walls = [wkb.loads(w["g"], hex=True) for w in db.run(
        """select encode(st_asbinary(geom), 'hex') as g from jstore.entity
           where drawing_key = :k and kind = 'wall' and status = 'active' and geom is not null""", k=key)]
    pages = db.run("""select fr.m, p.image_w as w, p.image_h as h from jstore.source_file f
                      join jstore.page p using (file_id) join jstore.frame fr using (file_id, page_no)
                      where f.drawing_key = :k and f.format in ('png', 'jpg') and p.image_w > 0 and p.image_h > 0""", k=key)
    page = None
    if len(pages) == 1 and len(pages[0]["m"]) == 6:      # one image, one frame: the page rectangle in metres
        m, w, h = pages[0]["m"], pages[0]["w"], pages[0]["h"]
        page = shapely.Polygon([(m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5])
                                for x, y in ((0, 0), (w, 0), (w, h), (0, h))])
    rooms = [dict(entity_id=r["entity_id"], label=r["label"], by=r["by"],
                  geom=wkb.loads(r["g"], hex=True) if r["g"] else None) for r in rows]
    return false_spaces(rooms, walls, page)


def prune(db, key: str, dry_run: bool = False) -> dict:
    """Takes the false spaces of a drawing out of the store's views (status 'superseded', the reason in
    attrs.not_a_room), so counts, lists, plan and 3D agree.  Undone by restore()."""
    bad = of_drawing(db, key)
    if not dry_run:
        for eid, why in bad.items():
            db.run("""update jstore.entity set status = 'superseded',
                             attrs = attrs || jsonb_build_object('not_a_room', cast(:w as text))
                      where entity_id = :e and status = 'active'""", e=eid, w=why)
    return bad


def restore(db, key: str | None = None) -> int:
    """The spaces prune() took out, back in the views."""
    rows = db.run("""update jstore.entity set status = 'active', attrs = attrs - 'not_a_room'
                     where status = 'superseded' and attrs ? 'not_a_room'
                       and (cast(:k as text) is null or drawing_key = :k) returning entity_id""", k=key)
    return len(rows)
