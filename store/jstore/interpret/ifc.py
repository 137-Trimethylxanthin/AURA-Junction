"""Layer 2 for IFC models: rooms, doors, windows and sanitary fixtures become entities with claims.

    run(db, drawing_key, file_rows) -> {"room": n, "door": n, ...}

Everything is read from the Layer 1 mirror through the model views of sql/20_ifc.sql (q.elements, q.spaces), so the
entities follow the same definitions the question-answering model sees:

  room     every top-level IfcSpace: number = Name, label = LongName, zone = the zone or group it is assigned to,
           net_area_m2 claimed twice - the stored quantity and the measured footprint.  Stored NetFloorArea is
           preferred when present and non-zero, otherwise the measured footprint (a gross or vendor area is kept
           as a normal claim); an issue is written when the two differ by more than 5 %
  door,    tag, storey, width_m / height_m from OverallWidth / OverallHeight, host = the room they bound
  window   (space boundaries), all bounded rooms in attrs.rooms
  toilet, washbasin, sink, bathtub, shower, urinal, bidet, sanitary_fixture
           by class (IFC2X3: IfcFlowTerminal typed by IfcSanitaryTerminalType), predefined type, then the words in
           the type name and name; host = the room whose footprint holds the fixture

Walls, slabs and the rest stay in Layer 1 (q.elements, q.storey_totals).
"""
from __future__ import annotations

import re

from shapely import from_wkb

from ..db import DB
from . import define, merge, vocab

BY = "jstore.interpret.ifc"
PTYPE = {"TOILETPAN": "toilet", "WCSEAT": "toilet", "WASHHANDBASIN": "washbasin", "SINK": "sink", "BATH": "bathtub",
         "SHOWER": "shower", "URINAL": "urinal", "BIDET": "bidet"}
# Words authoring tools put in type and object names (lower case, accents removed); first match wins.
WORDS = [
    ("urinal", r"urinal|urinoir|urinario|orinatoio|pisoar"),
    ("bidet", r"\bbidet"),
    ("washbasin", r"wash ?basin|hand ?basin|wash ?hand|lavabo|waschbecken|waschtisch|wastafel|fontein|handvask|"
                  r"tvattstall|pesuallas|umywalk|lavamanos|lavandino|lavatory|\bbasin\b"),
    ("bathtub", r"bath ?tub|\bbath\b|badewanne|\bwanne\b|ligbad|baignoire|banera|vasca|badekar|badkar|kylpyamme|wanna"),
    ("shower", r"shower|dusch|douche|ducha|doccia|bruser|suihku|prysznic"),
    ("sink", r"\bsink|spule|spoelbak|gootsteen|evier|fregadero|lavello|diskho|tiskiallas|\bzlew|kokkenvask"),
    ("toilet", r"toilet|\bwc\b|water ?closet|klosett|closetpot|inodoro|\bvaso\b|wc-istuin|klozet|miska"),
]
WORDS = [(k, re.compile(p)) for k, p in WORDS]
ACCESSORY = re.compile(r"holder|halter|dispenser|spender|brush|burste|paper|papier|rolhouder|seat cover|mirror|spiegel|"
                       r"grab ?bar|partition|cubicle|trennwand|cabin")
GENERIC = ("FlowTerminal", "BuildingElementProxy")       # classes exporters fall back to for fixtures
APARTMENT = re.compile(r"apartment|\bapt\b|\bflat\b|\bunit\b|wohnung|\bwhg\b|\bwe\b|appartement|logement|appartamento|"
                       r"vivienda|piso\b|lejlighed|bolig|lagenhet|asunto|huoneisto|mieszkan|woning|dwelling")


def fixture_kind(category: str, ptype: str | None, type_name: str | None, name: str | None,
                 object_type: str | None) -> tuple[str | None, float]:
    """-> (kind, confidence) for a candidate fixture; (None, 0) when nothing says what it is."""
    sanitary = category == "SanitaryTerminal"
    if sanitary and (ptype or "").upper() in PTYPE:
        return PTYPE[(ptype or "").upper()], 0.95
    for text in (type_name, object_type, name):
        s = vocab.plain(text or "")
        if not s or (not sanitary and ACCESSORY.search(s)):
            continue
        for kind, rx in WORDS:
            if rx.search(s):
                return kind, 0.85 if sanitary else 0.6
    return ("sanitary_fixture", 0.8) if sanitary else (None, 0.0)


def _zone(groups: list[dict]) -> str | None:
    """The zone a space belongs to: its only IfcZone, or the only apartment-like zone or group; else None."""
    def like(g: dict) -> bool:
        return bool(APARTMENT.search(vocab.plain(" ".join(str(g[k] or "") for k in ("name", "long_name", "object_type")))))

    zones = [g for g in groups if g["ifc_class"] in ("IfcZone", "IfcSpatialZone")]
    if len(zones) != 1:                           # several zones, or plain groups: only an apartment-like one counts
        zones = [g for g in (zones or groups) if like(g)]
    return (zones[0]["name"] or zones[0]["long_name"]) if len(zones) == 1 else None


def _box(b) -> list[float] | None:
    return [b[0], b[1], b[3], b[4]] if b and len(b) == 6 else None


def _records(db: DB, f: dict) -> list[dict]:
    fid = f["file_id"]
    pages = {r["guid"]: r["page_no"] for r in db.run(
        "select meta->>'guid' as guid, page_no from jstore.page where file_id = :f", f=fid)}
    geo = {r["guid"]: r for r in db.run("""
        select g.guid, encode(st_asbinary(g.footprint), 'hex') as wkb, g.bbox from jstore.ifc_geometry g
        join q.elements e on e.file_id = g.file_id and e.guid = g.guid
        where g.file_id = :f and (e.ifc_class = 'IfcSpace' or e.category in ('Door', 'Window', 'SanitaryTerminal')
                                  or e.category = any (:gen))""", f=fid, gen=list(GENERIC))}
    groups: dict[str, list[dict]] = {}
    for r in db.run("""select r.dst_guid as guid, g.ifc_class, g.name, g.long_name, g.object_type
                       from jstore.ifc_relation r join jstore.ifc_element g on g.file_id = r.file_id and g.guid = r.src_guid
                       where r.file_id = :f and r.rel = 'in_group' order by g.name""", f=fid):
        groups.setdefault(r["guid"], []).append(r)
    recs: list[dict] = []

    def record(e: dict, kind: str, d: int, **more) -> dict:
        g = geo.get(e["guid"]) or {}
        rec = dict(kind=kind, ifc_class=e["ifc_class"], page_no=pages.get(e["storey_guid"], 0),
                   geom=from_wkb(g["wkb"]) if g.get("wkb") else None,
                   sources=[("ifc_element", e["guid"], "object", _box(g.get("bbox")), d)], claims=[], **more)
        rec["attrs"] = {"guid": e["guid"], "ifc_file": fid, **(more.get("attrs") or {})}
        recs.append(rec)
        return rec

    # ---- rooms
    spaces = db.run("""select s.*, e.ifc_class, e.predefined_type, el.attrs->>'InteriorOrExteriorSpace' as side
                       from q.spaces s join q.elements e using (file_id, guid)
                       join jstore.ifc_element el using (file_id, guid)
                       where s.file_id = :f and not s.is_part order by s.storey, s.number""", f=fid)
    zone_of: dict[str, str | None] = {}
    if spaces:
        d = define(db, fid, "room", "ifc_class", {"class": "IfcSpace", "number": "Name", "label": "LongName",
                   "area": "stored NetFloorArea when non-zero, else the measured footprint"}, 0.95, BY)
        for s in spaces:
            label = s["label"] or s["number"]
            zone = _zone(groups.get(s["guid"], []))
            zone_of[s["number"]] = zone
            outdoor = "EXTERNAL" in ((s["side"] or "").upper(), (s["predefined_type"] or "").upper())
            rec = record(s, "room", d, number=s["number"], label=label, label_norm=vocab.room_type(label), zone=zone,
                         attrs={"indoor": not outdoor and vocab.indoor(label), "full_label": s["full_label"]})
            net, other, measured = s["stored_net_floor_area_m2"], s["stored_other_floor_area_m2"], s["measured_footprint_m2"]
            if net:
                rec["claims"].append(("net_area_m2", net, None, "m2", "stored_quantity", 0, 0.95))
            elif other:                           # gross or vendor area: kept, never preferred
                rec["claims"].append(("net_area_m2", other, None, "m2", "stored_quantity", 0, 0.6))
            if measured:
                rec["claims"].append(("net_area_m2", measured, None, "m2", "measured_geometry", 0, 0.8))
            if s["height_m"]:
                rec["claims"].append(("height_m", s["height_m"], None, "m", "measured_geometry", 0, 0.8))

    # ---- doors and windows
    bounded: dict[str, list[str]] = {}
    for r in db.run("""select r.dst_guid as guid, array_agg(distinct s.name order by s.name) as rooms
                       from jstore.ifc_relation r join jstore.ifc_element s on s.file_id = r.file_id and s.guid = r.src_guid
                       where r.file_id = :f and r.rel = 'bounds' and s.ifc_class = 'IfcSpace' and s.name is not null
                         and r.dst_guid is not null group by 1""", f=fid):
        bounded[r["guid"]] = r["rooms"]
    for kind, cat in (("door", "Door"), ("window", "Window")):
        els = db.run("""select * from q.elements where file_id = :f and category = :c and not is_part
                        order by storey, tag, guid""", f=fid, c=cat)
        if not els:
            continue
        d = define(db, fid, kind, "ifc_class", {"category": cat, "size": "OverallWidth / OverallHeight",
                   "host": "IfcRelSpaceBoundary"}, 0.95, BY)
        for e in els:
            rooms = bounded.get(e["guid"], [])
            rec = record(e, kind, d, number=e["tag"], label=e["name"] or e["type_name"],
                         host=rooms[0] if rooms else None, zone=zone_of.get(rooms[0]) if rooms else None,
                         attrs={"rooms": rooms, "type_name": e["type_name"], "is_external": e["is_external"]})
            for prop, stored, measured in (("width_m", e["width_m"], e["measured_length_m"]),
                                           ("height_m", e["height_m"], e["measured_height_m"])):
                if stored:
                    rec["claims"].append((prop, stored, None, "m", "stored_quantity", 0, 0.95))
                elif measured:
                    rec["claims"].append((prop, measured, None, "m", "measured_geometry", 0, 0.6))

    # ---- sanitary fixtures
    els = db.run("""select e.*, el.object_type from q.elements e join jstore.ifc_element el using (file_id, guid)
                    where e.file_id = :f and not e.is_part and (e.category = 'SanitaryTerminal' or e.category = any (:gen))
                    order by e.storey, e.guid""", f=fid, gen=list(GENERIC))
    inside = {r["guid"]: r["room"] for r in db.run("""
        select distinct on (ge.guid) ge.guid, s.name as room
        from jstore.ifc_geometry ge
        join jstore.ifc_geometry gs on gs.file_id = ge.file_id and st_contains(gs.footprint, st_centroid(ge.footprint))
             and (ge.z_min + ge.z_max) / 2 between gs.z_min - 0.1 and gs.z_max + 0.1
        join jstore.ifc_element s on s.file_id = gs.file_id and s.guid = gs.guid and s.ifc_class = 'IfcSpace'
        where ge.file_id = :f and ge.guid = any (:g) order by ge.guid, gs.footprint_m2""",
        f=fid, g=[e["guid"] for e in els])} if els else {}
    defs: dict[str, int] = {}
    for e in els:
        kind, conf = fixture_kind(e["category"], e["predefined_type"], e["type_name"], e["name"], e["object_type"])
        if kind is None:
            continue
        if kind not in defs:
            defs[kind] = define(db, fid, kind, "ifc_class", {
                "category": "SanitaryTerminal, or a generic terminal / proxy named so",
                "rule": "predefined type, then words in the type name, object type and name"}, conf, BY)
        room = inside.get(e["guid"])
        record(e, kind, defs[kind], number=e["tag"], label=e["name"] or e["type_name"], host=room, zone=zone_of.get(room),
               attrs={"type_name": e["type_name"], "predefined_type": e["predefined_type"], "category": e["category"],
                      "confidence": conf})
    return recs


def _finish(db: DB, f: dict) -> int:
    """Storeys, claim ranks and area conflicts for the entities read from one model.  -> number of issues"""
    p = dict(f=f["file_id"], d=f["drawing_key"])
    db.run("""update jstore.entity e set storey = el.storey
              from jstore.entity_source s join jstore.ifc_element el on el.file_id = s.file_id and el.guid = s.ref_key
              where s.entity_id = e.entity_id and s.file_id = :f and s.ref_table = 'ifc_element'
                and e.drawing_key = :d and el.storey is not null and e.storey is distinct from el.storey""", **p)
    db.run("""update jstore.claim c set rank = 'preferred'
              where c.file_id = :f and c.property = 'net_area_m2'
                and ((c.method = 'stored_quantity' and c.confidence >= 0.9)
                  or (c.method = 'measured_geometry' and not exists (
                        select from jstore.claim o where o.entity_id = c.entity_id and o.file_id = c.file_id
                          and o.property = c.property and o.method = 'stored_quantity' and o.confidence >= 0.9)))""",
           f=f["file_id"])
    return len(db.run("""
        insert into jstore.issue (project_id, drawing_key, entity_id, property, kind, detail)
        select e.project_id, e.drawing_key, e.entity_id, 'net_area_m2', 'value_conflict',
               format('room %s (%s): stored area %s m2, footprint measured from the geometry %s m2 (%s %% apart)',
                      coalesce(e.number, '?'), coalesce(e.label, '?'), round(a.value_num::numeric, 2),
                      round(b.value_num::numeric, 2),
                      round((100 * abs(a.value_num - b.value_num) / greatest(a.value_num, b.value_num))::numeric, 1))
        from jstore.claim a
        join jstore.claim b on b.entity_id = a.entity_id and b.file_id = a.file_id and b.property = a.property
                           and b.method = 'measured_geometry'
        join jstore.entity e on e.entity_id = a.entity_id
        where a.file_id = :f and e.drawing_key = :d and a.property = 'net_area_m2' and a.method = 'stored_quantity'
          and abs(a.value_num - b.value_num) > 0.05 * greatest(a.value_num, b.value_num)
        returning issue_id""", **p))


def run(db: DB, drawing_key: str, file_rows: list[dict]) -> dict:
    """Entities of the IFC files of one drawing (the dispatcher has removed the drawing's old ones)."""
    out: dict = {}
    before = db.one("select current_setting('jstore.files', true) as v")["v"] or ""
    try:
        for f in file_rows:
            db.one("select set_config('jstore.files', :f, false) as v", f=f["file_id"])   # scope of the q views
            recs = _records(db, f)
            stats = merge(db, f, recs, registered=True)
            for r in recs:
                out[r["kind"]] = out.get(r["kind"], 0) + 1
            out["issues"] = out.get("issues", 0) + _finish(db, f)
            for k in ("matched", "unlinked"):
                if stats.get(k):
                    out[k] = out.get(k, 0) + stats[k]
    finally:
        db.one("select set_config('jstore.files', :f, false) as v", f=before)
    return out
