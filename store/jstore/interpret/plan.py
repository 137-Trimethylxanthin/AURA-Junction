"""Layer 2 for plans: rooms, apartments, doors, windows and sanitary fixtures from the Layer 1 rows of one file.

    interpret(db, file)       a CAD file, from cad_entity
    interpret_pdf(db, file)   a vector PDF, from pdf_path / pdf_text (rooms and apartments only)

Nothing is read by name alone.  Every role is inferred per file and written to `definition`:
  rooms       the family of closed outlines (a layer, or a pen when everything shares a layer) whose members most
              often hold exactly one room-number text; a room is the smallest such outline around a number
  apartments  outlines of another family that hold an apartment tag and cover rooms
  units       the declared drawing unit, checked against printed areas and plausible room sizes
  symbols     three kinds of evidence, combined as 1 - prod(1 - e) and stored with their parts:
              lexicon   the layer name means the thing in one of the conventions of vocab.concepts
              blocks    a block reference is named like the thing (one INSERT = one item, attributes give the tag)
              geometry  the shapes behave like the thing: a quarter-circle swing hinged on a narrow opening (door),
                        a thin closed rectangle with glazing lines along it (window), a rounded bowl with or
                        without a drain, a long rounded basin, a lone drain (sanitary fixtures)
              Below MIN_CONFIDENCE no entities are created and the definition row says why.
Entities are written in the drawing's shared frame (metres); entity_source keeps handle and bbox in file coordinates.
"""
from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass, field

import numpy as np
from shapely import STRtree, from_wkb, unary_union
from shapely.affinity import affine_transform
from shapely.geometry import MultiPoint, Point, Polygon

from ..db import DB, arr
from . import define, issue, merge, vocab

MIN_CONFIDENCE = 0.5
IFC_CLASS = {"room": "IfcSpace", "apartment": "IfcZone", "door": "IfcDoor", "window": "IfcWindow"}
FIXTURES = ("toilet", "washbasin", "sink", "bathtub", "shower", "urinal")
TAG_ATTRIB = re.compile(r"^(mark|tag|number|num|no|nr|id|pos|name|code|typ|type)", re.I)
# Plausible sizes in metres (building practice, not this dataset).
DOOR_LEAF = (0.35, 1.8)          # radius of a door swing
DOOR_SWEEP = (40.0, 140.0)       # degrees
OPENING = dict(short=(0.02, 0.6), long=(0.4, 3.8))
WINDOW = dict(short=(0.03, 0.8), long=(0.25, 12.0))
DRAIN_R = (0.008, 0.045)
BOWL = (0.15, 1.3)               # both sides of a basin or pan
TUB = dict(short=(0.25, 1.1), long=(1.1, 2.3))
TRAY = (0.6, 1.7)


@dataclass(slots=True)
class Prim:
    ref: str
    kind: str                     # dxftype, or 'path' for PDF
    layer: str | None
    block: str | None
    parent: str | None
    style: tuple
    geom: object
    attrs: dict
    text: str | None = None
    ring: object = None           # Polygon when the primitive is a closed outline


@dataclass
class Ctx:
    f: dict
    prims: list[Prim]
    k: float = 1.0                # file unit -> metres
    m: list = field(default_factory=lambda: [1, 0, 0, 0, 1, 0])
    rooms: list = field(default_factory=list)       # (Polygon in file units, number, apartment)
    used_layers: set = field(default_factory=set)   # layers that hold room and apartment outlines

    def metres(self, g):
        a, b, c, d, e, f_ = self.m
        return affine_transform(g, [a, b, d, e, c, f_])


# ------------------------------------------------------------------ loading
def _ring(g, close: float = 1e-6):
    """A closed outline as Polygon: polygons, and polylines that end where they start (within `close` of their size)."""
    if g is None:
        return None
    if g.geom_type == "Polygon":
        pg = g
    elif g.geom_type == "LineString" and len(g.coords) >= 4:
        (x0, y0), (x1, y1) = g.coords[0][:2], g.coords[-1][:2]
        b = g.bounds
        if math.hypot(x1 - x0, y1 - y0) > close * max(b[2] - b[0], b[3] - b[1], 1e-12):
            return None
        pg = Polygon(g.coords)
    else:
        return None
    if not pg.is_valid:
        pg = pg.buffer(0)
        if pg.geom_type == "MultiPolygon":
            pg = max(pg.geoms, key=lambda q: q.area)
    return pg if pg.geom_type == "Polygon" and pg.area > 0 else None


def load_cad(db: DB, file_id: str) -> list[Prim]:
    rows = db.run("""select handle, parent, block, dxftype, layer, color, linetype, text, geom, attrs
                     from jstore.cad_entity where file_id = :f and space = 'model' order by id""", f=file_id)
    geoms = from_wkb([r["geom"] for r in rows]) if rows else []
    out = []
    for r, g in zip(rows, geoms):
        style = (r["layer"], (r["linetype"] or "BYLAYER").upper(), 256 if r["color"] is None else r["color"])
        p = Prim(r["handle"], r["dxftype"], r["layer"], r["block"], r["parent"], style, g, r["attrs"] or {}, r["text"])
        if r["dxftype"] in ("LWPOLYLINE", "POLYLINE", "HATCH", "CIRCLE", "ELLIPSE", "SPLINE", "SOLID"):
            p.ring = _ring(g) if r["dxftype"] != "HATCH" else None
        out.append(p)
    return out


def text_lines(prims: list[Prim]) -> list[tuple]:
    """(x, y, text, height, ref) per line of text; a multi-line MTEXT gives one entry per line at its own height."""
    out = []
    for p in prims:
        if p.kind not in ("TEXT", "MTEXT", "ATTRIB") or not p.text or p.geom is None or p.geom.geom_type != "Point":
            continue
        h = float(p.attrs.get("height") or 1.0)
        x, y = p.geom.x, p.geom.y
        lines = [s.strip() for s in re.split(r"[\n\r]+", p.text) if s.strip()]
        if p.kind != "MTEXT" or len(lines) == 1:
            out.extend((x, y, s, h, p.ref) for s in lines)
            continue
        pitch = 5 / 3 * h                                   # DXF default line pitch
        att = int(p.attrs.get("attachment") or 1)
        total = (len(lines) - 1) * pitch + h
        top = y if att <= 3 else (y + total / 2 if att <= 6 else y + total)
        out.extend((x, top - h / 2 - i * pitch, s, h, p.ref) for i, s in enumerate(lines))
    return out


# ------------------------------------------------------------------ rooms and apartments (any vector source)
def unit_factor(declared: float | None, room_areas: list[float], printed_pairs: list[tuple]) -> tuple[float, str]:
    """File unit -> metres, and how it was found."""
    f = declared
    if printed_pairs:      # printed area / measured area gives f squared directly: the strongest evidence
        fp = float(np.sqrt(np.median([p / g for p, g in printed_pairs if g > 0])))
        if f is None or abs(fp / f - 1) > 0.2:
            return fp, "printed_areas" if f is None else "printed_areas (declared unit contradicted)"
        return f, "declared, confirmed by printed areas"
    med = float(np.median(room_areas)) if room_areas else None
    if med and (f is None or not 0.5 <= med * f * f <= 200):
        # unknown or implausible: the metric unit that brings the median room closest to 10 m2
        g = min((1.0, 0.1, 0.01, 0.001, 0.0254, 0.3048), key=lambda g: abs(np.log(med * g * g / 10.0)))
        return g, "plausible_room_size"
    return (f, "declared") if f else (1.0, "assumed_metres")


def read_rooms(polys: list[tuple], texts: list[tuple], solids: list, declared: float | None) -> dict | None:
    """polys: (style, Polygon, ref); texts: (x, y, text, height, ref); solids: hatched regions (wall material).
    style[0] is the layer (or the stroke of a PDF path); the whole tuple is the pen.
    -> {"rooms": [...], "apartments": [...], "f", "f_how", "meta"} or None when there is nothing to read."""
    cls = [(x, y, *vocab.classify(s), h, ref, s) for x, y, s, h, ref in texts]     # x y kind value h ref raw
    nums = [c for c in cls if c[2] == "num"]
    if not polys or not nums:
        return None
    geoms = [p for _, p, _ in polys]
    tree = STRtree(geoms)
    npts = [Point(c[0], c[1]) for c in nums]
    hits = tree.query(npts, predicate="within")
    pairs = list(zip(*hits.tolist()))
    big = {(t, p) for t, p in pairs if geoms[p].area >= (2 * (nums[t][4] or 0)) ** 2}
    per_poly = collections.Counter(p for _, p in big)
    numbered = set(per_poly)

    def best_family(key):
        """The group whose outlines most often hold exactly one number, and the share of its outlines that hold a
        number at all (a dedicated room layer is near 1, a layer shared with walls and fixtures is low)."""
        sc = collections.Counter(key(polys[p][0]) for p, k in per_poly.items() if k == 1)
        if not sc:
            return None, 0.0
        fam = sc.most_common(1)[0][0]
        members = [i for i in range(len(polys)) if key(polys[i][0]) == fam]
        return fam, sum(i in numbered for i in members) / len(members)

    keyf, key_name = (lambda st: st[0]), "layer"
    fam, purity = best_family(keyf)
    if purity < 0.4:       # shared layer: try the full pen
        fam2, pur2 = best_family(lambda st: st)
        if pur2 > purity:
            keyf, key_name, fam, purity = (lambda st: st), "pen", fam2, pur2
    if fam is None:
        return None
    inpoly, polyof = collections.defaultdict(set), collections.defaultdict(set)
    infam = [(t, p) for t, p in pairs if keyf(polys[p][0]) == fam]
    has_big = {t for t, p in infam if (t, p) in big}
    for t, p in infam:     # a room smaller than its own text (a shaft) accepts any size
        if (t, p) in big or t not in has_big:
            inpoly[p].add(t)
            polyof[t].add(p)
    room_of = {}
    if purity >= 0.4:
        # clean family: the smallest outline around the number (a lift inside a stair hall keeps its own outline)
        for t, ps in polyof.items():
            p = min(ps, key=lambda p: geoms[p].area)
            if p not in room_of.values():
                room_of[t] = p
    else:
        # mixed family (fixtures, doors, walls on the same pen): the LARGEST outline around the number whose other
        # numbers all sit in nested outlines
        def valid(P, t):
            return all(any(Q != P and t not in inpoly[Q] and geoms[P].buffer(1e-6 * np.sqrt(geoms[P].area) + 1e-9)
                           .contains(geoms[Q]) for Q in polyof[u]) for u in inpoly[P] - {t})
        # a room is open along the inside of its outline; a wall ring drawn as one closed outline is hatched there
        solid = unary_union(solids) if solids else None

        def open_space(P):
            if solid is None:
                return True
            g_ = geoms[P]
            band = g_.intersection(g_.exterior.buffer(0.02 * np.sqrt(g_.area)))
            return band.area == 0 or band.intersection(solid).area < 0.25 * band.area

        ok = {P for ps in polyof.values() for P in ps if open_space(P)}

        def select(allowed):
            cands = {t: sorted((P for P in ps if P in allowed and valid(P, t)), key=lambda P: -geoms[P].area)
                     for t, ps in polyof.items()}
            pick = {t: 0 for t in cands if cands[t]}
            for _ in range(5):      # rooms partition the floor: overlapping picks step down to the next smaller
                changed = False
                ts = sorted(pick, key=lambda t: -geoms[cands[t][pick[t]]].area)
                cur = [geoms[cands[t][pick[t]]] for t in ts]
                if not cur:
                    break
                for a, b in zip(*STRtree(cur).query(cur, predicate="overlaps").tolist()):
                    A, B = cur[a], cur[b]
                    if a != b and A.intersection(B).area > 0.05 * min(A.area, B.area):
                        t = ts[a] if A.area >= B.area else ts[b]
                        if pick[t] + 1 < len(cands[t]):
                            pick[t] += 1
                            changed = True
                dup = collections.defaultdict(list)
                for t in pick:
                    dup[cands[t][pick[t]]].append(t)
                for ts_ in dup.values():
                    for t in ts_ if len(ts_) > 1 else []:
                        if pick[t] + 1 < len(cands[t]):
                            pick[t] += 1
                            changed = True
                if not changed:
                    break
            return {t: cands[t][pick[t]] for t in pick}

        pick = select(ok)
        pens = collections.Counter(polys[p][0] for p in pick.values())
        if len(pens) > 1:           # rooms share one pen: re-pick within the commonest
            pen = pens.most_common(1)[0][0]
            pick2 = select({P for P in ok if polys[P][0] == pen})
            pick = {t: pick2.get(t, p) for t, p in pick.items()}
        for t, p in sorted(pick.items()):
            if p not in room_of.values():
                room_of[t] = p
    room_idx = sorted(set(room_of.values()))
    if not room_idx:
        return None
    rgeoms = [geoms[p] for p in room_idx]
    rtree = STRtree(rgeoms)
    info = {p: dict(p=p, number=None, num_ref=None, label=None, label_ref=None, printed=None, area_ref=None, tags=[],
                    anchor=None) for p in room_idx}
    for t, p in room_of.items():
        info[p].update(number=nums[t][3], num_ref=nums[t][5], anchor=Point(nums[t][0], nums[t][1]))
    anchors = [info[p]["anchor"] for p in room_idx]
    atree = STRtree(anchors)
    # label, printed area, tag: the smallest room around the text; a text outside every room joins the room whose
    # number is within 3 text heights; per room the text nearest to the number wins
    best = {}
    for c in cls:
        x, y, kind, val, h = c[:5]
        if kind not in ("label", "area", "tag"):
            continue
        pt = Point(x, y)
        inside = rtree.query(pt, predicate="within")
        if len(inside):
            p = room_idx[min(inside, key=lambda i: rgeoms[i].area)]
        elif kind != "tag":
            i = atree.nearest(pt)
            if anchors[i].distance(pt) > 3 * (h or 1):
                continue
            p = room_idx[i]
        else:
            continue
        if kind == "tag":
            info[p]["tags"].append(val)
            continue
        d = pt.distance(info[p]["anchor"])
        if (p, kind) not in best or d < best[p, kind][0]:
            best[p, kind] = (d, val, c[5])
    for (p, kind), (_, val, ref) in best.items():
        info[p].update(dict(label=val, label_ref=ref) if kind == "label" else dict(printed=val, area_ref=ref))
    f, f_how = unit_factor(declared, [geoms[p].area for p in room_idx],
                           [(info[p]["printed"], geoms[p].area) for p in room_idx if info[p]["printed"]])
    # apartment outlines: non-room outlines that hold a tag text and cover at least one room
    tags = [c for c in cls if c[2] == "tag"]
    reps = [geoms[p].representative_point() for p in room_idx]
    units, outl, ufam = {}, set(), None
    tol = 0.05 / f
    if tags:
        room_set = set(room_idx)
        tpts = [Point(c[0], c[1]) for c in tags]
        th = tree.query(tpts, predicate="within")
        rh = tree.query(reps, predicate="within")
        rooms_in = collections.Counter(rh[1].tolist())
        # (an outline much larger than everything the rooms span is a sheet border, not an apartment)
        span = 1.5 * MultiPoint([c for g in rgeoms for c in g.exterior.coords]).convex_hull.area
        hold = [p for t, p in zip(*th.tolist()) if p not in room_set and rooms_in[p] and geoms[p].area <= span]
        ukey = lambda st: st[0]  # noqa: E731
        ul = collections.Counter(ukey(polys[p][0]) for p in hold)
        if ul and ul.most_common(1)[0][0] == polys[room_idx[0]][0][0]:
            ukey = lambda st: st  # noqa: E731  (shared with the rooms' layer: tell apart by pen)
            ul = collections.Counter(ukey(polys[p][0]) for p in hold)
        ufam = ul.most_common(1)[0][0] if ul else None
        shared = ukey(polys[room_idx[0]][0]) == ufam
        # outlines of that family covering most of a room (they may be inset by half a wall).  Unnamed ones count
        # too: an unnamed outline nested in a named one is a hole (a stair core), its rooms get no apartment
        for i, q in zip(*rh.tolist()):
            if q not in room_set and ukey(polys[q][0]) == ufam and geoms[q].area <= span and \
                    geoms[q].intersection(rgeoms[i]).area > 0.5 * rgeoms[i].area and \
                    (not shared or geoms[q].area > 1.2 * rgeoms[i].area):
                outl.add(q)
        loose = []
        for t, pt in enumerate(tpts):
            inside = [q for q in outl if geoms[q].contains(pt)]
            q = min(inside, key=lambda q: geoms[q].area) if inside else None
            if q is not None and q not in units:
                units[q] = t
            else:
                loose.append(t)
        for t in loose:      # a tag outside every outline names the nearest unnamed one within 3 m
            free = [q for q in outl if q not in units and
                    not any(geoms[u].buffer(tol).contains(geoms[q]) for u in units)]
            if free:
                q = min(free, key=lambda q: geoms[q].distance(tpts[t]))
                if geoms[q].distance(tpts[t]) * f < 3.0:
                    units[q] = t
    ulist = sorted(outl, key=lambda p: geoms[p].area)
    ubuf = {p: geoms[p].buffer(tol) for p in ulist}
    rooms = []
    for p, rp in zip(room_idx, reps):
        r = info[p]
        unit = next((tags[units[u]][3] if u in units else None for u in ulist if ubuf[u].contains(rp)), None)
        if unit is None and not units and len(set(r["tags"])) == 1:
            unit = r["tags"][0]                   # no outlines at all: the tag is written inside the room
        # net area: a room drawn inside this one (a lift in a stair hall) is not part of it
        inner = [q for q in (room_idx[i] for i in rtree.query(geoms[p], predicate="intersects"))
                 if q != p and geoms[q].area < geoms[p].area and geoms[q].intersection(geoms[p]).area > 0.5 * geoms[q].area]
        net = geoms[p].difference(unary_union([geoms[q] for q in inner])).area if inner else geoms[p].area
        rooms.append(dict(r, unit=unit, measured=net * f * f, nested=[info[q]["number"] for q in inner]))
    apartments = [dict(p=q, tag=tags[t][3], tag_ref=tags[t][5], tag_text=tags[t][6]) for q, t in units.items()]
    if not units:            # apartments known only from tags inside rooms
        seen = {}
        for c in tags:
            if any(r["unit"] == c[3] for r in rooms):
                seen.setdefault(c[3], c)
        apartments = [dict(p=None, tag=c[3], tag_ref=c[5], tag_text=c[6]) for c in seen.values()]
    kinds = collections.Counter(c[2] for c in cls)
    meta = dict(family=fam, key=key_name, purity=round(purity, 3), n_number_texts=len(nums), n_rooms=len(rooms),
                apartment_family=ufam, n_outlines=len(outl), n_named=len(units), n_tags=len(tags),
                texts=dict(kinds))
    return dict(rooms=rooms, apartments=apartments, f=f, f_how=f_how, meta=meta)


def _bbox(g, pad: float = 0.0) -> list[float]:
    b = g.bounds
    return [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad]


def room_records(res: dict, polys: list[tuple], text_at: dict, to_m, table: tuple[str, str], defs: dict,
                 measured_conf: float = 0.9, printed_conf: float = 0.98) -> list[dict]:
    """Records for the rooms and apartments of read_rooms.  table = (outline table, text table)."""
    recs = []
    t_out, t_txt = table

    def tsrc(ref, role, def_id):
        x, y, h = text_at[ref]
        return (t_txt, ref, role, [x - 2 * h, y - h, x + 2 * h, y + h], def_id)

    for r in res["rooms"]:
        g = polys[r["p"]][1]        # the outline as drawn; the measured claim excludes rooms nested in it
        src = [(t_out, polys[r["p"]][2], "outline", _bbox(g), defs["room"]), tsrc(r["num_ref"], "number", defs["room_text"])]
        claims = [("net_area_m2", round(r["measured"], 3), None, "m2", "measured_outline", 0, measured_conf)]
        if r["label_ref"]:
            src.append(tsrc(r["label_ref"], "label", defs["room_text"]))
        if r["area_ref"]:
            src.append(tsrc(r["area_ref"], "area_text", defs["room_text"]))
            claims.append(("net_area_m2", r["printed"], None, "m2", "printed", len(src) - 1, printed_conf))
        recs.append(dict(kind="room", ifc_class="IfcSpace", number=r["number"], label=r["label"],
                         label_norm=vocab.room_type(r["label"]), zone=r["unit"], geom=to_m(g),
                         attrs=dict(indoor=vocab.indoor(r["label"]), family=str(polys[r["p"]][0][0]),
                                    **({"encloses": r["nested"]} if r["nested"] else {})),
                         sources=src, claims=claims))
    for a in res["apartments"]:
        src, claims, g = [], [], None
        if a["p"] is not None:
            g = polys[a["p"]][1]
            src.append((t_out, polys[a["p"]][2], "outline", _bbox(g), defs["apartment"]))
            claims.append(("outline_area_m2", round(g.area * res["f"] ** 2, 3), None, "m2", "measured_outline", 0,
                           measured_conf))
        src.append(tsrc(a["tag_ref"], "tag", defs["apartment"]))
        recs.append(dict(kind="apartment", ifc_class="IfcZone", number=a["tag"], label=a["tag_text"],
                         geom=to_m(g) if g is not None else None, attrs={}, sources=src, claims=claims))
    return recs


def define_rooms(db: DB, f: dict, res: dict | None, source: str, hints=lambda fam: {}) -> dict:
    """Definition rows for room, room_text and apartment.  -> {concept: def_id}"""
    if res is None:
        d = define(db, f["file_id"], "room", "layer_role", dict(
            source=source, created=0, reason="no closed outline holds a room-number text (pattern like 2.01, U1.05)"), 0.0)
        return dict(room=d, room_text=d, apartment=d)
    m = res["meta"]
    cover = m["n_rooms"] / max(m["n_number_texts"], 1)
    out = {}
    fam = m["family"]
    out["room"] = define(db, f["file_id"], "room", "layer_role" if m["key"] == "layer" else "style_cluster", dict(
        source=source, family=fam, family_key=m["key"], lexicon=hints(fam),
        rule="closed outlines of this family; a room is the smallest outline around one room-number text"
             if m["purity"] >= 0.4 else
             "family shared with other linework; a room is the largest outline around a number whose other numbers "
             "sit in nested outlines and whose inner edge is free of wall hatch",
        purity=m["purity"], number_texts=m["n_number_texts"], created=m["n_rooms"]),
        round(min(0.99, (0.5 + 0.5 * m["purity"]) * min(cover, 1.0)), 3))
    out["room_text"] = define(db, f["file_id"], "room_text", "reader", dict(
        source=source, rule="number: letters? digits(.digits)+ ; printed area: value + m2/m²/qm ; apartment tag: "
                            "Whg./App./Apt./Unit ... + id ; any other short text without digits is the room name; "
                            "a text belongs to the smallest room around it, else to the number within 3 text heights",
        counts=m["texts"]), 0.9)
    ok = m["n_named"] > 0
    out["apartment"] = define(db, f["file_id"], "apartment", "layer_role", dict(
        source=source, family=m["apartment_family"], lexicon=hints(m["apartment_family"]),
        rule="outlines of this family that cover rooms; the apartment tag inside (or within 3 m) names the outline; "
             "a room belongs to the smallest outline around its interior point",
        outlines=m["n_outlines"], named=m["n_named"], tags=m["n_tags"], created=len(res["apartments"]),
        **({} if ok else {"reason": "no apartment tag text" if not m["n_tags"] else
                                    "tags found but no outline family covers rooms; tags inside rooms used"})),
        0.9 if ok else (0.5 if res["apartments"] else 0.0))
    return out


# ------------------------------------------------------------------ symbols: shared helpers
def _sides(pg) -> tuple[float, float, float, tuple]:
    """(short, long, fill, long-axis unit vector) of the minimum rotated rectangle; fill = area / rectangle area."""
    c = list(pg.minimum_rotated_rectangle.exterior.coords)
    e1 = (c[1][0] - c[0][0], c[1][1] - c[0][1])
    e2 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    l1, l2 = math.hypot(*e1), math.hypot(*e2)
    (s, v), (l, u) = sorted(((l1, e1), (l2, e2)), key=lambda z: z[0])
    return s, l, pg.area / max(s * l, 1e-30), (u[0] / max(l, 1e-30), u[1] / max(l, 1e-30))


def _combine(*e: float) -> float:
    return round(1 - math.prod(1 - min(max(x, 0.0), 0.99) for x in e), 3)


class Symbols:
    """Evidence gathering shared by doors, windows and fixtures of one CAD file."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.k = ctx.k
        self.tol = 0.03 / ctx.k
        prims = ctx.prims
        self.by_ref = {p.ref: p for p in prims}
        self.kids = collections.defaultdict(list)
        for p in prims:
            if p.parent:
                self.kids[p.parent].append(p)
        self.layer_hints = {l: vocab.concepts(l) for l in {p.layer for p in prims if p.layer}}
        self.block_hints = {b: vocab.concepts(b) for b in {p.block for p in prims if p.kind == "INSERT" and p.block}}
        self.inserts = [p for p in prims if p.kind == "INSERT"]
        self.shapes = [p for p in prims if p.kind not in ("INSERT", "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "HATCH",
                                                          "DIMENSION", "POINT", "VIEWPORT", "LEADER")
                       and p.geom is not None and not p.geom.is_empty]
        self.consumed: set[str] = set()

    def layers_for(self, concepts) -> dict:
        return {l: next(h[c] for c in concepts if c in h) for l, h in self.layer_hints.items()
                if any(c in h for c in concepts)}

    def other_layers(self, concepts) -> set:
        """Layers whose name says they hold something else (walls, dimensions, another symbol class ...)."""
        return {l for l, h in self.layer_hints.items() if h and not any(c in h for c in concepts)} | self.ctx.used_layers

    def descendants(self, ref: str) -> list[Prim]:
        out, stack = [], [ref]
        while stack:
            for c in self.kids.get(stack.pop(), ()):
                out.append(c)
                stack.append(c.ref)
        return out

    def named_inserts(self, concepts, layers: dict) -> list[tuple[Prim, str, dict, str]]:
        """Outermost block references named like one of the concepts, or lying on a layer named like them.
        -> (insert, concept, hint, 'block' | 'layer')"""
        out, taken = [], set()
        for p in self.inserts:               # parents come before the references nested in them
            if p.parent in taken:
                taken.add(p.ref)
                continue
            if p.ref in self.consumed:
                continue
            h = self.block_hints.get(p.block, {})
            c = next((c for c in concepts if c in h), None)
            if c:
                out.append((p, c, h[c], "block"))
            elif p.layer in layers and not layers[p.layer].get("part") and not h:
                out.append((p, concepts[0], layers[p.layer], "layer"))
            else:
                continue
            taken.add(p.ref)
        return out

    def scope(self, concepts, layers: dict) -> tuple[list[Prim], bool]:
        """Primitives to search by geometry: the layers named like the concept, else every layer not named otherwise."""
        if layers:
            return [p for p in self.shapes if p.layer in layers and p.ref not in self.consumed], True
        skip = self.other_layers(concepts)
        return [p for p in self.shapes if p.layer not in skip and p.ref not in self.consumed], False

    def consume(self, ins: Prim) -> list[Prim]:
        kids = self.descendants(ins.ref)
        self.consumed.update(k.ref for k in kids)
        self.consumed.add(ins.ref)
        return kids

    def tag_of(self, ins: Prim):
        """(tag text, attribute row) of a block reference: the first attribute named like a mark or number."""
        for k in self.kids.get(ins.ref, ()):
            if k.kind == "ATTRIB" and k.text and TAG_ATTRIB.match(str(k.attrs.get("tag") or "")):
                return k.text, k
        return None, None

    def host(self, g, reach: float):
        """(room number, apartment, all room numbers touched) for a symbol geometry in file units."""
        rooms = self.ctx.rooms
        if not rooms:
            return None, None, []
        if not hasattr(self, "_rtree"):
            self._rtree = STRtree([r[0] for r in rooms])
        zone = g.buffer(reach / self.k)
        hit = [(rooms[i][0].intersection(zone).area, i) for i in self._rtree.query(zone, predicate="intersects")]
        hit = sorted((h for h in hit if h[0] > 0), reverse=True)
        if not hit:
            return None, None, []
        r = rooms[hit[0][1]]
        return r[1], r[2], sorted({rooms[i][1] for _, i in hit if rooms[i][1]})


def _arc_points(p: Prim):
    a = p.attrs
    c, r = a["center"], a["radius"]
    pts = [(c[0] + r * math.cos(math.radians(a[k])), c[1] + r * math.sin(math.radians(a[k])))
           for k in ("start_angle", "end_angle")]
    return Point(c), [Point(q) for q in pts], (a["end_angle"] - a["start_angle"]) % 360


def _mark_duplicates(recs: list[dict]) -> None:
    """Things drawn twice stay two entities; the later copy points at the first."""
    first = {}
    for i, r in enumerate(recs):
        g = r["geom"]
        if g is None or r["kind"] in ("room", "apartment"):
            continue
        c = g.centroid
        key = (r["kind"], round(c.x, 3), round(c.y, 3), round(g.area, 4), round(g.length, 3))
        if key in first:
            r["duplicate"] = first[key]
        else:
            first[key] = i


def _src(p: Prim, role: str, def_id=None):
    return ("cad_entity", p.ref, role, _bbox(p.geom) if p.geom is not None and not p.geom.is_empty else None, def_id)


# ------------------------------------------------------------------ doors
def find_doors(db: DB, S: Symbols) -> list[dict]:
    ctx, k, tol = S.ctx, S.k, S.tol
    layers = S.layers_for(["door"])
    found = []          # dict(geom, width, parts=[(prim, role)], tag, via)
    named = S.named_inserts(["door"], layers)
    for ins, _, _, via in named:
        kids = S.consume(ins)
        shapes = [c for c in kids if c.geom is not None and not c.geom.is_empty and c.kind not in ("ATTRIB", "INSERT")]
        arcs = [c for c in shapes if c.kind == "ARC" and DOOR_LEAF[0] <= c.attrs.get("radius", 0) * k <= DOOR_LEAF[1]]
        width = None
        for key in ("WIDTH", "W", "BREITE", "LARGEUR", "LEVEYS"):
            v = next((v for a, v in (ins.attrs.get("attribs") or {}).items() if a.upper() == key), None)
            try:
                width = float(str(v).replace(",", ".")) * k if v else width
            except ValueError:
                pass
        if width is None and arcs:
            width = sum(a.attrs["radius"] for a in arcs) * k
        g = MultiPoint([q for c in shapes for q in _bbox_pts(c.geom)]).envelope if shapes else ins.geom
        tag, trow = S.tag_of(ins)
        found.append(dict(geom=g, width=width, parts=[(ins, "symbol")] + ([(trow, "tag")] if trow else []), tag=tag,
                          via=via, block=ins.block, swing=bool(arcs)))
    scope, by_name = S.scope(["door"], layers)
    # on a layer named for doors wide gates are accepted too; elsewhere only ordinary door sizes
    leaf_max, long_max = (2 * DOOR_LEAF[1], 2 * OPENING["long"][1]) if by_name else (DOOR_LEAF[1], OPENING["long"][1])
    arcs = []
    for p in scope:
        if p.kind == "ARC" and "radius" in p.attrs and DOOR_LEAF[0] <= p.attrs["radius"] * k <= leaf_max:
            c, ends, sweep = _arc_points(p)
            if DOOR_SWEEP[0] <= sweep <= DOOR_SWEEP[1]:
                arcs.append((p, c, ends))
    lines = [p for p in scope if p.kind != "ARC" and p.geom.geom_type in ("LineString", "Polygon")]
    edges = [p.ring.exterior if p.ring is not None else (p.geom.exterior if p.geom.geom_type == "Polygon" else p.geom)
             for p in lines]
    tree = STRtree(edges) if edges else None
    opening_of, loose, n_swing = collections.defaultdict(list), [], 0
    shape_cache = {}
    for a, c, ends in arcs:
        r = a.attrs["radius"]
        near = [i for i in tree.query(c.buffer(tol)) if edges[i].distance(c) <= tol] if tree is not None else []
        leaf = any(min(edges[i].distance(e) for e in ends) <= tol and edges[i].length <= 6.5 * r for i in near)
        ops = []
        for i in near:
            pg = lines[i].ring
            if pg is None:
                continue
            if i not in shape_cache:
                shape_cache[i] = _sides(pg)
            s, l, fill, _ = shape_cache[i]
            if fill >= 0.9 and OPENING["short"][0] <= s * k <= OPENING["short"][1] and \
                    OPENING["long"][0] <= l * k <= long_max and 0.7 * r <= l <= 2.6 * r:
                ops.append(i)
        if len(ops) > 1 and not by_name:
            # among unnamed linework wall stubs and frames sit at the hinge too: keep the outline whose length fits
            # the leaf best (and its exact copies)
            fit = lambda i: min(abs(shape_cache[i][1] / r - 1), abs(shape_cache[i][1] / r - 2))  # noqa: E731
            best = min(ops, key=fit)
            same = lambda i: all(abs(u - v) <= tol for u, v in zip(lines[i].ring.bounds, lines[best].ring.bounds))  # noqa: E731
            ops = [i for i in ops if same(i)]
        n_swing += bool(leaf or ops)
        for i in ops:
            opening_of[i].append(a)
        if not ops and leaf:
            loose.append((a, ends))
    for i, hinged in opening_of.items():
        s, l, _, _ = shape_cache[i]
        found.append(dict(geom=lines[i].ring, width=l * k, parts=[(lines[i], "outline")] + [(a, "symbol") for a in hinged],
                          tag=None, via="opening", leaves=len(hinged), swing=True))
    # swings without an opening outline: leaves whose arcs meet belong to one double door
    group = list(range(len(loose)))
    for i in range(len(loose)):
        for j in range(i):
            if min(p.distance(q) for p in loose[i][1] for q in loose[j][1]) <= tol:
                gi, gj = group[i], group[j]
                group = [gj if g == gi else g for g in group]
    for gid in sorted(set(group)):
        mem = [loose[i][0] for i in range(len(loose)) if group[i] == gid]
        found.append(dict(geom=unary_union([a.geom for a in mem]).convex_hull, width=sum(a.attrs["radius"] for a in mem) * k,
                          parts=[(a, "symbol") for a in mem], tag=None, via="swing", leaves=len(mem), swing=True))
    share = n_swing / len(arcs) if arcs else 0.0
    e_lex = max((h["weight"] for h in layers.values()), default=0.0)
    e_blk = 0.7 if any(v == "block" for *_, v in named) else 0.0
    # swings on unnamed layers need at least three to count; a swing inside a named block supports the name
    e_geo = 0.7 * share if (by_name and arcs) or n_swing >= 3 else (
        0.5 if any(d.get("swing") and d["via"] in ("block", "layer") for d in found) else 0.0)
    conf = _combine(e_lex, e_blk, e_geo)
    spec = dict(
        layers={l: h for l, h in layers.items()},
        blocks=sorted({d["block"] for d in found if d.get("block")})[:40],
        rule="one door per block reference named like a door or lying on a door layer; else one per narrow closed "
             "opening outline with a swing arc hinged on it (two leaves on one opening are one door); swings "
             "without an opening outline count once per group of arcs that meet",
        evidence=dict(lexicon=e_lex, blocks=e_blk, geometry=round(e_geo, 3)),
        geometry=dict(scope="named layers" if by_name else "all layers not named otherwise", arcs=len(arcs),
                      swings_with_leaf_or_opening=n_swing, openings=len(opening_of), loose_swings=len(loose)),
        via=dict(collections.Counter(d["via"] for d in found)))
    if not found:
        spec["reason"] = ("a layer is named like doors but holds no block reference, swing arc or opening outline"
                          if layers else "no layer or block named like a door and no door swing found")
        conf = min(conf, 0.3) if layers else 0.0
    elif conf < MIN_CONFIDENCE:
        spec["reason"] = f"{len(found)} candidate(s) from geometry alone, too little evidence to call them doors"
    spec["created"] = len(found) if conf >= MIN_CONFIDENCE else 0
    def_id = define(db, ctx.f["file_id"], "door", "layer_role" if layers else ("block_name" if e_blk else "geometry"),
                    spec, conf)
    if conf < MIN_CONFIDENCE:
        return []
    recs = []
    for d in found:
        S.consumed.update(p.ref for p, _ in d["parts"])
        hull = unary_union([p.geom for p, _ in d["parts"] if p.geom is not None]).convex_hull
        host, zone, touched = S.host(hull, 0.25)
        src = [_src(p, role, def_id) for p, role in d["parts"]]
        claims = [("width_m", round(d["width"], 3), None, "m", "measured_geometry", 0, 0.9)] if d["width"] else []
        recs.append(dict(kind="door", ifc_class="IfcDoor", number=d["tag"], host=host, zone=zone, geom=ctx.metres(d["geom"]),
                         attrs=dict(via=d["via"], rooms=touched, **({"leaves": d["leaves"]} if d.get("leaves") else {}),
                                    **({"block": d["block"]} if d.get("block") else {})),
                         sources=src, claims=claims))
    return recs


def _bbox_pts(g):
    b = g.bounds
    return [(b[0], b[1]), (b[2], b[3])]


# ------------------------------------------------------------------ windows
def find_windows(db: DB, S: Symbols) -> list[dict]:
    ctx, k, tol = S.ctx, S.k, S.tol
    layers = S.layers_for(["window"])
    found = []
    named = S.named_inserts(["window"], layers)
    for ins, _, _, via in named:
        kids = S.consume(ins)
        shapes = [c for c in kids if c.geom is not None and not c.geom.is_empty and c.kind not in ("ATTRIB", "INSERT")]
        g = MultiPoint([q for c in shapes for q in _bbox_pts(c.geom)]).envelope if shapes else ins.geom
        width = None
        if shapes and g.geom_type == "Polygon":
            width = _sides(g)[1] * k
        tag, trow = S.tag_of(ins)
        found.append(dict(geom=g, width=width, parts=[(ins, "symbol")] + ([(trow, "tag")] if trow else []), tag=tag,
                          via=via, block=ins.block))
    scope, by_name = S.scope(["window"], layers)
    frames = []
    for p in scope:
        if p.ring is not None and p.kind in ("LWPOLYLINE", "POLYLINE", "SOLID") and len(p.ring.exterior.coords) <= 9:
            s, l, fill, u = _sides(p.ring)
            if fill >= 0.95 and WINDOW["short"][0] <= s * k and l * k <= WINDOW["long"][1] and \
                    (by_name or (s * k <= WINDOW["short"][1] and l * k >= WINDOW["long"][0] and l >= 1.5 * s)):
                frames.append((p, s, l, u))
    segs = [p for p in scope if p.geom.geom_type == "LineString" and len(p.geom.coords) == 2 and p.kind != "ARC"]
    tree = STRtree([p.geom for p in segs]) if segs else None
    n_glazed, picked = 0, []
    for p, s, l, u in frames:
        glaz = []
        if tree is not None:
            for i in tree.query(p.ring.buffer(tol), predicate="contains"):
                (x0, y0), (x1, y1) = segs[i].geom.coords
                ln = math.hypot(x1 - x0, y1 - y0)
                if ln >= 0.6 * l and abs((x1 - x0) * u[0] + (y1 - y0) * u[1]) >= 0.95 * ln:
                    glaz.append(segs[i])
        n_glazed += bool(glaz)
        if by_name or (len(glaz) >= 2 and s * k <= 0.6):
            picked.append((p, l, glaz))
    for p, l, glaz in picked:
        found.append(dict(geom=p.ring, width=l * k, parts=[(p, "outline")] + [(g, "symbol") for g in glaz], tag=None,
                          via="frame", glazing_lines=len(glaz)))
    share = n_glazed / len(frames) if frames else 0.0
    e_lex = max((h["weight"] for h in layers.values()), default=0.0)
    e_blk = 0.7 if any(v == "block" for *_, v in named) else 0.0
    # frames with glazing lines are found on unnamed layers too, but stairs, counters and sills look the same:
    # without a name this stays below MIN_CONFIDENCE
    e_geo = 0.7 * share if by_name else (0.4 if len(picked) >= 3 else 0.0)
    conf = _combine(e_lex, e_blk, e_geo)
    spec = dict(
        layers=layers, blocks=sorted({d["block"] for d in found if d.get("block")})[:40],
        rule="one window per block reference named like a window or lying on a window layer; else one per thin "
             "closed rectangle (the frame); without a named layer the frame must hold two glazing lines along it",
        evidence=dict(lexicon=e_lex, blocks=e_blk, geometry=round(e_geo, 3)),
        geometry=dict(scope="named layers" if by_name else "all layers not named otherwise", thin_rectangles=len(frames),
                      with_glazing_line=n_glazed),
        via=dict(collections.Counter(d["via"] for d in found)))
    if not found:
        spec["reason"] = ("a layer is named like windows but holds no block reference or closed frame" if layers
                          else "no layer or block named like a window and no glazed frame found")
        conf = min(conf, 0.3) if layers else 0.0
    elif conf < MIN_CONFIDENCE:
        spec["reason"] = f"{len(found)} candidate(s) from geometry alone, too little evidence to call them windows"
    spec["created"] = len(found) if conf >= MIN_CONFIDENCE else 0
    def_id = define(db, ctx.f["file_id"], "window", "layer_role" if layers else ("block_name" if e_blk else "geometry"),
                    spec, conf)
    if conf < MIN_CONFIDENCE:
        return []
    recs = []
    for d in found:
        S.consumed.update(p.ref for p, _ in d["parts"])
        host, zone, touched = S.host(d["geom"], 0.5)
        claims = [("width_m", round(d["width"], 3), None, "m", "measured_geometry", 0, 0.9)] if d["width"] else []
        recs.append(dict(kind="window", ifc_class="IfcWindow", number=d["tag"], host=host, zone=zone,
                         geom=ctx.metres(d["geom"]),
                         attrs=dict(via=d["via"], rooms=touched, **({"block": d["block"]} if d.get("block") else {}),
                                    **({"glazing_lines": d["glazing_lines"]} if "glazing_lines" in d else {})),
                         sources=[_src(p, role, def_id) for p, role in d["parts"]], claims=claims))
    return recs


# ------------------------------------------------------------------ sanitary fixtures
RULES = {
    "toilet": "a rounded pan (smooth closed curve, 0.15-1.3 m across, at least 0.28 m long, filling its box like an ellipse) without a drain",
    "washbasin": "a rounded bowl (oval or circle, 0.1-1.3 m) with a small drain circle inside",
    "bathtub": "a rounded basin: smooth closed outline with rounded corners that fills its box, 1.1-2.3 m long; a "
               "shorter one counts when a drain lies in it (attrs.small)",
    "shower": "a drain circle that lies in no bowl and no tub, inside a tray outline (0.6-1.7 m) or at least inside a "
              "recognised room",
    "sink": "block references named like a sink only; no geometric rule",
    "urinal": "block references named like a urinal only; no geometric rule",
}


def find_fixtures(db: DB, S: Symbols) -> list[dict]:
    ctx, k, tol = S.ctx, S.k, S.tol
    concepts = ["fixture", *FIXTURES]
    layers = S.layers_for(concepts)
    found = []          # dict(kind, geom, parts, tag, via)
    named = S.named_inserts(list(FIXTURES), {})        # by block name only; a fixture layer does not say which kind
    for ins, kind, _, via in named:
        kids = S.consume(ins)
        shapes = [c for c in kids if c.geom is not None and not c.geom.is_empty and c.kind not in ("ATTRIB", "INSERT")]
        g = MultiPoint([q for c in shapes for q in _bbox_pts(c.geom)]).envelope if shapes else ins.geom
        tag, trow = S.tag_of(ins)
        found.append(dict(kind=kind, geom=g, parts=[(ins, "symbol")] + ([(trow, "tag")] if trow else []), tag=tag,
                          via="block", block=ins.block))
    scope, by_name = S.scope(concepts, layers)
    drains, bowls, tubs, rings = [], [], [], []
    for p in scope:
        if p.kind == "CIRCLE" and DRAIN_R[0] <= p.attrs.get("radius", 0) * k <= DRAIN_R[1]:
            drains.append(p)
            continue
        pg = p.ring if p.ring is not None else (_ring(p.geom) if p.kind in ("CIRCLE", "ELLIPSE", "SPLINE") else None)
        if pg is None:
            continue
        rings.append((p, pg))
        smooth = p.kind in ("CIRCLE", "ELLIPSE", "SPLINE") or len(pg.exterior.coords) >= 13
        if not smooth:
            continue
        s, l, fill, _ = _sides(pg)
        if 0.1 <= s * k and l * k <= BOWL[1] and 0.70 <= fill <= 0.86:
            bowls.append((p, pg))
        elif fill >= 0.9 and TUB["short"][0] <= s * k <= TUB["short"][1] and l * k <= TUB["long"][1]:
            tubs.append((p, pg))

    def outer(items):          # a rim drawn inside a rim is the same fixture
        keep = []
        for p, pg in sorted(items, key=lambda z: -z[1].area):
            if not any(q.buffer(tol).contains(pg) and pg.area < 0.9 * q.area for _, q in keep):
                keep.append((p, pg))
        return keep

    # a rounded basin shorter than a bathtub counts only with a drain in it
    small = {p.ref for p, pg in tubs if _sides(pg)[1] * k < TUB["long"][0]}
    tubs = [(p, pg) for p, pg in outer(tubs)
            if p.ref not in small or any(pg.buffer(tol).contains(d.geom.centroid) for d in drains)]
    # a plain circle is too common a shape to be a pan on its own: it counts only with a drain in it
    # and a very small oval likewise
    bowls = [(p, pg) for p, pg in outer(bowls) if not any(q.buffer(tol).contains(pg) for _, q in tubs)
             and ((p.kind != "CIRCLE" and BOWL[0] <= _sides(pg)[0] * k and _sides(pg)[1] * k >= 0.28)
                  or any(pg.contains(d.geom.centroid) for d in drains))]
    free = list(drains)

    def take(pg):
        zone = pg.buffer(tol)
        inside = [d for d in free if zone.contains(d.geom.centroid)]
        for d in inside:
            free.remove(d)
        return inside

    for p, pg in tubs:
        found.append(dict(kind="bathtub", geom=pg, parts=[(p, "outline")] + [(d, "symbol") for d in take(pg)], tag=None,
                          via="shape", **({"small": True} if p.ref in small else {})))
    for p, pg in bowls:
        dr = take(pg)
        found.append(dict(kind="washbasin" if dr else "toilet", geom=pg,
                          parts=[(p, "outline")] + [(d, "symbol") for d in dr], tag=None, via="shape"))
    if free and (tubs or bowls or by_name):
        cand = [(p, pg) for p, pg in rings if all(TRAY[0] <= v * k <= TRAY[1] for v in _sides(pg)[:2])]
        ttree = STRtree([pg for _, pg in cand]) if cand else None
        for d in free:
            c = d.geom.centroid
            trays = sorted((cand[i] for i in ttree.query(c, predicate="within")), key=lambda z: z[1].area) if ttree else []
            if not trays and S.host(c, 0.05)[0] is None:
                continue                     # neither a tray around it nor a room it lies in: just a small circle
            parts = [(d, "symbol")] + ([(trays[0][0], "outline")] if trays else [])
            found.append(dict(kind="shower", geom=trays[0][1] if trays else d.geom, parts=parts, tag=None, via="shape",
                              tray=bool(trays)))
    classes = {d["kind"] for d in found if d["via"] == "shape"}
    e_lex = max((h["weight"] for h in layers.values()), default=0.0)
    e_geo = min(0.7, 0.35 * len(classes))
    recs = []
    counts = collections.Counter(d["kind"] for d in found)
    for kind in FIXTURES:
        mine = [d for d in found if d["kind"] == kind]
        e_blk = 0.7 if any(d["via"] == "block" for d in mine) else 0.0
        conf = _combine(e_lex if any(d["via"] == "shape" for d in mine) else 0.0, e_blk,
                        e_geo if any(d["via"] == "shape" for d in mine) else 0.0) if mine else 0.0
        spec = dict(layers=layers, blocks=sorted({d["block"] for d in mine if d.get("block")})[:40], rule=RULES[kind],
                    evidence=dict(lexicon=e_lex, blocks=e_blk, geometry=e_geo),
                    geometry=dict(scope="named layers" if by_name else "all layers not named otherwise",
                                  drains=len(drains), bowls=len(bowls), tubs=len(tubs),
                                  classes_seen=sorted(classes)),
                    via=dict(collections.Counter(d["via"] for d in mine)))
        if not mine:
            spec["reason"] = "no block named like it and no such shape" + (" on the fixture layers" if layers else "")
        elif conf < MIN_CONFIDENCE:
            spec["reason"] = f"{len(mine)} candidate shape(s) but no named layer and fewer than two kinds of fixture"
        spec["created"] = len(mine) if conf >= MIN_CONFIDENCE else 0
        def_id = define(db, ctx.f["file_id"], kind, "layer_role" if layers else ("block_name" if e_blk else "geometry"),
                        spec, conf)
        if conf < MIN_CONFIDENCE:
            continue
        for d in mine:
            S.consumed.update(p.ref for p, _ in d["parts"])
            host, zone, _ = S.host(d["geom"].centroid, 0.3)
            recs.append(dict(kind=kind, ifc_class="IfcSanitaryTerminal", number=d["tag"], host=host, zone=zone,
                             geom=ctx.metres(d["geom"]),
                             attrs=dict(via=d["via"], **({"block": d["block"]} if d.get("block") else {}),
                                        **{x: d[x] for x in ("tray", "small") if x in d}),
                             sources=[_src(p, role, def_id) for p, role in d["parts"]], claims=[]))
    return recs


# ------------------------------------------------------------------ rooms without a name: indoor or not
OPEN_SHARE = 0.45        # share of a room's surroundings (0.9 m out) that is no room


def infer_outdoor(db: DB, f: dict, recs: list[dict]) -> None:
    """A room without a name (too small to print it) may still be a balcony.  Rule: most of what surrounds it is
    open space and it has no window of its own to the outside.  The rule is checked on the named rooms of the same
    file and applied to the unnamed ones only when it gets at least 80 % of the named ones right."""
    rooms = [r for r in recs if r["kind"] == "room" and r.get("geom") is not None]
    wins = [r["geom"] for r in recs if r["kind"] == "window" and r.get("geom") is not None]
    unnamed = [r for r in rooms if not r["label"]]
    spec = dict(rule=f"unnamed room, at least {OPEN_SHARE:.0%} of the points 0.9 m outside its outline lie in no room, "
                     "and no window that borders only this room", applied=False, marked=[])
    if not unnamed or not wins or len(rooms) == len(unnamed):
        spec["reason"] = ("no unnamed rooms" if not unnamed else
                          "no windows recognised" if not wins else "no named rooms to check the rule on")
        define(db, f["file_id"], "outdoor", "geometry", spec, 0.0)
        return
    geoms = [r["geom"] for r in rooms]
    tree = STRtree(geoms)
    floor = unary_union([g.buffer(0.02) for g in geoms])
    own = collections.Counter()
    for w in wins:
        zone = w.buffer(0.45)
        hit = [i for i in tree.query(zone) if geoms[i].intersection(zone).area > 0.02]
        if len(hit) == 1:
            own[hit[0]] += 1

    def open_air(i):
        ring = geoms[i].buffer(0.9).exterior
        n = max(int(geoms[i].exterior.length / 0.1), 8)
        outside = sum(not floor.contains(ring.interpolate(t, normalized=True)) for t in np.linspace(0, 1, n, endpoint=False))
        return outside / n >= OPEN_SHARE and own[i] == 0

    guess = [open_air(i) for i in range(len(rooms))]
    named = [i for i, r in enumerate(rooms) if r["label"]]
    agree = sum(guess[i] == (not rooms[i]["attrs"]["indoor"]) for i in named) / len(named)
    spec.update(named_rooms=len(named), agreement_on_named_rooms=round(agree, 3), applied=agree >= 0.8)
    if agree >= 0.8:
        for i, r in enumerate(rooms):
            if not r["label"] and guess[i]:
                r["attrs"].update(indoor=False, indoor_basis="geometry: open surroundings, no window of its own")
                spec["marked"].append(r["number"])
    else:
        spec["reason"] = "the rule misjudges too many named rooms of this file"
    define(db, f["file_id"], "outdoor", "geometry", spec, round(0.8 * agree, 3) if agree >= 0.8 else 0.2)


# ------------------------------------------------------------------ one CAD file
def _door_unit(prims: list[Prim]) -> float | None:
    """Unit guess from door swings when nothing else is known: the unit that makes the median leaf about 0.85 m."""
    r = [p.attrs["radius"] for p in prims if p.kind == "ARC" and p.attrs.get("radius")
         and 60 <= (p.attrs.get("end_angle", 0) - p.attrs.get("start_angle", 0)) % 360 <= 120]
    if len(r) < 3:
        return None
    med = float(np.median(r))
    g = min((1.0, 0.1, 0.01, 0.001, 0.0254, 0.3048), key=lambda g: abs(math.log(med * g / 0.85)))
    return g if 0.5 <= med * g <= 1.3 else None


def interpret(db: DB, f: dict) -> dict:
    """Layer 2 for one CAD file.  -> merge() counts"""
    fid = f["file_id"]
    prims = load_cad(db, fid)
    frame = db.one("select m, method from jstore.frame where file_id = :f and page_no = 0", f=fid)
    declared = frame["m"][0] if frame and frame["method"] != "assumed_metres" else None
    texts = text_lines(prims)
    polys = [(p.style, p.ring, p.ref) for p in prims if p.ring is not None and p.kind in ("LWPOLYLINE", "POLYLINE")]
    hatch = [p for p in prims if p.kind == "HATCH" and p.geom is not None and p.geom.geom_type in ("Polygon", "MultiPolygon")]
    solids = [p.geom if p.geom.is_valid else p.geom.buffer(0) for p in hatch]
    res = read_rooms(polys, texts, solids, declared) if polys else None
    if res is None or len(res["rooms"]) < 0.5 * res["meta"]["n_number_texts"]:
        # outlines may exist as hatch boundaries only
        hp = [(p.style, q, p.ref) for p in hatch
              for q in (p.geom.geoms if p.geom.geom_type == "MultiPolygon" else [p.geom]) if q.is_valid and q.area > 0]
        res2 = read_rooms(polys + hp, texts, solids, declared) if hp else None
        if res2 and len(res2["rooms"]) > (len(res["rooms"]) if res else 0):
            res, polys = res2, polys + hp
    if res:
        k, how = res["f"], res["f_how"]
    else:
        g = _door_unit(prims) if declared is None else None
        k, how = (declared, "declared") if declared else ((g, "plausible_door_width") if g else (1.0, "assumed_metres"))
    m = list(frame["m"]) if frame and declared and abs(k / declared - 1) < 1e-9 else [k, 0, 0, 0, k, 0]
    if frame and declared is None and how != "assumed_metres":
        db.run("""update jstore.frame set m = cast(:m as double precision[]), method = 'plausibility'
                  where file_id = :f and page_no = 0""", m=arr(m), f=fid)
    define(db, fid, "units", "reader", dict(
        to_metres=k, how=how, declared=f["meta"].get("units"), declared_to_metres=declared,
        rule="declared drawing unit; overridden when printed room areas contradict it by more than 20 %; without a "
             "declaration the unit that makes room areas (or door leaves) plausible"),
        0.95 if "declared" in how or how.startswith("printed") else (0.6 if how.startswith("plausible") else 0.2))
    ctx = Ctx(f, prims, k, m)
    S = Symbols(ctx)
    hints = lambda fam: S.layer_hints.get(fam if isinstance(fam, str) else (fam[0] if fam else None), {})  # noqa: E731
    defs = define_rooms(db, f, res, "cad", hints)
    recs = []
    if res:
        text_at = {ref: (x, y, h) for x, y, _, h, ref in texts}
        recs = room_records(res, polys, text_at, ctx.metres, ("cad_entity", "cad_entity"), defs)
        ctx.rooms = [(polys[r["p"]][1], r["number"], r["unit"]) for r in res["rooms"]]
        ctx.used_layers = {polys[r["p"]][0][0] for r in res["rooms"]} | \
                          {polys[a["p"]][0][0] for a in res["apartments"] if a["p"] is not None}
        if res["meta"]["purity"] < 0.4:      # a layer shared with other linework cannot be left out of the symbol search
            ctx.used_layers = set()
        S.consumed.update(polys[r["p"]][2] for r in res["rooms"])
        S.consumed.update(polys[a["p"]][2] for a in res["apartments"] if a["p"] is not None)
    for finder in (find_doors, find_windows, find_fixtures):
        try:
            recs += finder(db, S)
        except Exception as e:  # noqa: BLE001 - rooms must survive a symbol reader that trips over odd geometry
            issue(db, f, "interpret_failed", f"{finder.__name__} on {f['path']}: {e!r}")
    infer_outdoor(db, f, recs)
    _mark_duplicates(recs)
    return merge(db, f, recs, registered=bool(frame and frame["method"] != "assumed_metres"))


# ------------------------------------------------------------------ one PDF file
def join_words(rows: list[dict]) -> list[tuple]:
    """pdf_text rows (one per word) -> (x, y, text, size, ref) per text of a plan.  Neighbouring words on one line
    are joined while the result still reads as one printed area, apartment tag or room name ("15.6" + "m²",
    "Whg." + "A", "Chambre" + "à" + "coucher"); a number next to a name stays apart."""
    def line_key(r):
        size = r["size"] or (r["y1"] - r["y0"]) or 1.0
        return (r.get("line_no"), 0) if r.get("line_no") is not None else (None, round((r["y0"] + r["y1"]) / size))
    rows = sorted((r for r in rows if (r["text"] or "").strip()), key=lambda r: (line_key(r), r["x0"]))
    out, cur = [], None
    for r in rows:
        t = r["text"].strip()
        size = r["size"] or (r["y1"] - r["y0"]) or 1.0
        if cur and cur["key"] == line_key(r) and -0.3 * size <= r["x0"] - cur["x1"] <= 1.5 * size and \
                vocab.classify(cur["text"] + " " + t)[0] in ("area", "tag", "label"):
            cur.update(text=cur["text"] + " " + t, x1=r["x1"])
            continue
        if cur:
            out.append(cur)
        cur = dict(key=line_key(r), x0=r["x0"], x1=r["x1"], y=(r["y0"] + r["y1"]) / 2, text=t, size=size, ref=str(r["id"]))
    if cur:
        out.append(cur)
    return [((c["x0"] + c["x1"]) / 2, c["y"], c["text"], c["size"], c["ref"]) for c in out]


def interpret_pdf(db: DB, f: dict, page_no: int = 0) -> dict:
    """Rooms and apartments of one vector PDF page, with the same logic as CAD; the stroke style stands in for the layer."""
    fid = f["file_id"]
    paths = db.run("""select id, stroke, fill, width, dash, geom from jstore.pdf_path
                      where file_id = :f and page_no = :p and geom is not null order by seq""", f=fid, p=page_no)
    words = db.run("select * from jstore.pdf_text where file_id = :f and page_no = :p", f=fid, p=page_no)
    polys, solids = [], []
    for r, g in zip(paths, from_wkb([r["geom"] for r in paths]) if paths else []):
        pg = _ring(g, 2e-3) if g is not None and g.geom_type in ("Polygon", "LineString") else None
        if pg is None:
            continue
        if r["stroke"]:
            polys.append(((f"stroke {r['stroke']} width {r['width']} dash {r['dash'] or '-'}", r["fill"]), pg, str(r["id"])))
        elif r["fill"]:
            solids.append(pg)
    texts = join_words(words)
    frame = db.one("select m, method from jstore.frame where file_id = :f and page_no = :p", f=fid, p=page_no)
    scale = math.sqrt(abs(frame["m"][0] * frame["m"][4] - frame["m"][1] * frame["m"][3])) if frame else None
    if scale is None:        # the scale printed on the page: 1 pt on paper = 0.0254 / 72 m
        page = db.one("select meta from jstore.page where file_id = :f and page_no = :p", f=fid, p=page_no)
        ratios = {s_["ratio"] for s_ in ((page or {}).get("meta") or {}).get("scales", []) if s_.get("kind") == "metric"}
        if len(ratios) == 1:
            scale = ratios.pop() * 0.0254 / 72
    res = read_rooms(polys, texts, solids, scale) if polys else None
    defs = define_rooms(db, f, res, "pdf")
    if res is None:
        return dict(new=0, matched=0, unlinked=0)
    k = res["f"]
    m = list(frame["m"]) if frame and abs(k / scale - 1) < 0.05 else [k, 0, 0, 0, -k, 0]     # y is down in a PDF
    registered = bool(frame) and m == list(frame["m"])
    define(db, fid, "units", "reader", dict(to_metres_per_point=k, how=res["f_how"], frame=frame["method"] if frame else None,
                                            rule="frame of the page, else the scale printed on it, checked against "
                                                 "printed areas over outline areas"),
           0.9 if frame or "printed" in res["f_how"] else (0.8 if scale else 0.4))
    to_m = lambda g: affine_transform(g, [m[0], m[1], m[3], m[4], m[2], m[5]])  # noqa: E731
    text_at = {ref: (x, y, h) for x, y, _, h, ref in texts}
    recs = room_records(res, polys, text_at, to_m, ("pdf_path", "pdf_text"), defs, measured_conf=0.85)
    for r in recs:
        r["page_no"] = page_no
    return merge(db, f, recs, registered=registered)
