"""Layer 1 for raster images (PNG, JPG): one page, every OCR text line, optionally every detection candidate.

OCR is RapidOCR (PP-OCR models on onnxruntime), run on overlapping tiles so small room labels keep their pixels;
small images are enlarged first.  Each text line keeps its box, its exact outline (quad), the confidence and the
reading direction (`rot`, degrees counter-clockwise: 90 = reads bottom to top).  When most lines are sideways or
upside down, the image is turned upright, read again, and the better reading is kept (boxes always in the pixels
of the stored page image); the turn is recorded in meta.rotation.

`ocr_image()` is also used by the PDF reader for scanned pages.
"""
from __future__ import annotations

import os
import shutil
from importlib import metadata
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from ..db import DB, arr, js
from ..frames import scales_in, trusted_scale

Image.MAX_IMAGE_PIXELS = None          # scans of large sheets are legitimate here
TILE, OVERLAP = 1280, 200              # px; a text line longer than the overlap can be cut at a tile border
MIN_LONG_SIDE = 1280                   # smaller images are enlarged (at most MAX_UPSCALE) before reading
MAX_UPSCALE = 2.5
MIN_CONF = 0.3                         # below this the recogniser output is noise; everything above is stored
MAX_TILES = 64
WORD_COLUMNS = ["file_id", "page_no", "text", "conf", "x0", "y0", "x1", "y1", "engine", "geom", "rot", "quad"]

_engine = None


def engine_name() -> str:
    try:
        v = metadata.version("rapidocr-onnxruntime")
    except metadata.PackageNotFoundError:
        v = "?"
    return f"rapidocr-onnxruntime {v} (PP-OCRv4 det+rec, mobile cls)"


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        n = int(os.environ.get("JSTORE_OCR_THREADS", "2"))
        _engine = RapidOCR(intra_op_num_threads=n, inter_op_num_threads=1, max_side_len=100000)
    return _engine


def _positions(size: int, tile: int, overlap: int) -> list[int]:
    if size <= tile:
        return [0]
    n = int(np.ceil((size - overlap) / (tile - overlap)))
    return [int(round(v)) for v in np.linspace(0, size - tile, n)]


def _read_tile(eng, img: np.ndarray) -> list[tuple]:
    """-> [(quad 4x2 float, text, conf, rot)] for one tile, tile pixels."""
    boxes, _ = eng.auto_text_det(img)
    if boxes is None:
        return []
    crops = eng.get_crop_img_list(img, boxes)
    crops, cls_res, _ = eng.text_cls(crops)
    rec, _ = eng.text_rec(crops, False)
    out = []
    for box, cls, r in zip(boxes, cls_res, rec):
        text, conf = r[0], float(r[1])
        if not text.strip() or conf < MIN_CONF:
            continue
        q = np.asarray(box, dtype=float)
        w = max(np.linalg.norm(q[0] - q[1]), np.linalg.norm(q[2] - q[3]))
        h = max(np.linalg.norm(q[0] - q[3]), np.linalg.norm(q[1] - q[2]))
        tall = h >= 1.5 * w                       # the engine turns such crops 90 degrees counter-clockwise
        flipped = str(cls[0]) == "180" and float(cls[1]) >= eng.text_cls.cls_thresh   # only then was it turned
        rot = (90 if flipped else 270) if tall else (180 if flipped else 0)
        out.append((q, text.strip(), conf, rot))
    return out


def _merge(items: list[dict]) -> list[dict]:
    """Drop the copies that overlapping tiles produce: of two boxes covering each other keep the longer reading."""
    items = sorted(items, key=lambda d: (-(d["x1"] - d["x0"]) * (d["y1"] - d["y0"]), -d["conf"]))
    kept: list[dict] = []
    cell = 256
    grid: dict[tuple, list[int]] = {}
    for d in items:
        a = max(1e-6, (d["x1"] - d["x0"]) * (d["y1"] - d["y0"]))
        cells = [(i, j) for i in range(int(d["x0"] // cell), int(d["x1"] // cell) + 1)
                 for j in range(int(d["y0"] // cell), int(d["y1"] // cell) + 1)]
        dup = False
        for k in {k for c in cells for k in grid.get(c, [])}:
            o = kept[k]
            if o["tile"] == d["tile"]:
                continue
            iw = min(d["x1"], o["x1"]) - max(d["x0"], o["x0"])
            ih = min(d["y1"], o["y1"]) - max(d["y0"], o["y0"])
            if iw > 0 and ih > 0 and iw * ih / a >= 0.5:
                dup = True
                break
        if not dup:
            kept.append(d)
            for c in cells:
                grid.setdefault(c, []).append(len(kept) - 1)
    return sorted(kept, key=lambda d: (round(d["y0"] / 12), d["x0"]))


def _read(img: np.ndarray) -> list[dict]:
    """One reading of an image as it is.  -> text lines in the pixels of `img`."""
    eng = _get_engine()
    h, w = img.shape[:2]
    f = min(MAX_UPSCALE, MIN_LONG_SIDE / max(h, w)) if max(h, w) < MIN_LONG_SIDE else 1.0
    if f > 1.01:
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    else:
        f = 1.0
    H, W = img.shape[:2]
    tile = TILE
    while len(_positions(H, tile, OVERLAP)) * len(_positions(W, tile, OVERLAP)) > MAX_TILES:
        tile *= 2                                  # enormous scans: coarser tiles (the detector scales them down)
    out = []
    for ty in _positions(H, tile, OVERLAP):
        for tx in _positions(W, tile, OVERLAP):
            t = np.ascontiguousarray(img[ty:ty + tile, tx:tx + tile])
            th, tw = t.shape[:2]
            if th < 8 or tw < 8 or float(t.std()) < 2.0:      # blank tile
                continue
            for q, text, conf, rot in _read_tile(eng, t):
                x0, y0, x1, y1 = q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()
                inner = ((x0 <= 2 and tx > 0) or (y0 <= 2 and ty > 0) or (x1 >= tw - 3 and tx + tw < W)
                         or (y1 >= th - 3 and ty + th < H))
                if inner and max(x1 - x0, y1 - y0) < OVERLAP * 0.9:
                    continue                       # cut at an inner border; the neighbouring tile holds it whole
                q = (q + (tx, ty)) / f
                out.append(dict(text=text, conf=conf, rot=rot, quad=q, tile=(tx, ty),
                                x0=float(q[:, 0].min()), y0=float(q[:, 1].min()),
                                x1=float(q[:, 0].max()), y1=float(q[:, 1].max())))
    return _merge(out)


def _dominant_rotation(lines: list[dict]) -> tuple[int, float]:
    votes: dict[int, float] = {}
    for d in lines:
        if d["conf"] >= 0.6 and len(d["text"]) >= 3:
            votes[d["rot"]] = votes.get(d["rot"], 0.0) + 1
    n = sum(votes.values())
    if n < 5:
        return 0, 0.0
    rot = max(votes, key=votes.get)
    return rot, votes[rot] / n


def _score(lines: list[dict]) -> float:
    return sum(d["conf"] * len(d["text"]) for d in lines if d["conf"] >= 0.5)


_TURN = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _unturn(lines: list[dict], rot: int, w: int, h: int) -> list[dict]:
    """Lines read on the image turned clockwise by `rot` -> pixels of the original (w x h) image."""
    for d in lines:
        q = d["quad"]
        x, y = q[:, 0].copy(), q[:, 1].copy()
        if rot == 90:
            q = np.stack([y, h - x], axis=1)
        elif rot == 180:
            q = np.stack([w - x, h - y], axis=1)
        else:
            q = np.stack([w - y, x], axis=1)
        d.update(quad=q, rot=(d["rot"] + rot) % 360, x0=float(q[:, 0].min()), y0=float(q[:, 1].min()),
                 x1=float(q[:, 0].max()), y1=float(q[:, 1].max()))
    return lines


def ocr_image(img: np.ndarray) -> tuple[list[dict], dict]:
    """Every text line of a BGR or grey image.  -> (lines, info); info.rotation = clockwise turn that makes the
    text upright (0 when the image is upright already)."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    lines = _read(img)
    rot, share = _dominant_rotation(lines)
    info = dict(engine=engine_name(), rotation=0, lines=len(lines))
    if rot and share >= 0.6:
        turned = _unturn(_read(cv2.rotate(img, _TURN[rot])), rot, w, h)
        info.update(rotation=rot, rotation_share=round(share, 2), reread=_score(turned) > _score(lines))
        if info["reread"]:
            lines = turned
        info["lines"] = len(lines)
    return lines, info


def word_rows(file_id: str, page_no: int, lines: list[dict], engine: str) -> list[tuple]:
    rows = []
    for d in lines:
        cx, cy = (d["x0"] + d["x1"]) / 2, (d["y0"] + d["y1"]) / 2
        rows.append((file_id, page_no, d["text"], round(d["conf"], 4), d["x0"], d["y0"], d["x1"], d["y1"], engine,
                     f"SRID=0;POINT({cx!r} {cy!r})", d["rot"], arr(np.round(d["quad"], 1).ravel())))
    return rows


def store_words(db: DB, file_id: str, page_no: int, lines: list[dict], engine: str) -> int:
    return db.copy("jstore.raster_word", WORD_COLUMNS, word_rows(file_id, page_no, lines, engine))


def scale_texts(texts) -> list[dict]:
    """Scale statements among texts, each once."""
    seen, out = set(), []
    for t in texts:
        for s in scales_in(t):
            if (s["text"], s["labelled"]) not in seen:
                seen.add((s["text"], s["labelled"]))
                out.append(s)
    return out


def load(path: Path) -> tuple[np.ndarray, Image.Image, bool]:
    """-> (BGR array on white, the PIL image, changed): changed = the pixels differ from the file as stored
    (EXIF turn applied, transparency flattened, palette or 16-bit converted)."""
    im = Image.open(path)
    changed = False
    if im.getexif().get(0x0112, 1) not in (0, 1):
        im = ImageOps.exif_transpose(im)
        changed = True
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
        changed = True
    elif im.mode not in ("RGB", "L"):
        if im.mode.startswith("I;16") or im.mode in ("I", "F"):
            a = np.asarray(im, dtype=np.float64)
            a = (a - a.min()) / max(1e-9, a.max() - a.min()) * 255
            im = Image.fromarray(a.astype(np.uint8), "L")
        else:
            im = im.convert("RGB")
        changed = True
    a = np.asarray(im)
    bgr = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR) if a.ndim == 2 else cv2.cvtColor(a, cv2.COLOR_RGB2BGR)
    return bgr, im, changed


def page_image(file_id: str, path: Path, im: Image.Image, changed: bool) -> str:
    """Put the page image under renders/<file_id>/ and return its path relative to the store directory.
    An unchanged file is linked (or copied) under its own extension; a changed one is written as PNG."""
    from ..pipeline import store_dir
    d = store_dir() / "renders" / file_id
    d.mkdir(parents=True, exist_ok=True)
    ext = ".png" if changed or path.suffix.lower() == ".png" else ".jpg"
    out = d / f"p0{ext}"
    for old in d.glob("p0.*"):
        old.unlink()
    if changed:
        im.save(out, "PNG", compress_level=3)
    else:
        try:
            os.link(path, out)
        except OSError:
            shutil.copyfile(path, out)
    return str(out.relative_to(store_dir()))


def detect(db: DB, file_id: str, page_no: int, image_path: Path) -> dict:
    """Optional door / window / fixture candidates from Gemini; every returned box is stored."""
    from .. import gemini_detect
    from shapely.geometry import box as sbox
    res = gemini_detect.detect(image_path)
    db.run("delete from jstore.raster_detection where file_id = :f and page_no = :p", f=file_id, p=page_no)
    rows = [(file_id, page_no, o["label"], o.get("score"), *o["box"], res["detector"],
             js({k: o[k] for k in ("view", "merged_from") if k in o}), sbox(*o["box"]).wkb_hex)
            for o in res["objects"] if o["box"][2] > o["box"][0] and o["box"][3] > o["box"][1]]
    db.copy("jstore.raster_detection", ["file_id", "page_no", "class", "score", "x0", "y0", "x1", "y1", "detector",
                                        "attrs", "geom"], rows)
    return dict(detector=res["detector"], objects=len(rows), calls=res["calls"], cached=res["cached"],
                crop=res["crop"], errors=res["errors"])


def ingest(db: DB, file_id: str, path: Path) -> dict:
    """Read one image into page / raster_word (and raster_detection when JSTORE_GEMINI_DETECT=1)."""
    from ..pipeline import store_dir
    bgr, im, changed = load(path)
    h, w = bgr.shape[:2]
    for table in ("raster_word", "raster_detection", "page", "frame"):
        db.run(f"delete from jstore.{table} where file_id = :f", f=file_id)
    image = page_image(file_id, path, im, changed)
    dpi = im.info.get("dpi")
    lines, info = ocr_image(bgr)
    scales = scale_texts(d["text"] for d in lines)
    pmeta = dict(scales=scales, rotation=info["rotation"], ocr_lines=len(lines), mode=im.mode)
    if dpi and float(dpi[0]) > 1:
        pmeta["dpi"] = [round(float(dpi[0]), 2), round(float(dpi[1]), 2)]
    db.run("""insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image, meta)
              values (:f, 0, :n, :w, :h, 'px', :img, :iw, :ih, cast(:t as double precision[]), cast(:m as jsonb))""",
           f=file_id, n=path.stem, w=float(w), h=float(h), iw=w, ih=h, img=image, t=arr([1, 0, 0, 0, 1, 0]), m=js(pmeta))
    store_words(db, file_id, 0, lines, info["engine"])
    meta = dict(width=w, height=h, ocr=info, scales=scales, normalised=changed)
    if "dpi" in pmeta:
        meta["dpi"] = pmeta["dpi"]
    if trusted_scale(scales):
        meta["scale"] = trusted_scale(scales)["text"]
    if os.environ.get("JSTORE_GEMINI_DETECT") == "1":
        try:
            meta["detection"] = detect(db, file_id, 0, store_dir() / image)
        except Exception as e:  # noqa: BLE001 - detection is optional; the page and its words stay
            meta["detection"] = dict(error=repr(e)[:300])
    return meta
