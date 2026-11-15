"""Vector rebuild of an image upload: plan.svg for "Compare with original" and the 3D view, and room
outlines that are real polygons instead of label boxes.

`apply(out_dir, drawing)` runs after the drawing JSON of an image upload is written (quick mode: after our
parse; full mode: after the store's index is materialised). It is idempotent and never fails the upload:
any error leaves the drawing as it was.

Geometry stays in the image's pixel frame, so plan.svg overlays original.png exactly and every percentage
coordinate means the same point on both.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VECTOR_FILE = "vector.json"
PLAN_FILE = "plan.svg"
RASTER_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _vectorizer():
    from aura import vectorize

    return vectorize.vectorize_image, vectorize.plan_svg


def _vector(out_dir: Path) -> dict | None:
    """The vector plan of out_dir/original.png, computed once and cached as vector.json."""
    cached = out_dir / VECTOR_FILE
    src = out_dir / "original.png"
    if not src.is_file():
        return None
    if cached.is_file() and cached.stat().st_mtime >= src.stat().st_mtime:
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except ValueError:
            pass
    vectorize_image, _ = _vectorizer()
    vec = vectorize_image(src)
    tmp = cached.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(vec), encoding="utf-8")
    os.replace(tmp, cached)
    return vec


def _write_svg(out_dir: Path, vec: dict) -> None:
    _, plan_svg = _vectorizer()
    tmp = out_dir / (PLAN_FILE + ".tmp")
    tmp.write_text(plan_svg(vec), encoding="utf-8")
    os.replace(tmp, out_dir / PLAN_FILE)


def _ring(points: Any) -> list[list[float]] | None:
    """Outer ring of a vector room as [[x, y], ...] (accepts a plain ring or {"outer": [...]})."""
    if isinstance(points, dict):
        points = points.get("outer")
    if not isinstance(points, list) or len(points) < 3:
        return None
    return [[float(p[0]), float(p[1])] for p in points]


def refine_rooms(drawing: dict, vec: dict) -> dict:
    """Room outlines from the vector plan: each labelled room takes the enclosed region its label sits in.
    Several labels in one region (an open kitchen-living) split it by nearest label (Voronoi cells clipped to
    the region). A room keeps its outline when no region holds its label or the region is implausibly large.
    Areas are computed only when the drawing has a scale, and never replace a printed area."""
    from shapely.geometry import MultiPoint, Point, Polygon
    from shapely.ops import voronoi_diagram

    rooms = drawing.get("rooms") or []
    w, h = float(vec.get("width") or 0), float(vec.get("height") or 0)
    if not rooms or w <= 0 or h <= 0:
        return drawing
    regions: list[Polygon] = []
    for vr in vec.get("rooms") or []:
        ring = _ring(vr.get("points") if isinstance(vr, dict) else vr)
        if ring is None:
            continue
        poly = Polygon([(x / w * 100.0, y / h * 100.0) for x, y in ring]).buffer(0)
        if poly.is_empty or poly.area <= 0:
            continue
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.area > 0.6 * 100 * 100:  # the sheet's outside or the whole building: not a room
            continue
        regions.append(poly)
    if not regions:
        return drawing

    def label_pt(r: dict) -> Point | None:
        at = r.get("label_at")
        if isinstance(at, (list, tuple)) and len(at) == 2:
            return Point(float(at[0]), float(at[1]))
        pts = r.get("points") or []
        if len(pts) >= 3:
            return Polygon(pts).representative_point()
        return None

    by_region: dict[int, list[int]] = {}
    for i, r in enumerate(rooms):
        p = label_pt(r)
        if p is None:
            continue
        hits = [k for k, g in enumerate(regions) if g.contains(p)]
        if hits:
            k = min(hits, key=lambda k: regions[k].area)  # innermost region holding the label
            by_region.setdefault(k, []).append(i)

    mx, my = float(drawing.get("m_per_pct_x") or 0), float(drawing.get("m_per_pct_y") or 0)
    out_rooms = [dict(r) for r in rooms]
    for k, idxs in by_region.items():
        region = regions[k]
        shapes: dict[int, Polygon] = {}
        if len(idxs) == 1:
            shapes[idxs[0]] = region
        else:
            pts = {i: label_pt(rooms[i]) for i in idxs}
            cells = voronoi_diagram(MultiPoint(list(pts.values())), envelope=region.envelope.buffer(1))
            for i, p in pts.items():
                cell = next((c for c in cells.geoms if c.contains(p)), None)
                if cell is not None:
                    part = cell.intersection(region)
                    if part.geom_type == "MultiPolygon":
                        part = max(part.geoms, key=lambda g: g.area)
                    if part.geom_type == "Polygon" and part.area > 0:
                        shapes[i] = part
        for i, shape in shapes.items():
            shape = shape.simplify(0.05, preserve_topology=True)
            ring = [[round(x, 3), round(y, 3)] for x, y in list(shape.exterior.coords)[:-1]]
            if len(ring) < 3:
                continue
            room = out_rooms[i]
            room["points"] = ring
            if mx > 0 and my > 0 and (room.get("area_m2") is None or room.get("area_source") == "outline"):
                room["area_m2"] = round(shape.area * mx * my, 2)
                room["area_source"] = "outline"
    return drawing | {"rooms": out_rooms}


def apply(out_dir: Path, drawing: dict) -> dict:
    """plan.svg + polygon rooms for an image upload; the drawing unchanged on any failure."""
    try:
        if not (out_dir / "original.png").is_file():
            return drawing
        vec = _vector(out_dir)
        if not vec or not vec.get("walls"):
            return drawing
        images = dict(drawing.get("images") or {})
        base = f"/drawings/{drawing['id']}"
        orig = images.get("original") or f"{base}/original.png"
        images.update(rebuilt=f"{base}/{PLAN_FILE}", original=orig, original_alignment="exact")
        out = refine_rooms(drawing | {"images": images}, vec)
        w, h = float(vec["width"]), float(vec["height"])
        labels = [{"x": r["label_at"][0] / 100 * w, "y": r["label_at"][1] / 100 * h, "text": r.get("name") or r.get("num")}
                  for r in out.get("rooms") or [] if isinstance(r.get("label_at"), (list, tuple))]
        _write_svg(out_dir, vec | {"labels": labels})
        return out
    except Exception:  # noqa: BLE001 - a rebuild is a bonus; the upload stays usable without it
        log.exception("vector rebuild failed for %s", drawing.get("id"))
        return drawing


def apply_file(out_dir: Path) -> bool:
    """`apply` on out_dir/drawing.json in place (atomic write). True when the drawing changed."""
    path = out_dir / "drawing.json"
    try:
        if not path.is_file():
            return False
        drawing = json.loads(path.read_text(encoding="utf-8"))
        new = apply(out_dir, drawing)
        if new == drawing:
            return False
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 - never fails the upload
        log.exception("vector rebuild could not update %s", path)
        return False
