"""Layer 1 for CAD: every DXF entity becomes one row.  No interpretation happens here.

What is kept per entity: handle, space (model or layout), layer, colour, line type, type, text, a 2D geometry
with curves flattened, and the exact parameters in `attrs` (radius, angles, text height, rotation, attributes,
measured dimension value ...).  Block references are stored twice: the INSERT itself, and every entity it
places, expanded to real coordinates with handle `<insert handle>/<n>` and `parent` = the insert.

DWG is converted with an external converter first (`dwg2dxf`, when installed).
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath
from ezdxf import recover
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from ..db import DB, arr, geom, js

UNITS = {0: ("unitless", None), 1: ("inch", 0.0254), 2: ("foot", 0.3048), 4: ("mm", 0.001), 5: ("cm", 0.01),
         6: ("m", 1.0), 8: ("microinch", 2.54e-8), 9: ("mil", 2.54e-5), 10: ("yard", 0.9144), 14: ("dm", 0.1)}
MAX_ROWS = 600_000        # per file, block expansions included
MAX_DEPTH = 4             # nested block references
COLUMNS = ["file_id", "handle", "space", "parent", "block", "dxftype", "layer", "color", "linetype", "closed",
           "n_vertices", "text", "geom", "attrs"]


def _xy(v):
    return (float(v[0]), float(v[1]))


def _ok(pts):
    return all(math.isfinite(x) and math.isfinite(y) for x, y in pts)


def _shape(pts, closed):
    """Flattened vertices -> Polygon (closed, 3+ distinct points) or LineString or Point."""
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if not pts or not _ok(pts):
        return None
    if closed and len(set(pts)) >= 3:
        try:
            return Polygon(pts)
        except ValueError:
            pass
    if len(pts) >= 2:
        return LineString(pts)
    return Point(pts[0])


def _flat(e, tol):
    return [_xy(v) for v in ezpath.make_path(e).flattening(tol)]


def convert(e, tol: float) -> tuple[object, dict, str | None, bool | None, int | None]:
    """-> (geometry, attrs, text, closed, n_vertices) for one entity; geometry None when it has none."""
    t = e.dxftype()
    d = e.dxf
    a: dict = {}
    if t == "LINE":
        return LineString([_xy(d.start), _xy(d.end)]), a, None, False, 2
    if t == "LWPOLYLINE":
        closed = bool(e.closed)
        raw = [(float(x), float(y), float(b)) for x, y, _, _, b in e.get_points("xyseb")]
        if any(b for _, _, b in raw):
            a["bulges"] = [round(b, 6) for _, _, b in raw]
        if d.hasattr("const_width") and d.const_width:
            a["width"] = float(d.const_width)
        return _shape(_flat(e, tol), closed), a, None, closed, len(raw)
    if t == "POLYLINE":
        closed = bool(e.is_closed)
        n = len(e.vertices)
        if e.is_2d_polyline or e.is_3d_polyline:
            return _shape(_flat(e, tol), closed), a, None, closed, n
        a["mode"] = e.get_mode()
        return _shape([_xy(v.dxf.location) for v in e.vertices], closed), a, None, closed, n
    if t == "CIRCLE":
        a.update(center=_xy(d.center), radius=float(d.radius))
        return _shape(_flat(e, tol), True), a, None, True, None
    if t == "ARC":
        a.update(center=_xy(d.center), radius=float(d.radius), start_angle=float(d.start_angle),
                 end_angle=float(d.end_angle))
        return _shape(_flat(e, tol), False), a, None, False, None
    if t == "ELLIPSE":
        a.update(center=_xy(d.center), major_axis=_xy(d.major_axis), ratio=float(d.ratio),
                 start_param=float(d.start_param), end_param=float(d.end_param))
        full = abs((d.end_param - d.start_param) - math.tau) < 1e-6
        return _shape(_flat(e, tol), full), a, None, full, None
    if t == "SPLINE":
        a.update(degree=int(d.degree), n_control=len(e.control_points), n_fit=len(e.fit_points))
        closed = bool(e.closed)
        return _shape(_flat(e, tol), closed), a, None, closed, None
    if t == "TEXT":
        has_align = (d.get("halign", 0) or d.get("valign", 0)) and d.hasattr("align_point")
        at = d.align_point if has_align else d.insert
        a.update(height=float(d.get("height", 0) or 0), rotation=float(d.get("rotation", 0) or 0),
                 halign=int(d.get("halign", 0)), valign=int(d.get("valign", 0)), style=d.get("style", None),
                 insert=_xy(d.insert))
        return Point(_xy(at)), a, (d.text or "").strip(), None, None
    if t == "MTEXT":
        a.update(height=float(d.get("char_height", 0) or 0), width=float(d.get("width", 0) or 0),
                 rotation=float(d.get("rotation", 0) or 0), attachment=int(d.get("attachment_point", 1)),
                 style=d.get("style", None), raw=e.text[:2000])
        return Point(_xy(d.insert)), a, e.plain_text().strip(), None, None
    if t in ("ATTRIB", "ATTDEF"):
        a.update(tag=d.get("tag", None), height=float(d.get("height", 0) or 0),
                 rotation=float(d.get("rotation", 0) or 0))
        return Point(_xy(d.insert)), a, (d.get("text", "") or "").strip(), None, None
    if t == "INSERT":
        a.update(name=d.name, xscale=float(d.get("xscale", 1)), yscale=float(d.get("yscale", 1)),
                 rotation=float(d.get("rotation", 0) or 0),
                 attribs={at.dxf.tag: at.dxf.text for at in e.attribs})
        return Point(_xy(d.insert)), a, None, None, None
    if t == "HATCH":
        polys = []
        for p in ezpath.from_hatch(e):
            pts = [_xy(v) for v in p.flattening(tol)]
            s = _shape(pts, True)
            if isinstance(s, Polygon):
                polys.append(s)
        a.update(pattern=d.get("pattern_name", None), solid=bool(d.get("solid_fill", 0)), n_paths=len(polys))
        g = polys[0] if len(polys) == 1 else (MultiPolygon(polys) if polys else None)
        return g, a, None, True, None
    if t == "DIMENSION":
        try:
            m = e.get_measurement()
            a["measurement"] = float(m) if isinstance(m, (int, float)) else [float(x) for x in m]
        except Exception:  # noqa: BLE001 - some dimension types have no measurement
            pass
        a.update(dimtype=int(d.get("dimtype", 0)), text_override=d.get("text", None))
        pts = [_xy(d.get(k)) for k in ("defpoint2", "defpoint3") if d.hasattr(k)]
        g = LineString(pts) if len(pts) == 2 and pts[0] != pts[1] else (Point(_xy(d.defpoint)) if d.hasattr("defpoint") else None)
        return g, a, d.get("text", None) or None, None, None
    if t == "POINT":
        return Point(_xy(d.location)), a, None, None, None
    if t in ("SOLID", "TRACE", "3DFACE"):
        pts = [_xy(d.get(k)) for k in ("vtx0", "vtx1", "vtx3", "vtx2") if d.hasattr(k)]
        return _shape(pts, True), a, None, True, len(pts)
    if t == "LEADER":
        return _shape([_xy(v) for v in e.vertices], False), a, None, False, len(e.vertices)
    if t == "VIEWPORT":
        cx, cy = _xy(d.center)
        w, h = float(d.width), float(d.height)
        a.update(view_center=_xy(d.get("view_center_point", (0, 0))), view_height=float(d.get("view_height", 0) or 0),
                 status=int(d.get("status", 0)), id=int(d.get("id", 0)))
        if a["view_height"]:
            a["scale"] = h / a["view_height"]      # paper units per model unit
        return Polygon([(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2),
                        (cx - w / 2, cy + h / 2)]), a, None, True, 4
    a["note"] = "geometry not converted"
    return None, a, None, None, None


def _rows(file_id, e, space, tol, counter, parent=None, block=None, key=None, depth=0):
    """Rows for one entity and, for an INSERT, everything it places."""
    handle = key or e.dxf.get("handle", None) or f"anon-{counter[0]}"
    counter[0] += 1
    try:
        g, a, text, closed, n = convert(e, tol)
    except Exception as ex:  # noqa: BLE001 - one bad entity must not lose the file
        g, a, text, closed, n = None, {"error": repr(ex)[:200]}, None, None, None
    t = e.dxftype()
    if g is not None and not g.is_valid and g.geom_type == "Polygon":
        a["invalid_polygon"] = True
    yield (file_id, handle, space, parent, (e.dxf.name if t == "INSERT" else block), t, e.dxf.get("layer", None),
           e.dxf.get("color", None), e.dxf.get("linetype", None), closed, n, text, geom(g), js(a))
    if t == "INSERT" and depth < MAX_DEPTH and counter[0] < MAX_ROWS:
        i = 0
        for at in e.attribs:
            yield from _rows(file_id, at, space, tol, counter, handle, e.dxf.name, f"{handle}/a{i}", depth + 1)
            i += 1
        try:
            virtual = list(e.virtual_entities())
        except Exception:  # noqa: BLE001 - broken block definition
            virtual = []
        for j, v in enumerate(virtual):
            if counter[0] >= MAX_ROWS:
                break
            yield from _rows(file_id, v, space, tol, counter, handle, e.dxf.name, f"{handle}/{j}", depth + 1)


def _extent(layout):
    """(xmin, ymin, xmax, ymax) of a layout from its straight geometry, or None."""
    xs, ys = [], []
    for e in layout:
        t = e.dxftype()
        try:
            if t == "LINE":
                pts = [e.dxf.start, e.dxf.end]
            elif t == "LWPOLYLINE":
                pts = list(e.get_points("xy"))
            elif t in ("CIRCLE", "ARC"):
                c, r = e.dxf.center, e.dxf.radius
                pts = [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
            elif t in ("TEXT", "MTEXT", "INSERT"):
                pts = [e.dxf.insert]
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        for p in pts:
            if math.isfinite(p[0]) and math.isfinite(p[1]):
                xs.append(float(p[0]))
                ys.append(float(p[1]))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def read_document(path: Path):
    try:
        return ezdxf.readfile(str(path)), False
    except (ezdxf.DXFError, OSError, UnicodeDecodeError, ValueError):
        doc, _ = recover.readfile(str(path))
        return doc, True


def dwg_to_dxf(path: Path, tmp: Path) -> Path:
    exe = shutil.which("dwg2dxf")
    if not exe:
        raise RuntimeError("DWG needs a converter: dwg2dxf is not installed")
    out = tmp / (path.stem + ".dxf")
    subprocess.run([exe, "-y", "-o", str(out), str(path)], check=False, capture_output=True, timeout=300)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("DWG conversion produced no output")
    return out


def ingest(db: DB, file_id: str, path: Path) -> dict:
    """Parse one DXF or DWG into cad_layer / cad_entity / page / frame.  -> meta for source_file."""
    meta: dict = {}
    tmp = None
    try:
        if path.suffix.lower() == ".dwg":
            tmp = Path(tempfile.mkdtemp(prefix="dwg_"))
            path = dwg_to_dxf(path, tmp)
            meta["converted_from"] = "dwg"
        doc, recovered = read_document(path)
    finally:
        pass
    if recovered:
        meta["recovered"] = True
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    unit_name, to_m = UNITS.get(code, (f"code {code}", None))
    meta.update(dxf_version=doc.dxfversion, units=unit_name, insunits=code, to_m=to_m)

    for table in ("cad_entity", "cad_layer", "page", "frame"):
        db.run(f"delete from jstore.{table} where file_id = :f", f=file_id)

    spaces = [("model", doc.modelspace())] + [(l.name, l) for l in doc.layouts if l.name != "Model"]
    counter = [0]
    page_no = 0
    total = 0
    for space, layout in spaces:
        ext = _extent(layout)
        if ext is None and space != "model":
            continue
        diag = math.hypot(ext[2] - ext[0], ext[3] - ext[1]) if ext else 1.0
        tol = max(diag * 2e-5, 1e-9)
        rows = []
        for e in layout:
            if counter[0] >= MAX_ROWS:
                meta["truncated"] = True
                break
            rows.extend(_rows(file_id, e, space, tol, counter))
        total += db.copy("jstore.cad_entity", COLUMNS, rows)
        if ext:
            db.run("""insert into jstore.page (file_id, page_no, name, width, height, unit, meta)
                      values (:f, :p, :n, :w, :h, 'unit', cast(:m as jsonb))""",
                   f=file_id, p=page_no, n=space, w=ext[2] - ext[0], h=ext[3] - ext[1],
                   m=js({"extent": list(ext)}))
            if space == "model":
                k = to_m or 1.0
                db.run("insert into jstore.frame (file_id, page_no, m, method) values (:f, :p, cast(:m as double precision[]), :how)",
                       f=file_id, p=page_no, m=arr([k, 0, 0, 0, k, 0]), how="units" if to_m else "assumed_metres")
            page_no += 1
    db.run("""insert into jstore.cad_layer (file_id, name, color, linetype, is_on, n_entities)
              select :f, l.name, l.color, l.linetype, l.is_on,
                     (select count(*) from jstore.cad_entity c where c.file_id = :f and c.layer = l.name)
              from json_to_recordset(cast(:layers as json)) as l(name text, color int, linetype text, is_on boolean)""",
           f=file_id, layers=js([dict(name=l.dxf.name, color=l.color, linetype=l.dxf.get("linetype", None),
                                      is_on=bool(l.is_on())) for l in doc.layers]))
    meta.update(entities=total, spaces=[s for s, _ in spaces], blocks=len([b for b in doc.blocks if not b.name.startswith("*")]))
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return meta
