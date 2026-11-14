"""Page images: render CAD pages, re-render any file's pages, cut crops and tiles for the service.

`render_file(db, file_id, path)` dispatches on the file's format and fills page.image / image_w / image_h /
to_image for every page of the file.  PDF, SVG and raster readers render while they ingest; CAD pages are
rendered here with the ezdxf drawing add-on on matplotlib (model space and every layout that has a page row).

to_image maps file coordinates to pixels of the image: for CAD, drawing units with y flipped.
"""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from .db import DB, arr, js

LONG_SIDE = 3000          # px
MIN_SHORT_SIDE = 300      # px, so a single long wall still gives a usable picture
MAX_ENTITIES = 300_000    # above this a matplotlib render takes minutes and gigabytes
Image.MAX_IMAGE_PIXELS = None


def _note(db: DB, file_id: str, page_no: int | None, note: str) -> None:
    db.run("""update jstore.page set meta = meta || cast(:m as jsonb)
              where file_id = :f and (cast(:p as int) is null or page_no = :p)""",
           m=js({"render": note}), f=file_id, p=page_no)


def _ensure_fonts() -> None:
    """ezdxf needs at least one TrueType font to draw text; matplotlib ships DejaVu, so offer that folder."""
    import matplotlib
    from ezdxf.fonts import fonts
    if not fonts.font_manager.has_font("DejaVuSans.ttf"):
        fonts.font_manager.build([str(Path(matplotlib.get_data_path()) / "fonts" / "ttf")])


def render_cad(db: DB, file_id: str, path: Path) -> dict:
    """Render each CAD page of a file (model space and non-empty layouts) to PNG.  -> {pages, rendered, notes}."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    from .ingest import dxf as dxf_reader
    from .pipeline import store_dir

    pages = db.run("select page_no, name from jstore.page where file_id = :f order by page_no", f=file_id)
    out = dict(pages=len(pages), rendered=0, notes=[])
    if not pages:
        return out
    n = db.one("select count(*) n from jstore.cad_entity where file_id = :f", f=file_id)["n"]
    if n > MAX_ENTITIES:
        note = f"not rendered: {n} entities (limit {MAX_ENTITIES})"
        _note(db, file_id, None, note)
        out["notes"].append(note)
        return out
    tmp = None
    try:
        if path.suffix.lower() == ".dwg":
            tmp = Path(tempfile.mkdtemp(prefix="dwg_"))
            path = dxf_reader.dwg_to_dxf(path, tmp)
        doc, _ = dxf_reader.read_document(path)
        _ensure_fonts()
        ctx = RenderContext(doc)
        cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
        for p in pages:
            layout = doc.modelspace() if p["name"] == "model" else doc.layouts.get(p["name"])
            if layout is None:
                _note(db, file_id, p["page_no"], "not rendered: layout not found")
                continue
            fig = plt.figure()
            try:
                ax = fig.add_axes([0, 0, 1, 1])
                Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(layout, finalize=True)
                (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
                w, h = x1 - x0, y1 - y0
                if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
                    _note(db, file_id, p["page_no"], "not rendered: nothing to draw")
                    continue
                k = LONG_SIDE / max(w, h)
                if min(w, h) * k < MIN_SHORT_SIDE:                # pad the short direction
                    pad = (MIN_SHORT_SIDE / k - min(w, h)) / 2
                    if w < h:
                        x0, x1 = x0 - pad, x1 + pad
                    else:
                        y0, y1 = y0 - pad, y1 + pad
                    w, h = x1 - x0, y1 - y0
                W, H = round(w * k), round(h * k)
                ax.set_aspect("auto")
                ax.set_xlim(x0, x1)
                ax.set_ylim(y0, y1)
                ax.set_axis_off()
                fig.set_size_inches(W / 100, H / 100)
                rel = Path("renders") / file_id / f"p{p['page_no']}.png"
                (store_dir() / rel).parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(store_dir() / rel, dpi=100, facecolor="white")
            finally:
                plt.close(fig)
            with Image.open(store_dir() / rel) as im:
                W, H = im.size
            sx, sy = W / w, H / h
            db.run("""update jstore.page set image = :i, image_w = :w, image_h = :h,
                      to_image = cast(:t as double precision[]), meta = meta || cast(:m as jsonb)
                      where file_id = :f and page_no = :p""",
                   i=str(rel), w=W, h=H, t=arr([sx, 0, -x0 * sx, 0, -sy, y1 * sy]), f=file_id, p=p["page_no"],
                   m=js({"render": "ezdxf drawing add-on, matplotlib", "render_extent": [x0, y0, x1, y1]}))
            out["rendered"] += 1
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def render_file(db: DB, file_id: str, path: Path) -> dict:
    """(Re)render every page of one stored file, whatever its format.  -> {pages, rendered}."""
    row = db.one("select format from jstore.source_file where file_id = :f", f=file_id)
    fmt = row["format"] if row else None
    if fmt in ("dxf", "dwg"):
        return render_cad(db, file_id, Path(path))
    if fmt == "pdf":
        from .ingest import pdf
        n = pdf.render_pages(db, file_id, Path(path))
        return dict(pages=n, rendered=n)
    if fmt in ("png", "jpg"):
        from .ingest import raster
        _, im, changed = raster.load(Path(path))
        image = raster.page_image(file_id, Path(path), im, changed)
        db.run("""update jstore.page set image = :i, image_w = :w, image_h = :h,
                  to_image = cast(:t as double precision[]) where file_id = :f and page_no = 0""",
               i=image, w=im.size[0], h=im.size[1], t=arr([1, 0, 0, 0, 1, 0]), f=file_id)
        return dict(pages=1, rendered=1)
    if fmt == "svg":
        from .ingest import svg
        from .pipeline import store_dir
        doc = svg.read(Path(path))
        rel = Path("renders") / file_id / "p0.png"
        w, h, s = svg.rasterise(svg._rows(file_id, doc)[2], doc["width"], doc["height"], store_dir() / rel)
        db.run("""update jstore.page set image = :i, image_w = :w, image_h = :h,
                  to_image = cast(:t as double precision[]) where file_id = :f and page_no = 0""",
               i=str(rel), w=w, h=h, t=arr([s, 0, 0, 0, s, 0]), f=file_id)
        return dict(pages=1, rendered=1)
    return dict(pages=0, rendered=0)


# ------------------------------------------------------------------ crops and tiles
def crop(image_path, box_px, out_path, max_side: int = 1600) -> dict:
    """Cut box_px = (x0, y0, x1, y1) out of an image, shrink it to max_side, save it.
    -> {path, box (clamped), scale (output pixels per source pixel), size}."""
    with Image.open(image_path) as im:
        W, H = im.size
        x0, y0, x1, y1 = box_px
        x0, y0 = max(0, int(math.floor(min(x0, x1)))), max(0, int(math.floor(min(y0, y1))))
        x1, y1 = min(W, int(math.ceil(max(box_px[0], box_px[2])))), min(H, int(math.ceil(max(box_px[1], box_px[3]))))
        if x1 <= x0 or y1 <= y0:
            raise ValueError("crop box is outside the image")
        part = im.crop((x0, y0, x1, y1))
        if part.mode not in ("RGB", "L"):
            part = part.convert("RGB")
        scale = min(1.0, max_side / max(part.size))
        if scale < 1.0:
            part = part.resize((max(1, round(part.size[0] * scale)), max(1, round(part.size[1] * scale))), Image.LANCZOS)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        part.save(out_path)
        return dict(path=str(out_path), box=[x0, y0, x1, y1], scale=scale, size=list(part.size))


def tile_boxes(width: int, height: int, tile: int = 1024, overlap: int = 128) -> list[tuple[int, int, int, int]]:
    """Boxes of overlapping tiles that cover an image evenly, row by row."""
    def starts(size):
        if size <= tile:
            return [0]
        n = math.ceil((size - overlap) / (tile - overlap))
        return [round(i * (size - tile) / (n - 1)) for i in range(n)]
    return [(x, y, min(width, x + tile), min(height, y + tile)) for y in starts(height) for x in starts(width)]


def tiles(image_path, out_dir, tile: int = 1024, overlap: int = 128, max_side: int | None = None) -> list[dict]:
    """Write the tiles of an image as <out_dir>/t<row>_<col>.png.  -> [{path, box, scale, size}]."""
    with Image.open(image_path) as im:
        W, H = im.size
    out, rows = [], {}
    for box in tile_boxes(W, H, tile, overlap):
        r = rows.setdefault(box[1], len(rows))
        c = sum(1 for o in out if o["box"][1] == box[1])
        out.append(crop(image_path, box, Path(out_dir) / f"t{r}_{c}.png", max_side or tile))
    return out
