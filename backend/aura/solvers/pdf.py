"""Permissive PDF floor-plan renderer & multi-page project bundler (pypdfium2, BSD/Apache).

Renders page 1 into `out` (`original.png`, `thumb.png`, `drawing.json`, single-page `source.pdf`)
and, when the PDF contains 2..8 pages, extracts sibling floor folders (`<id>-p2`, ...) plus an
auto-matched `ProjectBundle` so multi-storey house PDFs immediately unlock fast floor switching
and cross-floor queries.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aura.contract import CONTRACT_VERSION

MAX_PDF_PAGES = 8
MAX_PIXELS = 40_000_000
TARGET_LONG_SIDE_PX = 2000

_FLOOR_PATTERNS: list[tuple[re.Pattern[str], Any, Any]] = [
    (
        re.compile(r"\b(\d+)\s*\.?\s*(?:UG|Untergeschoss|Sous-sol)\b|\bB(\d+)\b", re.IGNORECASE),
        lambda m: f"-{m.group(1) or m.group(2)} UG",
        lambda m: -int(m.group(1) or m.group(2)),
    ),
    (
        re.compile(
            r"\b(?:KG|UG|Kellergeschoss|Untergeschoss|Sous-sol|Kellarikerros|Kellari|Basement|Lower\s+Ground)\b",
            re.IGNORECASE,
        ),
        lambda m: m.group(0).strip(),
        lambda _m: -1,
    ),
    (
        re.compile(
            r"\b(?:EG|Erdgeschoss|Rez[- ]de[- ]chaussée|RDC|Pohjakerros|Ground\s+Floor|GF|Level\s*0)\b",
            re.IGNORECASE,
        ),
        lambda m: m.group(0).strip(),
        lambda _m: 0,
    ),
    (
        re.compile(
            r"\b(\d+)\s*\.?\s*(?:OG|Obergeschoss|étage|etage|kerros)\b|"
            r"\b(?:Level|Floor|Storey)\s*(\d+)\b|"
            r"\b(\d+)(?:st|nd|rd|th)\s+Floor\b|"
            r"\b(\d+)F\b",
            re.IGNORECASE,
        ),
        lambda m: m.group(0).strip(),
        lambda m: int(next(g for g in m.groups() if g is not None)),
    ),
    (
        re.compile(r"\b(?:DG|Dachgeschoss|Attic|Combles|Ullakko|Roof\s+Plan|Penthouse)\b", re.IGNORECASE),
        lambda m: m.group(0).strip(),
        lambda _m: 99,
    ),
]


def infer_floor_label_and_order(text: str, page_index: int) -> tuple[str, int | float]:
    """Infer architectural floor label & vertical order from page text or title."""
    snippet = " ".join((text or "")[:2000].split())
    for rx, label_fn, order_fn in _FLOOR_PATTERNS:
        m = rx.search(snippet)
        if m:
            label = str(label_fn(m))[:40].strip()
            return (label or f"Page {page_index + 1}"), order_fn(m)
    return f"Page {page_index + 1}", page_index + 1


def _render_single_page(page, out_dir: Path) -> tuple[int, int, str]:
    from PIL import Image

    pw, ph = page.get_size()
    scale = min(TARGET_LONG_SIDE_PX / max(pw, ph, 1.0), 3.0)
    scale = max(1.0, scale)
    if (pw * scale) * (ph * scale) > MAX_PIXELS:
        scale = (MAX_PIXELS / max(pw * ph, 1.0)) ** 0.5 * 0.95

    bitmap = page.render(scale=scale)
    pil_im = bitmap.to_pil().convert("RGB")
    w, h = pil_im.size
    if w * h > MAX_PIXELS:
        raise ValueError("rendered PDF page larger than 40 MP")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    pil_im.save(out_dir / "original.png", optimize=True)
    thumb = pil_im.copy()
    thumb.thumbnail((480, 1920))
    thumb.save(out_dir / "thumb.png", optimize=True)

    text = ""
    try:
        tp = page.get_textpage()
        text = tp.get_text_range() or ""
    except Exception:  # noqa: BLE001 - text is optional; pdfium raises assorted errors on odd pages
        text = ""
    return w, h, text


def _save_single_page_pdf(doc, page_index: int, dest: Path) -> None:
    import pypdfium2 as pdfium

    single = pdfium.PdfDocument.new()
    single.import_pages(doc, [page_index])
    single.save(str(dest))
    single.close()


def _build_pdf_drawing_dict(
    drawing_id: str,
    title: str,
    w: int,
    h: int,
    meta: dict[str, Any],
    *,
    project_id: str | None = None,
    floor_label: str | None = None,
    floor_order: float | None = None,
) -> dict[str, Any]:
    base = f"/drawings/{drawing_id}"
    d: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "id": drawing_id,
        "title": title,
        "kind": "raster",
        "source_ext": ".pdf",
        "status": "ready",
        "source": "upload",
        "capabilities": [],
        "thumbnail": f"{base}/thumb.png",
        "frame": {
            "dxf_min": [0, 0],
            "dxf_max": [w, h],
            "margin_m": 0,
            "m_per_unit": 1,
            "y_flip": True,
            "px": [w, h],
        },
        "m_per_pct_x": 0,
        "m_per_pct_y": 0,
        "images": {
            "rebuilt": f"{base}/original.png",
            "original": f"{base}/original.png",
            "original_alignment": "exact",
        },
        "rooms": [],
        "units": [],
        "credit": {
            "text": "Uploaded PDF floor plan; deleted after 24 hours or when you delete it.",
            "license": "user upload",
            "license_name": "Your upload",
        },
        **meta,
    }
    if project_id is not None:
        d["project_id"] = project_id
    if floor_label is not None:
        d["floor_label"] = floor_label
    if floor_order is not None:
        d["floor_order"] = floor_order
    return d


def render_pdf(src: Path, out: Path, drawing_id: str, meta: dict[str, Any],
               *, max_pages: int = MAX_PDF_PAGES) -> dict[str, Any]:
    """Render 1..max_pages of `src`. Multi-page PDFs auto-bundle into a ProjectBundle."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(src))
    try:
        total = len(doc)
        if total < 1:
            raise ValueError("PDF has no pages")
        n_pages = min(total, max_pages)
        raw_title = str(meta.get("title") or "Uploaded PDF").strip()
        stem_title = re.sub(r"\.pdf$", "", raw_title, flags=re.IGNORECASE).strip() or "Uploaded PDF"

        if n_pages == 1:
            w0, h0, text0 = _render_single_page(doc[0], out)
            lbl0, ord0 = infer_floor_label_and_order(text0 or stem_title, 0)
            base_meta = {**meta, "source_ext": ".pdf"}
            d0 = _build_pdf_drawing_dict(
                drawing_id, raw_title, w0, h0, base_meta,
                floor_label=lbl0 if lbl0 != "Page 1" else None,
                floor_order=ord0 if lbl0 != "Page 1" else None,
            )
            (out / "drawing.json").write_text(json.dumps(d0, ensure_ascii=False), encoding="utf-8")
            return {"px": [w0, h0], "pages": 1}

        # Multi-page PDF (2..8 pages): bundle into proj-<tok> and split per-floor single-page PDFs
        project_id = f"proj-{drawing_id.removeprefix('up-')}"
        floors: list[dict[str, Any]] = []
        siblings: list[dict[str, Any]] = []
        used_labels: set[str] = set()
        page_pdfs: list[tuple[int, Path]] = []

        w0 = h0 = 0
        for idx in range(n_pages):
            did = drawing_id if idx == 0 else f"{drawing_id}-p{idx + 1}"
            target_dir = out if idx == 0 else (out.parent / did)
            target_dir.mkdir(parents=True, exist_ok=True)

            w, h, page_text = _render_single_page(doc[idx], target_dir)
            if idx == 0:
                w0, h0 = w, h
            lbl, order = infer_floor_label_and_order(page_text, idx)
            if lbl in used_labels:
                lbl = f"{lbl} (p.{idx + 1})"
                order = float(order) + idx * 0.01
            used_labels.add(lbl)

            floor_title = f"{stem_title} · {lbl}"
            floor_meta = {
                **meta,
                "title": floor_title,
                "source_ext": ".pdf",
                "project_id": project_id,
                "floor_label": lbl,
                "floor_order": order,
            }
            d_dict = _build_pdf_drawing_dict(
                did, floor_title, w, h, floor_meta,
                project_id=project_id, floor_label=lbl, floor_order=order,
            )
            (target_dir / "drawing.json").write_text(
                json.dumps(d_dict, ensure_ascii=False), encoding="utf-8"
            )
            floors.append({"drawing_id": did, "label": lbl, "order": order})
            if idx > 0:
                page_pdfs.append((idx, target_dir / "source.pdf"))
                siblings.append({
                    "id": did,
                    "title": floor_title,
                    "kind": "raster",
                    "meta": floor_meta,
                })

        # Write single-page source.pdf into siblings, then rewrite page 0's source.pdf to page 0 only
        # so agent-service opening `source.pdf` on any floor inspects that exact floor's vectors!
        for idx, sib_pdf in page_pdfs:
            _save_single_page_pdf(doc, idx, sib_pdf)
        tmp_p0 = out / "source_p1.pdf"
        _save_single_page_pdf(doc, 0, tmp_p0)
        doc.close()
        doc = None
        tmp_p0.replace(src)

        floors.sort(key=lambda f: (float(f["order"]), f["drawing_id"]))
        project: dict[str, Any] = {
            "id": project_id,
            "title": stem_title,
            "floors": floors,
            "auto_matched": True,
        }
        if meta.get("created_at"):
            project["created_at"] = meta["created_at"]
        return {
            "px": [w0, h0],
            "pages": n_pages,
            "project": project,
            "siblings": siblings,
        }
    finally:
        if doc is not None:
            doc.close()
