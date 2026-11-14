"""Layer 1 for IFC: every rooted object, property, quantity and relation of a model becomes a row.

ifc_element   products (elements, openings, spatial elements, annotations ...), type objects, groups / zones /
              systems and the project, with the storey resolved by walking containment and aggregation upwards
ifc_property  every property and quantity of those objects, the ones inherited from the type included
              (from_type); lengths, areas and volumes converted to m, m2, m3 with the file's units
ifc_relation  contains, aggregates, nests, typed_by, voids, fills, bounds, in_group, referenced, services, material
ifc_geometry  what the tessellated body measures in world coordinates and metres: footprint, volume, surface,
              z range, bounding box, plus the extents of the footprint's oriented rectangle and the area of
              the largest side face (see sql/20_ifc.sql).  An assembly without a body of its own is measured
              as the sum of its parts (from_parts)
page / frame  one page per building storey in elevation order, identity frame

Geometry runs in a forked child with a time budget (JSTORE_IFC_GEOM_BUDGET_S, default 600 s): a model whose
shapes crash, need too much memory (JSTORE_IFC_GEOM_MEM_MB, default 2048 on top of the parsed model) or take too
long keeps its elements, properties and relations, and meta says what is missing.
"""
from __future__ import annotations

import collections
import multiprocessing as mp
import os
import resource
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import numpy as np
import shapely

from ..db import DB, arr, geom, js

BATCH = 20_000
GEOM_CHUNK = 200          # products per geometry iterator
GEOM_MIN = 8              # a group this small that still kills the geometry process is skipped
MAX_TRIANGLES = 60_000     # per shape: above this the footprint polygon is skipped (areas are still measured)
SKIP_ATTRS = {"GlobalId", "OwnerHistory", "Name", "Description", "ObjectType", "Tag", "LongName", "PredefinedType",
              "ObjectPlacement", "Representation", "RepresentationMaps", "HasPropertySets", "RepresentationContexts",
              "UnitsInContext"}
PREFIX = {"EXA": 1e18, "PETA": 1e15, "TERA": 1e12, "GIGA": 1e9, "MEGA": 1e6, "KILO": 1e3, "HECTO": 1e2, "DECA": 10.0,
          "DECI": 0.1, "CENTI": 0.01, "MILLI": 1e-3, "MICRO": 1e-6, "NANO": 1e-9, "PICO": 1e-12}
DIMS = {"LENGTHUNIT": ("m", 1), "AREAUNIT": ("m2", 2), "VOLUMEUNIT": ("m3", 3)}
MEASURES = {"IfcLengthMeasure": "LENGTHUNIT", "IfcPositiveLengthMeasure": "LENGTHUNIT",
            "IfcNonNegativeLengthMeasure": "LENGTHUNIT", "IfcAreaMeasure": "AREAUNIT", "IfcVolumeMeasure": "VOLUMEUNIT"}
QUANTITY = {"IfcQuantityLength": ("LengthValue", "LENGTHUNIT"), "IfcQuantityArea": ("AreaValue", "AREAUNIT"),
            "IfcQuantityVolume": ("VolumeValue", "VOLUMEUNIT"), "IfcQuantityCount": ("CountValue", None),
            "IfcQuantityWeight": ("WeightValue", None), "IfcQuantityTime": ("TimeValue", None),
            "IfcQuantityNumber": ("NumberValue", None)}
EL_COLS = ["file_id", "guid", "step_id", "ifc_class", "name", "long_name", "object_type", "tag", "predefined_type",
           "description", "storey", "storey_guid", "parent_guid", "type_guid", "type_name", "attrs"]
PROP_COLS = ["file_id", "guid", "pset", "name", "value_text", "value_num", "unit", "kind", "from_type"]
REL_COLS = ["file_id", "rel", "src_guid", "dst_guid", "attrs"]
ASSEMBLY_SQL = """
insert into jstore.ifc_geometry (file_id, guid, footprint, footprint_m2, volume_m3, z_min, z_max, bbox, length_m, width_m,
                                 side_m2, from_parts)
select file_id, guid, fp, st_area(fp), volume_m3, z_min, z_max, bbox,
       greatest(st_distance(st_pointn(ring, 1), st_pointn(ring, 2)), st_distance(st_pointn(ring, 2), st_pointn(ring, 3))),
       least(st_distance(st_pointn(ring, 1), st_pointn(ring, 2)), st_distance(st_pointn(ring, 2), st_pointn(ring, 3))),
       side_m2, true
from (select *, case when geometrytype(st_orientedenvelope(fp)) = 'POLYGON'
                     then st_exteriorring(st_orientedenvelope(fp)) end as ring
      from (select e.file_id, e.parent_guid as guid, st_union(g.footprint) as fp, sum(g.volume_m3) as volume_m3,
                   min(g.z_min) as z_min, max(g.z_max) as z_max, max(g.side_m2) as side_m2,
                   array[min(g.bbox[1]), min(g.bbox[2]), min(g.bbox[3]), max(g.bbox[4]), max(g.bbox[5]), max(g.bbox[6])] as bbox
            from jstore.ifc_element e join jstore.ifc_geometry g on g.file_id = e.file_id and g.guid = e.guid
            where e.file_id = :f and e.parent_guid = any (cast(:w as text[]))
              and not exists (select from jstore.ifc_geometry o where o.file_id = e.file_id and o.guid = e.parent_guid)
            group by 1, 2) a) b
returning guid"""
GEOM_COLS = ["file_id", "guid", "footprint", "footprint_m2", "volume_m3", "surface_m2", "z_min", "z_max", "bbox",
             "length_m", "width_m", "side_m2"]


# ----------------------------------------------------------------------------- units


def unit_scale(u) -> float | None:
    """Factor from one IfcNamedUnit to SI (prefix raised to the unit's dimension); None when unknown."""
    try:
        if u.is_a("IfcSIUnit"):
            dim = DIMS.get(u.UnitType, (None, 1))[1]
            return PREFIX.get(u.Prefix, 1.0) ** dim
        if u.is_a("IfcConversionBasedUnit"):
            cf = u.ConversionFactor
            inner = unit_scale(cf.UnitComponent)
            return None if inner is None else float(cf.ValueComponent.wrappedValue) * inner
    except Exception:  # noqa: BLE001 - malformed unit
        pass
    return None


def file_scales(f) -> dict:
    """{LENGTHUNIT / AREAUNIT / VOLUMEUNIT: factor to SI}.  A missing area or volume unit follows the length unit."""
    found = {}
    for ua in f.by_type("IfcUnitAssignment"):
        for u in ua.Units or ():
            t = getattr(u, "UnitType", None)
            if t in DIMS and t not in found:
                s = unit_scale(u)
                if s:
                    found[t] = s
    length = found.get("LENGTHUNIT", 1.0)
    return {t: found.get(t, length ** d) for t, (_, d) in DIMS.items()}


def _unit_name(f) -> str | None:
    for ua in f.by_type("IfcUnitAssignment"):
        for u in ua.Units or ():
            if getattr(u, "UnitType", None) == "LENGTHUNIT":
                return ((getattr(u, "Prefix", None) or "") + (u.Name or "")).lower() or None
    return None


# ----------------------------------------------------------------------------- properties


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        return ", ".join(_text(x) or "" for x in v)
    if isinstance(v, ifcopenshell.entity_instance):
        if v.id() == 0:                       # a typed value (IfcLabel('x'))
            return _text(v.wrappedValue)
        return str(getattr(v, "Name", None) or v.is_a())
    return str(v)


class Props:
    """Turns property definitions into rows (pset, name, value_text, value_num, unit, kind); cached per definition."""

    def __init__(self, scales: dict):
        self.scales = scales
        self.cache: dict[int, list[tuple]] = {}

    def _scaled(self, raw, measure: str | None, own_unit):
        """-> (value_num, unit) for a number of the given measure class."""
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None, None
        ut = MEASURES.get(measure or "")
        if ut is None:
            return float(raw), None
        s = unit_scale(own_unit) if own_unit is not None and own_unit.is_a("IfcNamedUnit") else None
        return float(raw) * (s or self.scales[ut]), DIMS[ut][0]

    def _value(self, v, own_unit=None):
        """A typed IFC value -> (value_text, value_num, unit)."""
        if v is None:
            return None, None, None
        if isinstance(v, ifcopenshell.entity_instance) and v.id() == 0:
            num, unit = self._scaled(v.wrappedValue, v.is_a(), own_unit)
            return _text(v.wrappedValue), num, unit
        num, unit = self._scaled(v, None, None)
        return _text(v), num, unit

    def _property(self, p, prefix: str, out: list, pset: str, kind: str):
        name = prefix + str(p.Name or "")
        t = p.is_a()
        if t == "IfcPropertySingleValue":
            out.append((pset, name, *self._value(p.NominalValue, p.Unit), kind))
        elif t == "IfcComplexProperty":
            for sub in p.HasProperties or ():
                self._property(sub, name + ".", out, pset, kind)
        elif t == "IfcPropertyEnumeratedValue":
            out.append((pset, name, _text(p.EnumerationValues), None, None, kind))
        elif t == "IfcPropertyListValue":
            out.append((pset, name, _text(p.ListValues), None, None, kind))
        elif t == "IfcPropertyBoundedValue":
            for part in ("LowerBoundValue", "UpperBoundValue", "SetPointValue"):
                v = getattr(p, part, None)
                if v is not None:
                    out.append((pset, f"{name}.{part}", *self._value(v, p.Unit), kind))
        elif t == "IfcPropertyTableValue":
            out.append((pset, name, js({"defining": [_text(x) for x in p.DefiningValues or ()],
                                        "defined": [_text(x) for x in p.DefinedValues or ()]}), None, None, kind))
        elif t == "IfcPropertyReferenceValue":
            out.append((pset, name, _text(p.PropertyReference), None, None, kind))
        else:
            out.append((pset, name, None, None, None, kind))

    def _quantity(self, q, prefix: str, out: list, pset: str):
        name = prefix + str(q.Name or "")
        t = q.is_a()
        if t == "IfcPhysicalComplexQuantity":
            for sub in q.HasQuantities or ():
                self._quantity(sub, name + ".", out, pset)
            return
        attr, ut = QUANTITY.get(t, (None, None))
        raw = getattr(q, attr, None) if attr else None
        if raw is None:
            out.append((pset, name, None, None, None, "quantity"))
        elif ut is None:
            out.append((pset, name, str(raw), float(raw), None, "quantity"))
        else:
            own = getattr(q, "Unit", None)
            s = unit_scale(own) if own is not None else None
            out.append((pset, name, str(raw), float(raw) * (s or self.scales[ut]), DIMS[ut][0], "quantity"))

    def rows(self, d) -> list[tuple]:
        """Rows of one property set definition (IfcPropertySet, IfcElementQuantity or a predefined set)."""
        i = d.id()
        if i in self.cache:
            return self.cache[i]
        out: list[tuple] = []
        pset = str(getattr(d, "Name", None) or d.is_a())
        try:
            if d.is_a("IfcElementQuantity"):
                for q in d.Quantities or ():
                    self._quantity(q, "", out, pset)
            elif d.is_a("IfcPropertySet"):
                for p in d.HasProperties or ():
                    self._property(p, "", out, pset, "property")
            else:  # IfcDoorLiningProperties, IfcWindowPanelProperties ...: their own attributes are the values
                for k, v in d.get_info(recursive=False).items():
                    if k in ("id", "type", "GlobalId", "OwnerHistory", "Name", "Description") or v is None:
                        continue
                    if isinstance(v, (int, float, str, bool)):
                        num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
                        out.append((pset, k, str(v), num, None, "property"))
        except Exception:  # noqa: BLE001 - one malformed set must not lose the file
            pass
        self.cache[i] = out
        return out


# ----------------------------------------------------------------------------- materials


def _material(m, ls: float) -> dict:
    """What a material association says, as plain data."""
    t = m.is_a()
    name = lambda x: getattr(x, "Name", None) if x is not None else None  # noqa: E731
    if t == "IfcMaterial":
        return {"materials": [m.Name]}
    if t == "IfcMaterialList":
        return {"materials": [name(x) for x in m.Materials or ()]}
    if t == "IfcMaterialLayerSetUsage":
        out = _material(m.ForLayerSet, ls)
        if getattr(m, "OffsetFromReferenceLine", None) is not None:
            out["offset_m"] = m.OffsetFromReferenceLine * ls
        return out
    if t == "IfcMaterialLayerSet":
        layers = [{"material": name(x.Material), "thickness_m": round((x.LayerThickness or 0) * ls, 6)}
                  for x in m.MaterialLayers or ()]
        return {"layer_set": getattr(m, "LayerSetName", None), "layers": layers,
                "materials": [x["material"] for x in layers],
                "thickness_m": round(sum(x["thickness_m"] for x in layers), 6)}
    if t == "IfcMaterialLayer":
        return {"materials": [name(m.Material)], "thickness_m": (m.LayerThickness or 0) * ls}
    if t == "IfcMaterialProfileSetUsage" or t == "IfcMaterialProfileSetUsageTapering":
        return _material(m.ForProfileSet, ls)
    if t == "IfcMaterialProfileSet":
        return {"profile_set": m.Name, "materials": [name(x.Material) for x in m.MaterialProfiles or ()],
                "profiles": [name(x.Profile) for x in m.MaterialProfiles or ()]}
    if t == "IfcMaterialConstituentSet":
        return {"constituent_set": m.Name, "materials": [name(x.Material) for x in m.MaterialConstituents or ()]}
    return {"materials": [name(m)], "kind": t}


# ----------------------------------------------------------------------------- geometry


def measure(verts: np.ndarray, faces: np.ndarray) -> dict | None:
    """Volume, surface, footprint and extents of one triangulated body (world coordinates, metres)."""
    if len(verts) == 0 or len(faces) == 0 or not np.isfinite(verts).all():
        return None
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    tri = verts[faces] - lo                       # local origin keeps the sums precise far from 0,0,0
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1) / 2
    out = dict(volume_m3=abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6), surface_m2=float(area.sum()),
               z_min=float(lo[2]), z_max=float(hi[2]), bbox=[*lo.tolist(), *hi.tolist()],
               footprint=None, footprint_m2=None, length_m=None, width_m=None, side_m2=None)
    # Faces that show in plan.  A closed body is covered by its upward faces alone, which halves the union.
    nz = n[:, 2] / 2
    closed = abs(float(nz.sum())) <= 1e-6 * float(np.abs(nz).sum())
    flat = nz > 1e-9 if closed else np.abs(nz) > 1e-9
    if not flat.any() or flat.sum() > MAX_TRIANGLES:
        return out
    try:
        xy = (tri[flat][:, :, :2] + lo[:2])
        fp = shapely.union_all(shapely.polygons(xy))
        if fp.is_empty or fp.geom_type not in ("Polygon", "MultiPolygon"):
            parts = [g for g in getattr(fp, "geoms", []) if g.geom_type == "Polygon"]
            fp = shapely.MultiPolygon(parts) if parts else None
        if fp is None:
            return out
        out["footprint"], out["footprint_m2"] = fp, float(fp.area)
        # Oriented rectangle of the footprint: its long edge is the element's run, the short one its thickness.
        ring = np.asarray(fp.minimum_rotated_rectangle.exterior.coords)
        e1, e2 = ring[1] - ring[0], ring[2] - ring[1]
        l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
        if l1 < l2:
            e1, e2, l1, l2 = e2, e1, l2, l1
        out["length_m"], out["width_m"] = l1, l2
        # Area of the faces seen from one side: across the run (a wall's face), or along it if that is larger.
        unit = n / np.maximum(np.linalg.norm(n, axis=1), 1e-30)[:, None]
        sides = []
        for d in (e2 / l2 if l2 > 1e-9 else None, e1 / l1 if l1 > 1e-9 else None):
            if d is not None:
                sides.append(float(area[unit[:, :2] @ d > 0.01].sum()))
        out["side_m2"] = max(sides) if sides else None
    except Exception:  # noqa: BLE001 - degenerate projection: keep the scalar measures
        pass
    return out


def _measure_safe(mesh: tuple) -> dict | None:
    try:
        return measure(mesh[1], mesh[2])
    except Exception:  # noqa: BLE001 - one bad shape
        return None


def _vm_bytes() -> int:
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[0]) * resource.getpagesize()


def _geometry_child(f, file_id: str, keys: dict[int, str], threads: int, ranges: list[tuple[int, int]], conn) -> None:
    """Runs in a forked process: tessellate the products of `ranges` (positions in the processing order) and write
    ifc_geometry after each range.  The geometry kernel keeps memory it has used, so the process hands over to a
    fresh one ('recycle') when it has grown by half its allowance, and is capped at the full allowance: a
    pathological shape ends this process, not the machine."""
    done = failed = 0
    try:
        extra = int(os.environ.get("JSTORE_IFC_GEOM_MEM_MB", "2048")) << 20
        base = _vm_bytes()
        resource.setrlimit(resource.RLIMIT_AS, (base + extra, base + extra))
        db = DB()
        s = ifcopenshell.geom.settings()
        s.set("use-world-coords", True)
        pool = ThreadPoolExecutor(max(2, threads // 2))
        # Rooms and building elements first: they are what is left when the time budget ends early.
        todo = sorted((f.by_id(i) for i in keys), key=lambda e: (
            0 if e.is_a("IfcSpace") else 1 if e.is_a("IfcBuildingElement") or e.is_a("IfcBuiltElement") else 2, e.id()))
        for n, (lo, hi) in enumerate(ranges):
            if n and _vm_bytes() - base > extra // 2:
                conn.send(("recycle", n))
                break
            conn.send(("range", n))
            try:
                it = ifcopenshell.geom.iterator(s, f, threads, include=todo[lo:hi])
                if not it.initialize():
                    continue
            except Exception:  # noqa: BLE001 - nothing in this range has a body
                continue
            meshes = []
            while True:
                try:
                    sh = it.get()
                    g = sh.geometry
                    meshes.append((keys[sh.id], np.array(g.verts, dtype=float).reshape(-1, 3),
                                   np.array(g.faces, dtype=np.int64).reshape(-1, 3)))
                except Exception:  # noqa: BLE001 - one bad shape
                    failed += 1
                if not it.next():
                    break
            rows = {}
            for (key, _, _), m in zip(meshes, pool.map(_measure_safe, meshes)):   # GEOS releases the GIL
                if m is None:
                    failed += 1
                    continue
                rows[key] = (file_id, key, geom(m["footprint"]), m["footprint_m2"], m["volume_m3"], m["surface_m2"],
                             m["z_min"], m["z_max"], arr(m["bbox"]), m["length_m"], m["width_m"], m["side_m2"])
            db.copy("jstore.ifc_geometry", GEOM_COLS, rows.values())
            done += len(rows)
        else:
            conn.send(("ok", done, failed))
        db.close()
    except Exception as e:  # noqa: BLE001
        conn.send(("error", done, repr(e)[:300]))
    finally:
        conn.close()
        os._exit(0)                               # no interpreter teardown: the parent's sockets stay untouched


# ----------------------------------------------------------------------------- reader


def _chunks(db: DB, table: str, cols: list[str], rows) -> int:
    n, buf = 0, []
    for r in rows:
        buf.append(r)
        if len(buf) >= BATCH:
            n += db.copy(table, cols, buf)
            buf = []
    return n + db.copy(table, cols, buf)


def _open(path: Path):
    """-> (model, note).  A file that does not parse because a block of it was overwritten with NUL bytes is read
    again without the damaged lines; the note says how many were dropped."""
    try:
        return ifcopenshell.open(str(path)), None
    except Exception:  # noqa: BLE001
        data = path.read_bytes()
        if b"\x00" not in data:
            raise
        lines = data.split(b"\n")
        good = [ln for ln in lines if b"\x00" not in ln]
        with tempfile.NamedTemporaryFile(suffix=".ifc") as tmp:
            tmp.write(b"\n".join(good))
            tmp.flush()
            return ifcopenshell.open(tmp.name), f"{len(lines) - len(good)} line(s) holding NUL bytes dropped"


def ingest(db: DB, file_id: str, path: Path) -> dict:
    t0 = time.time()
    for t in ("ifc_geometry", "ifc_property", "ifc_relation", "ifc_element", "page", "frame"):
        db.run(f"delete from jstore.{t} where file_id = :f", f=file_id)
    f, repaired = _open(path)
    t_open = time.time() - t0
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema_identifier)
    scales = file_scales(f)
    ls = scales["LENGTHUNIT"]

    def all_of(cls: str) -> list:
        try:
            return f.by_type(cls)
        except RuntimeError:                      # class not in this schema
            return []

    # ---- objects that get a row, with a key that is unique in the file (a repeated GlobalId gets '#<step id>')
    objs, keys, used = [], {}, set()
    for cls in ("IfcProject", "IfcProduct", "IfcTypeObject", "IfcGroup"):
        for e in all_of(cls):
            i = e.id()
            if i in keys:
                continue
            g = e.GlobalId or f"#{i}"
            if g in used:
                g = f"{g}#{i}"
            used.add(g)
            keys[i] = g
            objs.append(e)
    key = lambda e: keys.get(e.id()) if e is not None else None  # noqa: E731

    # ---- relations (also the source of parents, storeys and types)
    rels: list[tuple] = []
    container, whole, nest, host, type_of = {}, {}, {}, {}, {}

    def link(rel, src, dst, attrs=None, into=None):
        s, d = key(src), key(dst)
        if s is None or (d is None and dst is not None):
            return
        rels.append((file_id, rel, s, d, js(attrs or {})))
        if into is not None and dst is not None:
            into.setdefault(dst.id(), src)

    def contained(r):
        for e in r.RelatedElements or ():
            link("contains", r.RelatingStructure, e, into=container)

    def aggregated(r):
        for e in r.RelatedObjects or ():
            link("aggregates", r.RelatingObject, e, into=whole)

    def nested(r):
        for e in r.RelatedObjects or ():
            link("nests", r.RelatingObject, e, into=nest)

    def typed(r):
        for e in r.RelatedObjects or ():
            if r.RelatingType is not None and key(e):
                type_of.setdefault(e.id(), r.RelatingType)
                link("typed_by", e, r.RelatingType)

    def bounded(r):
        if r.RelatingSpace is not None and key(r.RelatingSpace):
            link("bounds", r.RelatingSpace, r.RelatedBuildingElement,
                 {"physical": _text(r.PhysicalOrVirtualBoundary), "internal": _text(r.InternalOrExternalBoundary),
                  "name": r.Name})

    def grouped(r):
        for e in r.RelatedObjects or ():
            link("in_group", r.RelatingGroup, e)

    def referenced(r):
        for e in r.RelatedElements or ():
            link("referenced", r.RelatingStructure, e)

    def serviced(r):
        for e in r.RelatedBuildings or ():
            link("services", r.RelatingSystem, e)

    mats: dict[int, dict] = {}

    def material(r):
        m = r.RelatingMaterial
        if m is None:
            return
        if m.id() not in mats:
            try:
                mats[m.id()] = _material(m, ls)
            except Exception:  # noqa: BLE001
                mats[m.id()] = {"kind": m.is_a()}
        for e in r.RelatedObjects or ():
            if isinstance(e, ifcopenshell.entity_instance) and key(e):
                rels.append((file_id, "material", key(e), None, js(mats[m.id()])))

    skipped = 0
    for cls, fn in (("IfcRelContainedInSpatialStructure", contained), ("IfcRelAggregates", aggregated),
                    ("IfcRelNests", nested),
                    ("IfcRelVoidsElement", lambda r: link("voids", r.RelatingBuildingElement, r.RelatedOpeningElement, into=host)),
                    ("IfcRelFillsElement", lambda r: link("fills", r.RelatingOpeningElement, r.RelatedBuildingElement, into=host)),
                    ("IfcRelDefinesByType", typed), ("IfcRelSpaceBoundary", bounded), ("IfcRelAssignsToGroup", grouped),
                    ("IfcRelReferencedInSpatialStructure", referenced), ("IfcRelServicesBuildings", serviced),
                    ("IfcRelAssociatesMaterial", material)):
        for r in all_of(cls):
            try:
                fn(r)
            except Exception:  # noqa: BLE001 - a relation pointing at something that is not there (damaged file)
                skipped += 1

    # ---- storey of every object: direct container first, then the whole it is part of, then the wall it sits in
    storey_cache: dict[int, object] = {}

    def storey_of(e):
        chain, cur = [], e
        while cur is not None and cur.id() not in storey_cache and len(chain) < 32:
            if cur.is_a("IfcBuildingStorey"):
                storey_cache[cur.id()] = cur
                break
            chain.append(cur.id())
            i = cur.id()
            cur = container.get(i) or whole.get(i) or nest.get(i) or host.get(i)
        st = storey_cache.get(cur.id()) if cur is not None else None
        for i in chain:
            storey_cache[i] = st
        return st

    # ---- elements
    attr_cache: dict[str, list[tuple[str, bool]]] = {}

    def attributes(e) -> list[tuple[str, bool]]:
        c = e.is_a()
        if c not in attr_cache:
            attr_cache[c] = [(a.name(), "LengthMeasure" in str(a.type_of_attribute()))
                             for a in schema.declaration_by_name(c).as_entity().all_attributes()
                             if a.name() not in SKIP_ATTRS]
        return attr_cache[c]

    def plain(v, scale):
        if isinstance(v, bool) or isinstance(v, str):
            return v
        if isinstance(v, (int, float)):
            return v * scale if scale != 1 else v
        if isinstance(v, (tuple, list)) and all(isinstance(x, (int, float, str, bool)) for x in v):
            return [plain(x, scale) for x in v]
        return None

    def element_rows():
        for e in objs:
            try:
                info = e.get_info(recursive=False)
            except Exception:  # noqa: BLE001 - an attribute pointing at something that is not there
                info = {k: getattr(e, k, None) for k in ("Name", "Description", "ObjectType", "Tag", "LongName")}
            attrs = {}
            for name, is_len in attributes(e):
                v = plain(info.get(name), ls if is_len else 1)
                if v is not None:
                    attrs[name] = v
            t = type_of.get(e.id())
            pt = info.get("PredefinedType")
            if pt in (None, "NOTDEFINED") and t is not None:
                pt = getattr(t, "PredefinedType", None) or pt
            is_product = e.is_a("IfcProduct")
            st = storey_of(e) if is_product else None
            i = e.id()
            parent = whole.get(i) or nest.get(i) or container.get(i) or host.get(i)
            yield (file_id, keys[i], i, e.is_a(), info.get("Name"), info.get("LongName"), info.get("ObjectType"),
                   info.get("Tag"), pt, info.get("Description"), st.Name if st is not None else None, key(st),
                   key(parent), key(t), t.Name if t is not None else None, js(attrs))

    n_el = _chunks(db, "jstore.ifc_element", EL_COLS, element_rows())
    n_rel = _chunks(db, "jstore.ifc_relation", REL_COLS, rels)
    del rels

    # ---- geometry in a forked child (it inherits the parsed model), while this process writes the properties
    shaped = {e.id(): keys[e.id()] for e in objs if getattr(e, "Representation", None) is not None}
    budget = float(os.environ.get("JSTORE_IFC_GEOM_BUDGET_S", "600"))
    threads = int(os.environ.get("JSTORE_IFC_THREADS", "0")) or min(8, os.cpu_count() or 1)
    t_geom = time.time()

    def spawn(ranges: list[tuple[int, int]]):
        rx, tx = mp.get_context("fork").Pipe(duplex=False)
        p = mp.get_context("fork").Process(target=_geometry_child, args=(f, file_id, shaped, threads, ranges, tx))
        p.start()
        tx.close()
        return p, rx

    pending = [(a, min(a + GEOM_CHUNK, len(shaped))) for a in range(0, len(shaped), GEOM_CHUNK)] if budget > 0 else []
    proc, rx = spawn(pending) if pending else (None, None)

    # ---- properties and quantities
    props = Props(scales)

    def definitions(d):
        if isinstance(d, (tuple, list)):          # IFC4 IfcPropertySetDefinitionSet
            return list(d)
        if isinstance(d, ifcopenshell.entity_instance) and d.id() == 0:
            return list(d.wrappedValue)
        return [d] if d is not None else []

    def property_rows():
        for r in all_of("IfcRelDefinesByProperties"):
            rows = [x for d in definitions(r.RelatingPropertyDefinition) for x in props.rows(d)]
            if not rows:
                continue
            for e in r.RelatedObjects or ():
                k = key(e)
                if k:
                    for row in rows:
                        yield (file_id, k, *row, False)
        typed = collections.defaultdict(list)
        for i, t in type_of.items():
            typed[t.id()].append(keys[i])
        for t in all_of("IfcTypeObject"):
            rows = [x for d in (t.HasPropertySets or ()) for x in props.rows(d)]
            if not rows:
                continue
            for row in rows:
                yield (file_id, keys[t.id()], *row, False)
            for k in typed.get(t.id(), ()):
                for row in rows:
                    yield (file_id, k, *row, True)

    n_prop = _chunks(db, "jstore.ifc_property", PROP_COLS, property_rows())

    # ---- pages: one per storey, in elevation order
    def elevation(st) -> float | None:
        if st.Elevation is not None:
            return float(st.Elevation) * ls
        try:
            import ifcopenshell.util.placement as up
            return float(up.get_local_placement(st.ObjectPlacement)[2][3]) * ls
        except Exception:  # noqa: BLE001
            return None

    storeys = sorted(((elevation(s), s) for s in all_of("IfcBuildingStorey")),
                     key=lambda p: (p[0] is None, p[0] or 0.0, p[1].id()))
    project = next(iter(all_of("IfcProject")), None)
    pages = [(file_id, n, s.Name, "m", js({"elevation_m": el, "guid": keys[s.id()], "long_name": s.LongName}))
             for n, (el, s) in enumerate(storeys)]
    if not pages:                                 # a model without storeys is still one page
        pages = [(file_id, 0, getattr(project, "Name", None), "m", js({}))]
    db.copy("jstore.page", ["file_id", "page_no", "name", "unit", "meta"], pages)
    db.copy("jstore.frame", ["file_id", "page_no", "m", "method", "residual", "n_anchors"],
            [(file_id, p[1], arr([1, 0, 0, 0, 1, 0]), "native", 0.0, 0) for p in pages])

    # ---- wait for the geometry
    gmeta: dict = {"shapes": len(shaped), "measured": 0, "missing": len(shaped), "status": "none" if shaped else "empty"}
    if proc is not None:
        cur, crashed = 0, []
        while True:
            left = budget - (time.time() - t_geom)
            try:
                msg = rx.recv() if rx.poll(max(left, 0.0)) else ("timeout",)
            except EOFError:                      # the child died (crash in the geometry kernel, memory cap)
                msg = ("died",)
            if msg[0] == "range":
                cur = msg[1]
                continue
            if proc.is_alive() and msg[0] != "recycle":
                proc.kill()
            proc.join(5)
            if msg[0] == "recycle":
                pending = pending[msg[1]:]
            elif msg[0] == "died":
                lo, hi = pending[cur]
                if cur:                           # it may only have run out of memory: once more in a fresh process
                    pending = pending[cur:]
                elif hi - lo > GEOM_MIN:          # first range of a fresh process: narrow down what kills it
                    step = max(GEOM_MIN, (hi - lo) // 5)
                    pending = [(a, min(a + step, hi)) for a in range(lo, hi, step)] + pending[1:]
                else:
                    crashed.append(f"{lo}-{hi}")
                    pending = pending[1:]
            if msg[0] in ("recycle", "died") and pending and time.time() - t_geom < budget:
                proc, rx = spawn(pending)
                cur = 0
                continue
            gmeta["status"] = {"ok": "ok", "died": "ok", "recycle": "ok",
                               "timeout": f"stopped after the {budget:.0f} s budget"}.get(msg[0]) or f"error: {msg[2]}"
            if msg[0] in ("recycle", "died") and pending:
                gmeta["status"] = f"stopped after the {budget:.0f} s budget"
            break
        if crashed:
            gmeta["crashed"] = crashed
            gmeta["status"] += f", {len(crashed)} group(s) of up to {GEOM_MIN} shapes skipped after repeated crashes"
        got = db.one("select count(*) n from jstore.ifc_geometry where file_id = :f", f=file_id)["n"]
        # An assembly without a body of its own (a layered wall exported as parts, a stair made of flights)
        # is measured as the sum of its parts.
        wholes = [keys[i] for i in {w.id() for w in whole.values()} if i in keys and f.by_id(i).is_a("IfcElement")]
        parts = sum(len(db.run(ASSEMBLY_SQL, f=file_id, w=wholes)) for _ in range(3)) if wholes and got else 0
        have = {r["guid"] for r in db.run("select guid from jstore.ifc_geometry where file_id = :f", f=file_id)}
        lacking = collections.Counter(f.by_id(i).is_a() for i, k in shaped.items() if k not in have)
        gmeta.update(measured=got, from_parts=parts, missing=sum(lacking.values()),
                     missing_classes=dict(lacking.most_common(8)),   # annotations, axis-only objects, failures
                     seconds=round(time.time() - t_geom, 1))

    app = next(iter(all_of("IfcApplication")), None)
    counts = collections.Counter(e.is_a() for e in objs)
    return dict(
        schema=f.schema_identifier, project=getattr(project, "Name", None) or getattr(project, "LongName", None),
        units=_unit_name(f) or "metre", length_unit=_unit_name(f), length_scale=ls,
        area_scale=scales["AREAUNIT"], volume_scale=scales["VOLUMEUNIT"],
        application=getattr(app, "ApplicationFullName", None),
        application_version=getattr(app, "Version", None),
        classes=dict(counts.most_common()),
        storeys=[{"name": s.Name, "elevation_m": el, "guid": keys[s.id()]} for el, s in storeys],
        rows={"ifc_element": n_el, "ifc_property": n_prop, "ifc_relation": n_rel},
        **({"repaired": repaired} if repaired else {}), **({"relations_skipped": skipped} if skipped else {}),
        geometry=gmeta, seconds={"open": round(t_open, 1), "total": round(time.time() - t0, 1)})
