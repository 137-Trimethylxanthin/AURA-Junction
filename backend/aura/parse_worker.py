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
import os
import resource
import sys
from pathlib import Path

AS_LIMIT = 4096 * 1024 * 1024
WORK_PIXELS = 80_000_000  # served copy of an uploaded image; below Pillow's own bomb warning (89 MP)
CPU_LIMIT_S = 60


FSIZE_LIMIT = 200 * 1024 * 1024  # no single output file (plan.svg, model.json) past 200 MB
NOFILE_LIMIT = 64
MAX_ITEMS = 3000  # countable objects in one uploaded DXF (the dataset's largest has ~600)
MAX_TEXTS = 20000
KEEP_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "TMPDIR", "OMP_NUM_THREADS")


class TooLarge(ValueError):
    """The model has more objects than an uploaded floor plan plausibly has."""


def _limits() -> None:
    """M4: the child holds no secrets (it never needs Settings) and gets hard resource limits."""
    for k in [k for k in os.environ if k not in KEEP_ENV]:  # second line of defence
        os.environ.pop(k, None)
    os.environ["OMP_NUM_THREADS"] = "1"
    try:  # not dumpable: no core dump, /proc/<pid>/{environ,mem} unreadable to the same UID
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except Exception:  # noqa: BLE001, S110 - not Linux: nothing to set
        pass
    resource.setrlimit(resource.RLIMIT_AS, (AS_LIMIT, AS_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_S, CPU_LIMIT_S + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_LIMIT, FSIZE_LIMIT))
    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(NOFILE_LIMIT, hard), hard))


def _write_json(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False))


def parse_dxf(src: Path, out: Path, drawing_id: str, meta: dict) -> dict:
    from aura.render.drawing import build_drawing
    from aura.render.svg import render_svg
    from aura.solvers import swiss

    model = swiss.load(src, drawing_id)
    if sum(len(v) for v in model.items.values()) > MAX_ITEMS or len(model.texts) > MAX_TEXTS:
        raise TooLarge  # H1: refused here, before any O(n) work reaches the web process
    model.id = drawing_id
    drawing = build_drawing(model, source="upload", original=None, thumbnail=None)
    drawing.update(meta)
    drawing["title"] = meta.get("title") or drawing.get("title") or "Uploaded DXF"
    # Not the dataset's credit: this is the user's own file (M3).
    drawing["credit"] = {"text": "Uploaded by a user; deleted after 24 hours or when you delete it.",
                         "license": "user upload", "license_name": "Your upload"}
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

    # No pixel limit on what is uploaded: this child (memory and CPU limited) decodes any size. The copy the
    # app serves and the main process reopens is scaled down to WORK_PIXELS, so nothing outside this child
    # ever decodes an unbounded image. `w, h` stay the source size; `px` is the size of the served copy.
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(src) as im:
        w, h = im.size
        if w * h > WORK_PIXELS:
            im.draft("RGB", (w, h))
            k = (WORK_PIXELS / (w * h)) ** 0.5
            im = im.convert("RGB").resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        else:
            im = im.convert("RGB")
        pw, ph = im.size
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
                  "y_flip": True, "px": [pw, ph]},
        "m_per_pct_x": 0, "m_per_pct_y": 0,
        "images": {"rebuilt": f"{base}/original.png", "original": f"{base}/original.png",
                   "original_alignment": "exact"},
        "rooms": [], "units": [],
        "credit": {"text": "Uploaded by a user; deleted after 24 h.", "license": "user upload"},
        **meta,
    }
    _write_json(out / "drawing.json", drawing)
    return {"px": [pw, ph], "source_px": [w, h]}


def parse_pdf(src: Path, out: Path, drawing_id: str, meta: dict) -> dict:
    from aura.solvers.pdf import render_pdf

    return render_pdf(src, out, drawing_id, meta)


def parse_ifc(src: Path, out: Path, drawing_id: str, meta: dict) -> dict:
    from aura.contract import CONTRACT_VERSION
    from aura.render.ifc import build_drawing, render_svg
    from aura.solvers import ifc

    model = ifc.load(src, storey=None, drawing_id=drawing_id, geometry=True)
    if sum(len(v) for v in model.elements.values()) > MAX_ITEMS * 4:
        raise TooLarge
    if model.frame is None:
        model.frame = {"min": [0.0, 0.0], "max": [20.0, 20.0], "scale": 40.0, "px": [800, 800]}
    credit = {
        "text": "Uploaded IFC building model; deleted after 24 hours or when you delete it.",
        "license": "user upload",
        "license_name": "Your upload",
    }
    drawing = build_drawing(model, source="upload", credit=credit)
    drawing["contract_version"] = CONTRACT_VERSION
    drawing.update(meta)
    raw_title = meta.get("title")
    if raw_title:
        drawing["title"] = f"{raw_title} · {model.storey}" if model.storey else raw_title
    drawing["source_ext"] = ".ifc"
    (out / "plan.svg").write_text(render_svg(model), encoding="utf-8")
    _write_json(out / "model.json", model.to_json())
    _write_json(out / "drawing.json", drawing)
    return {"rooms": len(model.rooms), "capabilities": drawing.get("capabilities") or []}


def _child(kind: str, src: str, out: str, drawing_id: str, meta: dict, conn) -> None:
    try:
        _limits()
        fn = {
            "dxf": parse_dxf,
            "pdf": parse_pdf,
            "ifc": parse_ifc,
        }.get(kind, parse_raster)
        res = fn(Path(src), Path(out), drawing_id, meta)
        conn.send({"ok": True, **res})
    except MemoryError:
        conn.send({"ok": False, "error": "The file needs more memory than allowed."})
    except TooLarge:
        conn.send({"ok": False, "error": (f"This drawing has more than {MAX_ITEMS:,} objects or "
                                          f"{MAX_TEXTS:,} texts; uploads are limited to one floor plan.")})
    except Exception as e:  # noqa: BLE001 - report any parse failure to the parent
        conn.send({"ok": False, "error": f"Could not read this file ({type(e).__name__})."})
    finally:
        conn.close()


_LAUNCHER: str | None = None


def _launcher() -> str:
    """A tiny exec wrapper so the spawned Python starts with a MINIMAL environment: secrets
    (GEMINI_API_KEY, AURA_PRESENTER_TOKEN, AURA_IP_SALT, ...) never enter the child process, so
    they are not in its /proc/<pid>/environ nor in its memory. `env -i` keeps the inherited pipe
    descriptors spawn needs; sys.path travels in spawn's own preparation data."""
    global _LAUNCHER
    if _LAUNCHER is None or not Path(_LAUNCHER).is_file():
        import shlex
        import stat
        import tempfile

        keep = " ".join(f'{k}="${{{k}:-}}"' for k in KEEP_ENV)
        fd, path = tempfile.mkstemp(prefix="aura-child-", suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write(f"#!/bin/sh\nexec /usr/bin/env -i {keep} {shlex.quote(sys.executable)} \"$@\"\n")
        os.chmod(path, stat.S_IRWXU)
        _LAUNCHER = path
    return _LAUNCHER


def start(kind: str, src: Path, out: Path, drawing_id: str, meta: dict,
          target=_child) -> tuple[mp.Process, object]:
    """Spawn the child; returns (process, parent end of the pipe).

    Isolation (M4): the child starts with a scrubbed environment (no secrets), hard rlimits, and
    is not dumpable (PR_SET_DUMPABLE=0: no core dumps, its /proc files are root-owned). It still
    runs as the SAME UID with the same filesystem access (the DB and other uploads); a real
    sandbox (separate user, seccomp, read-only mounts) is not set up here."""
    ctx = mp.get_context("spawn")
    if os.path.exists("/usr/bin/env"):
        ctx.set_executable(_launcher())  # every spawn in this process gets the scrubbed env
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=target, args=(kind, str(src), str(out), drawing_id, meta, child),
                    daemon=True)
    p.start()
    child.close()
    return p, parent
