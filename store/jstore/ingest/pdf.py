"""Layer 1 for PDF: every vector path, every word and the document structure, page by page.  No interpretation.

Coordinates are points with the origin at the top-left of the visible page (crop box, page rotation applied)
and y down, so `page.to_image` is a plain scale onto the rendered PNG.

  pdf_path   one row per painted subpath: kind (line | rect | curve, as pdfplumber classifies it), flattened
             geometry (closed subpaths as polygons), stroke / fill as '#rrggbb', line width, dash, closed.
  pdf_text   one row per word with box, font, size, reading direction (`rot`) and `line_no` (words printed as one
             line share it).
  page       size, render (pypdfium2), per-page meta: scale texts, counts, rotation, label, whether it is a scan.
  meta       page count, title / producer, bookmarks with target pages, page labels, link and other annotations,
             scale texts per page.

A page whose text layer is empty and which is covered by an image is a scan: its render goes through OCR and the
lines land in raster_word (pixels of the render) under that page number.

Guards: at most MAX_PATHS paths per page are stored (meta.path_cap, page.meta.paths_dropped) and a file gets
TIME_BUDGET seconds for content; pages after that still get a page row and a render (meta.pages_without_content).
"""
from __future__ import annotations

import math
import os
import struct
import time
from pathlib import Path

import numpy as np
import pdfplumber
import pypdfium2 as pdfium

from ..db import DB, arr, js
from ..frames import trusted_scale

MAX_PATHS = int(os.environ.get("JSTORE_PDF_MAX_PATHS", "200000"))      # per page
TIME_BUDGET = float(os.environ.get("JSTORE_PDF_SECONDS", "300"))       # per file, content extraction
RENDER_LONG_SIDE, RENDER_MAX_DPI = 3000, 200
BEZIER_STEPS = 8
MAX_ANNOTS = 2000
PATH_COLUMNS = ["file_id", "page_no", "seq", "kind", "stroke", "fill", "width", "dash", "closed", "n_vertices",
                "geom", "attrs"]
TEXT_COLUMNS = ["file_id", "page_no", "seq", "text", "x0", "y0", "x1", "y1", "font", "size", "geom", "line_no", "rot"]


# ------------------------------------------------------------------ small encoders
def wkb(pts: np.ndarray, closed: bool) -> str | None:
    """Hex WKB of a point, line or (closed, 3+ distinct points) polygon; much faster than building shapely objects."""
    n = len(pts)
    if n == 0 or not np.isfinite(pts).all():
        return None
    if n == 1:
        return (b"\x01" + struct.pack("<I", 1) + pts.astype("<f8").tobytes()).hex()
    if closed and n >= 3 and len(np.unique(pts, axis=0)) >= 3:
        if (pts[0] != pts[-1]).any():
            pts = np.vstack([pts, pts[:1]])
        return (b"\x01" + struct.pack("<III", 3, 1, len(pts)) + pts.astype("<f8").tobytes()).hex()
    return (b"\x01" + struct.pack("<II", 2, n) + pts.astype("<f8").tobytes()).hex()


def point_wkb(x: float, y: float) -> str:
    return (b"\x01" + struct.pack("<Idd", 1, x, y)).hex()


def colour(c) -> tuple[str | None, object]:
    """A PDF colour -> ('#rrggbb' or None, the raw value when it is not grey, RGB or CMYK)."""
    if c is None:
        return None, None
    if isinstance(c, (int, float)):
        c = (c,)
    try:
        v = [min(1.0, max(0.0, float(x))) for x in c]
    except (TypeError, ValueError):
        return None, str(c)[:80]                      # pattern or separation name
    if len(v) == 1:
        rgb = (v[0],) * 3
    elif len(v) == 3:
        rgb = v
    elif len(v) == 4:
        rgb = [(1 - v[i]) * (1 - v[3]) for i in range(3)]
    else:
        return None, v
    return "#" + "".join(f"{round(x * 255):02x}" for x in rgb), None


def flatten(path, ox: float, oy: float) -> tuple[np.ndarray, bool, bool]:
    """pdfplumber path [(op, points...)] -> (vertices, closed, has_curve)."""
    pts: list[tuple[float, float]] = []
    closed = curved = False
    for seg in path:
        op = seg[0]
        if op in ("m", "l"):
            pts.append(seg[1])
        elif op in ("c", "v", "y") and pts:
            curved = True
            p0 = pts[-1]
            if op == "c" and len(seg) >= 4:
                p1, p2, p3 = seg[1], seg[2], seg[3]
            elif op == "v":
                p1, p2, p3 = p0, seg[1], seg[2]
            else:
                p1, p2, p3 = seg[1], seg[2], seg[2]
            for i in range(1, BEZIER_STEPS + 1):
                t = i / BEZIER_STEPS
                u = 1 - t
                pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
        elif op == "h":
            closed = True
    a = np.asarray(pts, dtype=float).reshape(-1, 2) - (ox, oy)
    if len(a) >= 4 and (a[0] == a[-1]).all():
        closed = True
    return a, closed, curved


# ------------------------------------------------------------------ page content
def path_rows(file_id: str, page_no: int, page, ox: float, oy: float) -> tuple[list[tuple], int]:
    rows, dropped = [], 0
    objs = page.objects
    seq = 0
    for kind, key in (("line", "line"), ("rect", "rect"), ("curve", "curve")):
        for o in objs.get(key, []):
            if seq >= MAX_PATHS:
                dropped += 1
                continue
            pts, closed, curved = flatten(o.get("path") or [("m", p) for p in o.get("pts", [])], ox, oy)
            g = wkb(pts, closed)
            if g is None:
                continue
            a: dict = {}
            stroke = fill = None
            if o.get("stroke"):
                stroke, raw = colour(o.get("stroking_color"))
                if raw is not None:
                    a["stroke_raw"] = raw
            if o.get("fill"):
                fill, raw = colour(o.get("non_stroking_color"))
                if raw is not None:
                    a["fill_raw"] = raw
                if o.get("evenodd"):
                    a["evenodd"] = True
            if curved:
                a["bezier"] = True
            dash = o.get("dash")
            dash = None if not dash or not dash[0] else " ".join(f"{float(d):g}" for d in dash[0]) + f" /{float(dash[1] or 0):g}"
            lw = o.get("linewidth")
            rows.append((file_id, page_no, seq, kind, stroke, fill, float(lw) if lw is not None else None, dash,
                         closed, len(pts), g, js(a) if a else "{}"))
            seq += 1
    return rows, dropped


def _word_rot(chars: list[dict], upright: bool) -> int:
    if upright:
        return 0
    m = chars[0].get("matrix") if chars else None
    if not m:
        return 90
    return int(round(math.degrees(math.atan2(m[1], m[0])) / 5) * 5) % 360


def words_of(page, ox: float, oy: float) -> list[dict]:
    """Words with box, font, size and reading direction; rotated words are re-read along their own direction."""
    out = []
    try:
        ws = page.extract_words(extra_attrs=["fontname", "size"], x_tolerance_ratio=0.3, keep_blank_chars=False,
                                use_text_flow=False, return_chars=True)
    except Exception:  # noqa: BLE001 - damaged fonts
        return out
    for w in ws:
        chars = w.get("chars") or []
        rot = _word_rot(chars, bool(w.get("upright", True)))
        text = w["text"]
        if rot and chars:                              # order the glyphs along the direction the text runs
            key = {90: lambda c: -c["top"], 270: lambda c: c["top"], 180: lambda c: -c["x0"]}.get(rot)
            if key:
                text = "".join(c["text"] for c in sorted(chars, key=key))
        text = text.strip()
        if not text:
            continue
        size = float(w.get("size") or 0) or None
        out.append(dict(text=text, x0=float(w["x0"]) - ox, y0=float(w["top"]) - oy, x1=float(w["x1"]) - ox,
                        y1=float(w["bottom"]) - oy, font=w.get("fontname"), size=size, rot=rot))
    return out


def group_lines(words: list[dict]) -> None:
    """Set w['line']: words on one baseline, in reading order, with gaps smaller than about one and a half
    character heights.  Works along each reading direction separately."""
    def uv(w):                                         # u runs along the text, v across it
        r = w["rot"]
        if r == 90:
            return -w["y1"], -w["y0"], w["x1"]
        if r == 270:
            return w["y0"], w["y1"], -w["x0"]
        if r == 180:
            return -w["x1"], -w["x0"], -w["y0"]
        return w["x0"], w["x1"], w["y1"]

    def height(w):
        return w["size"] or (abs(w["x1"] - w["x0"]) if w["rot"] in (90, 270) else abs(w["y1"] - w["y0"])) or 1.0

    line_no = 0
    for rot in sorted({w["rot"] for w in words}):
        ws = sorted((w for w in words if w["rot"] == rot), key=lambda w: uv(w)[2])
        bands, band = [], []
        for w in ws:
            if band and uv(w)[2] - uv(band[0])[2] > 0.35 * height(band[0]):
                bands.append(band)
                band = []
            band.append(w)
        if band:
            bands.append(band)
        for band in bands:
            band.sort(key=lambda w: uv(w)[0])
            prev = None
            for w in band:
                u0, u1, _ = uv(w)
                h = height(w)
                if prev is not None and not (-0.5 * h <= u0 - prev[0] <= 1.5 * h and abs(h - prev[1]) <= 0.25 * h):
                    line_no += 1
                w["line"] = line_no
                prev = (u1, h)
            line_no += 1
    order = {}
    for w in words:                                    # number lines top to bottom, left to right
        order.setdefault(w["line"], (round(w["y0"], 1), round(w["x0"], 1)))
    rank = {ln: i for i, ln in enumerate(sorted(order, key=order.get))}
    for w in words:
        w["line"] = rank[w["line"]]


def line_texts(words: list[dict]) -> list[str]:
    lines: dict[int, list[str]] = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w["text"])
    return [" ".join(v) for v in lines.values()]


def is_scan(page, n_words: int, n_paths: int) -> bool:
    """No usable text layer and images that cover most of the page."""
    if n_words >= 5:
        return False
    try:
        images = page.images
    except Exception:  # noqa: BLE001
        return False
    if not images or len(images) > 2000:
        return False
    x0, y0 = min(i["x0"] for i in images), min(i["top"] for i in images)
    x1, y1 = max(i["x1"] for i in images), max(i["bottom"] for i in images)
    return (x1 - x0) * (y1 - y0) >= 0.5 * float(page.width) * float(page.height) and n_paths < 2000


# ------------------------------------------------------------------ rendering
def render_page(doc: pdfium.PdfDocument, page_no: int, out: Path) -> tuple[int, int, float]:
    """Render one page to PNG.  -> (width px, height px, pixels per point)."""
    page = doc[page_no]
    w, h = page.get_size()
    scale = min(RENDER_MAX_DPI / 72, RENDER_LONG_SIDE / max(w, h))
    bitmap = page.render(scale=scale)
    im = bitmap.to_pil().convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "PNG", compress_level=3)
    size = im.size
    page.close()
    return size[0], size[1], scale


def render_pages(db: DB, file_id: str, path: Path) -> int:
    """(Re)render every page of a PDF that is already in the store; fills page.image, image_w, image_h, to_image."""
    from ..pipeline import store_dir
    doc = pdfium.PdfDocument(str(path))
    n = 0
    try:
        for r in db.run("select page_no from jstore.page where file_id = :f order by page_no", f=file_id):
            rel = Path("renders") / file_id / f"p{r['page_no']}.png"
            w, h, s = render_page(doc, r["page_no"], store_dir() / rel)
            db.run("""update jstore.page set image = :i, image_w = :w, image_h = :h,
                      to_image = cast(:t as double precision[]) where file_id = :f and page_no = :p""",
                   i=str(rel), w=w, h=h, t=arr([s, 0, 0, 0, s, 0]), f=file_id, p=r["page_no"])
            n += 1
    finally:
        doc.close()
    return n


# ------------------------------------------------------------------ document structure
def outline(doc: pdfium.PdfDocument) -> list[dict]:
    out = []
    try:
        for bm in doc.get_toc(max_depth=15):
            dest = bm.get_dest()
            idx = dest.get_index() if dest else None
            out.append(dict(level=bm.level, title=(bm.get_title() or "").strip(), page=idx))
            if len(out) >= 5000:
                break
    except Exception:  # noqa: BLE001 - damaged outline tree
        pass
    return out


def _resolve(v):
    from pdfminer.pdftypes import resolve1
    try:
        return resolve1(v)
    except Exception:  # noqa: BLE001
        return None


def _target_page(pdf, dest, page_ids: dict) -> int | None:
    """Page index a link destination points at (explicit array, or a named destination)."""
    from pdfminer.pdftypes import PDFObjRef
    dest = dest if isinstance(dest, PDFObjRef) else _resolve(dest)
    if isinstance(dest, (bytes, str)):
        try:
            dest = _resolve(pdf.doc.get_dest(dest))
        except Exception:  # noqa: BLE001
            return None
    if isinstance(dest, dict):
        dest = _resolve(dest.get("D"))
    if isinstance(dest, list) and dest:
        first = dest[0]
        if isinstance(first, PDFObjRef):
            return page_ids.get(first.objid)
        if isinstance(first, int):
            return first
    return None


def annotations(pdf, page, page_no: int, ox: float, oy: float, page_ids: dict) -> tuple[list[dict], list[dict]]:
    """-> (links, other annotations) of one page."""
    links, notes = [], []
    try:
        annots = page.annots
    except Exception:  # noqa: BLE001
        return links, notes
    for a in annots:
        data = a.get("data") or {}
        sub = str(_resolve(data.get("Subtype")) or "").strip("/'")
        box = [round(float(a["x0"]) - ox, 2), round(float(a["top"]) - oy, 2), round(float(a["x1"]) - ox, 2),
               round(float(a["bottom"]) - oy, 2)]
        if sub == "Link" or a.get("uri"):
            item = dict(page=page_no, box=box)
            if a.get("uri"):
                item["uri"] = str(a["uri"])[:500]
            action = _resolve(data.get("A")) or {}
            dest = data.get("Dest") if data.get("Dest") is not None else (action.get("D") if isinstance(action, dict) else None)
            if dest is not None:
                item["target_page"] = _target_page(pdf, dest, page_ids)
            links.append(item)
        else:
            item = dict(page=page_no, box=box, subtype=sub)
            for k in ("contents", "title"):
                if a.get(k):
                    item[k] = str(a[k])[:500]
            notes.append(item)
    return links, notes


def _clean(v):
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).replace("\x00", "")[:500]


def ocr_page(db: DB, file_id: str, page_no: int, image: Path, pmeta: dict, totals: dict, meta: dict) -> list[str]:
    """OCR a rendered page into raster_word (pixels of the render).  -> the texts read."""
    import cv2
    from . import raster
    lines, info = raster.ocr_image(cv2.imread(str(image)))
    totals["ocr_lines"] += raster.store_words(db, file_id, page_no, lines, info["engine"])
    pmeta.update(scanned=True, ocr_lines=len(lines), ocr_rotation=info["rotation"])
    meta["ocr_engine"] = info["engine"]
    return [d["text"] for d in lines]


# ------------------------------------------------------------------ entry point
def ingest(db: DB, file_id: str, path: Path) -> dict:
    """Parse one PDF into page / pdf_path / pdf_text (and raster_word for scanned pages).  -> meta."""
    from ..pipeline import store_dir
    from . import raster

    t0 = time.time()
    for table in ("pdf_path", "pdf_text", "raster_word", "raster_detection", "page", "frame"):
        db.run(f"delete from jstore.{table} where file_id = :f", f=file_id)
    meta: dict = dict(path_cap=MAX_PATHS)
    doc = pdfium.PdfDocument(str(path))
    pdf = pdfplumber.open(str(path))
    try:
        n_pages = len(doc)
        info = {k: _clean(v) for k, v in (pdf.metadata or {}).items() if v not in (None, "", b"")}
        meta.update(pages=n_pages, title=info.get("Title"), producer=info.get("Producer"), creator=info.get("Creator"),
                    info=info, outline=outline(doc))
        labels = []
        for i in range(n_pages):
            try:
                labels.append(doc.get_page_label(i) or None)
            except Exception:  # noqa: BLE001
                labels.append(None)
        if any(labels):
            meta["page_labels"] = labels
        plumb_pages = pdf.pages
        page_ids = {getattr(p.page_obj, "pageid", None): i for i, p in enumerate(plumb_pages)}
        links, notes, scales, scanned, skipped, capped = [], [], {}, [], [], []
        totals = dict(paths=0, words=0, ocr_lines=0)
        for no in range(n_pages):
            rel = Path("renders") / file_id / f"p{no}.png"
            img_w, img_h, s = render_page(doc, no, store_dir() / rel)
            pmeta: dict = {}
            if labels[no]:
                pmeta["label"] = labels[no]
            page = plumb_pages[no] if no < len(plumb_pages) else None
            width, height = img_w / s, img_h / s
            if page is None:                          # pdfminer cannot open the page tree: read the render instead
                texts = ocr_page(db, file_id, no, store_dir() / rel, pmeta, totals, meta)
                pmeta.update(content="page not readable as vectors; OCR of the render", scales=raster.scale_texts(texts))
                skipped.append(no)
                if pmeta["scales"]:
                    scales[str(no)] = pmeta["scales"]
            elif time.time() - t0 > TIME_BUDGET:
                skipped.append(no)
                pmeta["content"] = "skipped: time budget"
            else:
                ox, oy = float(page.bbox[0]), float(page.bbox[1])
                pmeta["rotation"] = int(page.rotation or 0)
                rows, dropped = path_rows(file_id, no, page, ox, oy)
                totals["paths"] += db.copy("jstore.pdf_path", PATH_COLUMNS, rows)
                if dropped:
                    pmeta["paths_dropped"] = dropped
                    capped.append(no)
                words = words_of(page, ox, oy)
                group_lines(words)
                totals["words"] += db.copy("jstore.pdf_text", TEXT_COLUMNS, (
                    (file_id, no, i, w["text"], w["x0"], w["y0"], w["x1"], w["y1"], w["font"], w["size"],
                     point_wkb((w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2), w["line"], w["rot"])
                    for i, w in enumerate(words)))
                texts = line_texts(words)
                pmeta.update(paths=len(rows), words=len(words))
                li, an = annotations(pdf, page, no, ox, oy, page_ids)
                links += li
                notes += an
                if is_scan(page, len(words), len(rows)):
                    texts += ocr_page(db, file_id, no, store_dir() / rel, pmeta, totals, meta)
                    scanned.append(no)
                found = raster.scale_texts(texts)
                pmeta["scales"] = found
                if found:
                    scales[str(no)] = found
                page.close()                          # drop pdfplumber's cached objects of this page
            db.run("""insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h,
                                               to_image, meta)
                      values (:f, :p, :n, :w, :h, 'pt', :img, :iw, :ih, cast(:t as double precision[]), cast(:m as jsonb))""",
                   f=file_id, p=no, n=labels[no] or f"page {no + 1}", w=width, h=height, img=str(rel), iw=img_w,
                   ih=img_h, t=arr([s, 0, 0, 0, s, 0]), m=js(pmeta))
        meta.update(totals, links=links[:MAX_ANNOTS], annotations=notes[:MAX_ANNOTS], scales=scales,
                    scanned_pages=scanned, seconds=round(time.time() - t0, 1))
        if skipped:
            meta["pages_without_content"] = skipped
        if capped:
            meta["pages_capped"] = capped
        per_page = [trusted_scale(v) for v in scales.values()]
        if per_page and all(per_page) and len({s["ratio"] for s in per_page}) == 1:
            meta["scale"] = per_page[0]["text"]             # every page that states a scale states this one
    finally:
        pdf.close()
        doc.close()
    return meta
