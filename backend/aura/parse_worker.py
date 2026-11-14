"""Parse one uploaded file in a spawned child process with hard resource limits.

The child sets RLIMIT_AS (1.5 GB) and RLIMIT_CPU (60 s) before touching the file; the parent
(jobs.py) joins for 60 s and then kills it. A thread could not be killed: CPU-bound ezdxf or
Pillow work would pin the only container.

Outputs, in the upload folder: drawing.json (contract Drawing + evidence/texts), plan.svg,
model.json for DXF; drawing.json + original.png + thumb.png for raster.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import resource
import traceback
from pathlib import Path

AS_LIMIT = 1536 * 1024 * 1024
CPU_LIMIT_S = 60
MAX_PIXELS = 40_000_000


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (AS_LIMIT, AS_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_S, CPU_LIMIT_S + 5))


def _write_json(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False))


def parse_dxf(src: Path, out: Path, drawing_id: str, meta: dict) -> dict:
    from aura.render.drawing import build_drawing
    from aura.render.svg import render_svg
    from aura.solvers import swiss

    model = swiss.load(src, drawing_id)
    model.id = drawing_id
    drawing = build_drawing(model, source="upload", original=None, thumbnail=None)
    drawing.update(meta)
    drawing["title"] = meta.get("title") or drawing.get("title") or "Uploaded DXF"
    caps = drawing.get("capabilities") or []
    # A DXF without Swiss layers: rendered, nothing to count (answers abstain not_supported_yet).
    if not model.rooms and not any(model.boxes()[s] for s in swiss.BOX_SUBJECTS):
        caps = []
    drawing["capabilities"] = caps
    drawing["evidence"] = {s: model.boxes()[s] for s in swiss.BOX_SUBJECTS if s in caps}
    drawing["texts"] = [{"id": b["id"], "text": b["note"], "x": b["x"], "y": b["y"], "w": b["w"],
                         "h": b["h"]} for b in swiss.text_boxes(model, model.texts)]
    (out / "plan.svg").write_text(render_svg(model), encoding="utf-8")
    if caps:
        _write_json(out / "model.json", model.to_json())
    _write_json(out / "drawing.json", drawing)
    return {"rooms": len(model.rooms), "capabilities": caps}


def parse_raster(src: Path, out: Path, drawing_id: str, meta: dict) -> dict:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with Image.open(src) as im:
        w, h = im.size
        if w * h > MAX_PIXELS:
            raise ValueError("image larger than 40 MP")
        im = im.convert("RGB")
        im.save(out / "original.png", optimize=True)
        t = im.copy()
        t.thumbnail((480, 1920))
        t.save(out / "thumb.png", optimize=True)
    base = f"/drawings/{drawing_id}"
    drawing = {
        "contract_version": "1.1", "id": drawing_id, "title": meta.get("title") or "Uploaded image",
        "kind": "raster", "status": "ready", "source": "upload", "capabilities": [],
        "thumbnail": f"{base}/thumb.png",
        "frame": {"dxf_min": [0, 0], "dxf_max": [w, h], "margin_m": 0, "m_per_unit": 1,
                  "y_flip": True, "px": [w, h]},
        "m_per_pct_x": 0, "m_per_pct_y": 0,
        "images": {"rebuilt": f"{base}/original.png", "original": f"{base}/original.png",
                   "original_alignment": "exact"},
        "rooms": [], "units": [],
        "credit": {"text": "Uploaded by a user; deleted after 24 h.", "license": "user upload"},
        **meta,
    }
    _write_json(out / "drawing.json", drawing)
    return {"px": [w, h]}


def _child(kind: str, src: str, out: str, drawing_id: str, meta: dict, conn) -> None:
    try:
        _limits()
        fn = parse_dxf if kind == "dxf" else parse_raster
        res = fn(Path(src), Path(out), drawing_id, meta)
        conn.send({"ok": True, **res})
    except MemoryError:
        conn.send({"ok": False, "error": "The file needs more memory than allowed."})
    except Exception as e:  # noqa: BLE001 - report any parse failure to the parent
        traceback.print_exc()
        conn.send({"ok": False, "error": f"Could not read this file ({type(e).__name__})."})
    finally:
        conn.close()


def start(kind: str, src: Path, out: Path, drawing_id: str, meta: dict,
          target=_child) -> tuple[mp.Process, object]:
    """Spawn the child; returns (process, parent end of the pipe)."""
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=target, args=(kind, str(src), str(out), drawing_id, meta, child),
                    daemon=True)
    p.start()
    child.close()
    return p, parent
