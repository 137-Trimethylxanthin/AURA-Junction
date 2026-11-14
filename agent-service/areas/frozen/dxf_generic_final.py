"""Generic DXF area reader: infers layer roles at run time instead of relying on layer names.

Pipeline (no layer names, no renderer constants):
  1. Flatten modelspace: explode INSERTs (block geometry + ATTRIBs), keep closed polylines (LWPOLYLINE, POLYLINE,
     optionally HATCH boundaries) and text lines (TEXT, MTEXT split on paragraphs with formatting codes stripped, ATTRIB).
  2. Classify each text line: room number (e.g. 2.01, U1.05, 12.3), printed area ("15.6 m²"), apartment tag
     ("Whg. A", "App. B", "Appartement C", "Appartamento D", "Unit E" ...), else room label.
  3. Room layer = the layer with most closed polylines containing exactly one room-number text.  Within it a room is a
     closed polygon that contains exactly one number text and is the smallest such polygon around that number
     (works also when every entity is merged on layer "0").
  4. Units: $INSUNITS -> metres; sanity-checked against plausible room sizes (median 2-60 m2) and printed areas.
  5. Apartment outlines = closed polygons (any layer, not rooms) containing an apartment tag and >= 1 room; the tag
     names the smallest one around it; a room belongs to the smallest named outline containing its interior point.
  6. Room records -> areas.common.answer_all (printed area preferred over measured, like gold).
"""
import collections
import os
import re
import sys

import ezdxf
import numpy as np
from shapely import STRtree, unary_union
from shapely.geometry import Point, Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from areas import common  # noqa: E402

# --- text vocabulary (domain knowledge, multilingual) ---
NUM = re.compile(r"^(?:[A-Z]{1,2}[ -]?)?\d{1,3}(?:\.\d{1,3})+$")          # 2.01, U1.05, A 2.10, 1.2.3
AREA = re.compile(r"^(?:(?:NF|NGF|HNF|F|A|Fl\.?|Surf\.?|Sup\.?)\s*[:=]?\s*)?(\d+(?:[.,']\d+)?)\s*(m2|m²|m\^2|qm|sqm)\s*$", re.I)
TAG = re.compile(r"^(?i:Whg|Wohnung|Wohneinheit|WE|App|Appt|Apt|Appartement|Appartamento|Apartment|Appart|Logement|"
                 r"Log|Unit|Einheit|Unité|Unità|Alloggio)\.?\s*(?i:Nr\.?|No\.?|n°|N\.)?\s*([A-Z]{1,3}|\d{1,3})$")
UNIT_TO_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1, 3: 1609.344, 7: 1000.0, 10: 0.9144}


def _mtext_lines(e):
    try:
        txt = e.plain_text(split=False)
    except Exception:
        txt = e.dxf.get("text", "")
    return [l.strip() for l in re.split(r"[\n\r]+|\\P", txt) if l.strip()]


def _anchor(e):
    """Text anchor in WCS: TEXT uses align_point when aligned, else insert."""
    t = e.dxftype()
    if t in ("TEXT", "ATTRIB") and (e.dxf.get("halign", 0) or e.dxf.get("valign", 0)) and e.dxf.hasattr("align_point"):
        p = e.dxf.align_point
    else:
        p = e.dxf.insert
    return float(p[0]), float(p[1])


def _closed_pts(e):
    t = e.dxftype()
    try:
        if t == "LWPOLYLINE":
            pts = [(x, y) for x, y in e.get_points("xy")]
            closed = e.closed
        elif t == "POLYLINE":
            if not (e.is_2d_polyline or e.is_3d_polyline):
                return None
            pts = [(v.dxf.location[0], v.dxf.location[1]) for v in e.vertices]
            closed = e.is_closed
        else:
            return None
    except Exception:
        return None
    if len(pts) >= 2 and np.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
        closed, pts = True, pts[:-1]
    return pts if closed and len(pts) >= 3 else None


def _style(e):
    """Pen signature: (layer, linetype, colour, lineweight).  Layer alone is the usual role key; the rest separates
    roles when everything sits on one layer but entities carry explicit styles."""
    return (e.dxf.layer, str(e.dxf.get("linetype", "BYLAYER")).upper(), e.dxf.get("color", 256),
            e.dxf.get("lineweight", -1), e.dxf.get("true_color", None))


def _flatten(space, depth=0):
    """Yield primitive entities with INSERTs exploded (attributes kept as text entities)."""
    for e in space:
        if e.dxftype() == "INSERT":
            for a in getattr(e, "attribs", []):
                yield a
            if depth < 4:
                try:
                    yield from _flatten(e.virtual_entities(), depth + 1)
                except Exception:
                    pass
        else:
            yield e


def read(path, use_hatch=False):
    doc = ezdxf.readfile(path)
    polys, texts, hatches = [], [], []  # polys: (layer, Polygon); texts: (x, y, text, height)
    for e in _flatten(doc.modelspace()):
        t = e.dxftype()
        if t in ("TEXT", "ATTRIB"):
            s = (e.dxf.get("text", "") or "").strip()
            if s:
                x, y = _anchor(e)
                texts.append((x, y, s, e.dxf.get("height", 1.0)))
        elif t == "MTEXT":
            x, y = _anchor(e)
            lines = _mtext_lines(e)
            h = e.dxf.get("char_height", 1.0)
            ls = 5 / 3 * h * e.dxf.get("line_spacing_factor", 1.0)  # DXF default line pitch
            n, att = len(lines), e.dxf.get("attachment_point", 1)
            tot = (n - 1) * ls + h
            top = y if att <= 3 else (y + tot / 2 if att <= 6 else y + tot)
            for i, s in enumerate(lines):  # each line at its own (approximate) centre height
                texts.append((x, top - h / 2 - i * ls, s, h))
        elif t in ("LWPOLYLINE", "POLYLINE"):
            pts = _closed_pts(e)
            if pts:
                pg = Polygon(pts).buffer(0)
                if pg.area > 0:
                    polys.append((_style(e), pg))
        elif t == "HATCH":  # hatch region (even-odd over its boundary paths): wall poche, or room fills
            pgs = []
            for bp in e.paths:
                if hasattr(bp, "vertices"):
                    pts = [(v[0], v[1]) for v in bp.vertices]
                else:  # edge path: chain the edge start points (arcs approximated by their chords)
                    pts = [(ed.start[0], ed.start[1]) for ed in getattr(bp, "edges", []) if hasattr(ed, "start")]
                if len(pts) >= 3:
                    pg = Polygon(pts).buffer(0)
                    if pg.area > 0:
                        pgs.append(pg)
            if pgs:
                reg = pgs[0]
                for pg in pgs[1:]:
                    reg = reg.symmetric_difference(pg)
                hatches.append(reg)
                if use_hatch:
                    polys.extend((_style(e), pg) for pg in pgs)
    return doc, polys, texts, hatches


def classify(s):
    s = s.strip()
    m = AREA.match(s)
    if m:
        return "area", float(m.group(1).replace(",", ".").replace("'", "."))
    m = TAG.match(s)
    if m:
        return "tag", m.group(1).upper()
    if NUM.match(s):
        return "num", s
    if re.search(r"[A-Za-zÀ-ÿ]", s) and not re.search(r"\d", s) and len(s) <= 40:
        return "label", s
    return "other", s


def unit_factor(doc, room_areas, printed_pairs):
    """Drawing unit -> metres.  $INSUNITS first; overridden if rooms come out implausibly sized."""
    f = UNIT_TO_M.get(doc.header.get("$INSUNITS", 0))
    if printed_pairs:  # printed area / measured area gives f^2 directly: strongest evidence
        r = np.median([p / g for p, g in printed_pairs if g > 0])
        fp = float(np.sqrt(r))
        if f is None or abs(fp / f - 1) > 0.2:
            return fp
        return f
    med = float(np.median(room_areas)) if room_areas else None
    if med and (f is None or not 0.5 <= med * f * f <= 200):
        # unknown / implausible units: the metric unit that makes the median room closest to ~10 m2
        return min((1.0, 0.1, 0.01, 0.001), key=lambda g: abs(np.log(med * g * g / 10.0)))
    return f or 1.0


def build_rooms(path, use_hatch=False):
    doc, polys, texts, hatches = read(path, use_hatch)
    cls = [(x, y, *classify(s), h) for x, y, s, h in texts]
    nums = [c for c in cls if c[2] == "num"]
    if not polys or not nums:
        return [], {}
    geoms = [p for _, p in polys]
    tree = STRtree(geoms)
    npts = [Point(c[0], c[1]) for c in nums]
    # polygons containing each number text
    hits = tree.query(npts, predicate="within")  # [0]=point idx, [1]=poly idx
    big = {(t, p) for t, p in zip(*hits.tolist()) if geoms[p].area >= (2 * (nums[t][4] or 0)) ** 2}
    per_poly = collections.Counter(p for _, p in big)
    numbered = set(per_poly)

    def best_family(key):
        """room family = the group whose polygons most often hold exactly one number; purity = share of its
        polygons that hold a number at all (a dedicated room layer ~1, a merged layer with walls/fixtures low)"""
        sc = collections.Counter(key(polys[p][0]) for p, k in per_poly.items() if k == 1)
        if not sc:
            return None, 0.0
        fam = sc.most_common(1)[0][0]
        members = [i for i in range(len(polys)) if key(polys[i][0]) == fam]
        return fam, sum(i in numbered for i in members) / len(members)

    keyf = lambda st: st[0]  # noqa: E731  (layer)
    fam, purity = best_family(keyf)
    if purity < 0.4:  # merged layer: try the full pen signature
        fam2, pur2 = best_family(lambda st: st)
        if pur2 > purity:
            keyf, fam, purity = (lambda st: st), fam2, pur2  # noqa: E731
    room_layer = fam
    inpoly, polyof = collections.defaultdict(set), collections.defaultdict(set)
    infam = [(t, p) for t, p in zip(*hits.tolist()) if keyf(polys[p][0]) == fam]
    has_big = {t for t, p in infam if (t, p) in big}
    for t, p in infam:  # rooms smaller than their own text (a 0.2 m2 shaft) accept any size
        if (t, p) in big or t not in has_big:
            inpoly[p].add(t)
            polyof[t].add(p)
    room_of = {}
    if purity >= 0.4:
        # clean room family: a number's room = the smallest family polygon around it; nested rooms (a lift inside a
        # stair hall) are fine because each number takes its own smallest polygon
        for t, ps in polyof.items():
            p = min(ps, key=lambda p: geoms[p].area)
            if p not in room_of.values():
                room_of[t] = p
    else:
        # mixed family (fixtures, doors, walls, apartment outlines on the same layer and pen): a room is the LARGEST
        # polygon around its number whose other numbers all sit in nested sub-polygons (fixtures nest inside rooms)
        def valid(P, t):
            return all(any(Q != P and t not in inpoly[Q] and geoms[P].buffer(1e-6 * np.sqrt(geoms[P].area) + 1e-9)
                           .contains(geoms[Q]) for Q in polyof[u]) for u in inpoly[P] - {t})
        # a room is bounded by walls from outside: the inner band along its outline is open space.  A wall ring drawn
        # as one closed outline (or an apartment outline crossing partitions) has hatched wall material in that band.
        # Hatched islands well inside (columns, shaft walls of a lift in a stair hall) do not count.
        solid = unary_union(hatches) if hatches else None

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
            # rooms partition the floor: a pick that partly overlaps another number's pick (apartment outline, wall
            # ring) steps down to its next smaller candidate
            for _ in range(5):
                changed = False
                ts = sorted(pick, key=lambda t: -geoms[cands[t][pick[t]]].area)
                cur = [geoms[cands[t][pick[t]]] for t in ts]
                ctree = STRtree(cur)
                for a, b in zip(*ctree.query(cur, predicate="overlaps").tolist()):
                    A, B = cur[a], cur[b]
                    if a != b and A.intersection(B).area > 0.05 * min(A.area, B.area):
                        t = ts[a] if A.area >= B.area else ts[b]
                        if pick[t] + 1 < len(cands[t]):
                            pick[t] += 1
                            changed = True
                dup = collections.defaultdict(list)  # one polygon picked for several numbers: none of them owns it
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
        # rooms share one pen: if the picks are mostly one pen signature, re-pick within it (drops e.g. dash-dot
        # apartment outlines that won a number) - only meaningful when entities carry explicit styles
        pens = collections.Counter(polys[p][0] for p in pick.values())
        if len(pens) > 1:
            pen = pens.most_common(1)[0][0]
            pick2 = select({P for P in ok if polys[P][0] == pen})
            pick = {t: pick2.get(t, p) for t, p in pick.items()}
        for t, p in sorted(pick.items()):
            if p not in room_of.values():
                room_of[t] = p
    room_idx = sorted(set(room_of.values()))
    rgeoms = [geoms[p] for p in room_idx]
    rtree = STRtree(rgeoms) if rgeoms else None
    info = {p: dict(number=None, label=None, printed=None, tags=[], anchor=None) for p in room_idx}
    for t, p in room_of.items():
        info[p].update(number=nums[t][3], anchor=Point(nums[t][0], nums[t][1]))
    anchors = [info[p]["anchor"] for p in room_idx]
    atree = STRtree(anchors) if anchors else None
    # labels / printed areas / tags: smallest room containing the text; a text outside every room joins the room whose
    # number is within 3 text heights (text block overflowing a tiny room); per room keep the text nearest the number
    best = {}
    for x, y, kind, val, h in cls:
        if rtree is None or kind not in ("label", "area", "tag"):
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
            best[p, kind] = (d, val)
    for (p, kind), (_, val) in best.items():
        info[p]["label" if kind == "label" else "printed"] = val
    pairs = [(info[p]["printed"], geoms[p].area) for p in room_idx if info[p]["printed"]]
    f = unit_factor(doc, [geoms[p].area for p in room_idx], pairs)
    # apartment outlines: non-room polygons containing a tag text and >= 1 room interior point
    tags = [c for c in cls if c[2] == "tag"]
    reps = [geoms[p].representative_point() for p in room_idx]
    units, outl = {}, set()
    tol = 0.05 / f  # 5 cm tolerance in drawing units
    if tags:
        room_set = set(room_idx)
        tpts = [Point(c[0], c[1]) for c in tags]
        th = tree.query(tpts, predicate="within")
        rh = tree.query(reps, predicate="within")
        rooms_in = collections.Counter(rh[1].tolist())
        # unit layer: the layer holding most tag-bearing polygons that also hold rooms
        hold = [p for t, p in zip(*th.tolist()) if p not in room_set and rooms_in[p]]
        ukey = lambda st: st[0]  # noqa: E731
        ul = collections.Counter(ukey(polys[p][0]) for p in hold)
        if ul and room_idx and ul.most_common(1)[0][0] == polys[room_idx[0]][0][0]:
            ukey = lambda st: st  # noqa: E731  (shared with the rooms' layer: separate by pen signature)
            ul = collections.Counter(ukey(polys[p][0]) for p in hold)
        ulayer = ul.most_common(1)[0][0] if ul else None
        shared = bool(room_idx) and ukey(polys[room_idx[0]][0]) == ulayer  # rooms and outlines share one pen
        # outlines = unit-layer polygons covering most of at least one room (outlines may be inset by a wall half).  Unnamed ones count too: an unnamed outline
        # nested in a named one is a hole (e.g. the stair core cut out of a single apartment), so its rooms get no unit
        for i, q in zip(*rh.tolist()):
            if q not in room_set and ukey(polys[q][0]) == ulayer and \
                    geoms[q].intersection(rgeoms[i]).area > 0.5 * rgeoms[i].area and \
                    (not shared or geoms[q].area > 1.2 * rgeoms[i].area):
                outl.add(q)  # (one pen for everything: must be clearly larger than the room, not a duplicate/fixture)
        # name outlines: a tag names the smallest outline around it; tags outside every outline (or whose outline is
        # already named) take the nearest still-unnamed outline that is not a hole in a named one
        loose = []
        for t, pt in enumerate(tpts):
            inside = [q for q in outl if geoms[q].contains(pt)]
            q = min(inside, key=lambda q: geoms[q].area) if inside else None
            if q is not None and q not in units:
                units[q] = tags[t][3]
            else:
                loose.append(t)
        for t in loose:
            free = [q for q in outl if q not in units and
                    not any(geoms[u].buffer(tol).contains(geoms[q]) for u in units)]
            if free:
                q = min(free, key=lambda q: geoms[q].distance(tpts[t]))
                if geoms[q].distance(tpts[t]) * f < 3.0:  # within 3 m of the outline
                    units[q] = tags[t][3]
    ulist = sorted(outl, key=lambda p: geoms[p].area)
    ubuf = {p: geoms[p].buffer(tol) for p in ulist}
    rooms = []
    for p, rp in zip(room_idx, reps):
        r = info[p]
        unit = next((units.get(u) for u in ulist if ubuf[u].contains(rp)), None)
        if unit is None and not units and len(set(r["tags"])) == 1:
            unit = r["tags"][0]  # no outlines at all: tag written inside the room
        area = r["printed"] if r["printed"] is not None else round(geoms[p].area * f * f, 2)
        b = geoms[p].bounds
        rooms.append(dict(number=r["number"], label=r["label"], area=area, unit=unit,
                          printed=r["printed"] is not None, bbox=[round(v, 3) for v in b]))
    meta = dict(room_layer=str(room_layer), purity=round(purity, 2), unit_factor=f, n_units=len(units), n_nums=len(nums), n_rooms=len(rooms))
    return rooms, meta


def solve(path, queries):
    rooms, meta = build_rooms(path)
    if len(rooms) < 0.5 * meta.get("n_nums", 0):  # outlines may be hatches only: retry with hatch boundaries
        r2, m2 = build_rooms(path, use_hatch=True)
        if len(r2) > len(rooms):
            rooms, meta = r2, m2
    res = common.answer_all(rooms, queries)
    return [dict(r, meta=meta) if r else r for r in res]
