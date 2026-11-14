"""Tests for the MIT/BSD PDF vector & raster reader (pdfplumber + pypdfium2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from areas import cli, common
from areas.frozen import dxf_generic_final, pdf_vector_final

SWISS_DIR = Path(__file__).resolve().parents[2] / "data" / "junction-dataset" / "data" / "swiss_dwellings"
SWISS_STEMS = (
    "site1000_bldg2082_floor9385",
    "site1001_bldg2083_floor9389",
    "site1002_bldg2084_floor9393",
)


def test_no_agpl_pdf_dependency():
    src = Path(pdf_vector_final.__file__).read_text(encoding="utf-8").lower()
    assert ("pymu" + "pdf") not in src and ("fi" + "tz") not in src


def test_compound_subpath_even_odd_hole_and_color_norm():
    # Outer 100x100 square (area 10,000) with inner 40x40 hole (area 1,600) -> net area 8,400
    compound_obj = {
        "fill": True,
        "stroke": True,
        "non_stroking_color": 0.85,
        "stroking_color": (128, 64, 0),
        "linewidth": 0.5,
        "dash": None,
        "pts": [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)],
        "path": [
            ("m", (0.0, 0.0)),
            ("l", (100.0, 0.0)),
            ("l", (100.0, 100.0)),
            ("l", (0.0, 100.0)),
            ("h",),
            ("m", (30.0, 30.0)),
            ("l", (70.0, 30.0)),
            ("l", (70.0, 70.0)),
            ("l", (30.0, 70.0)),
            ("h",),
        ],
    }
    d = pdf_vector_final._to_drawing(compound_obj)
    rs = pdf_vector_final.rings(d)
    assert len(rs) == 2
    poly = pdf_vector_final.to_poly(rs)
    assert abs(poly.area - 8400.0) < 1e-6
    assert d["fill"] == (0.85, 0.85, 0.85)
    assert all(0.0 <= c <= 1.0 for c in d["color"])
    assert round(d["color"][0], 2) == 0.50


def test_render_page_pypdfium2(tmp_path: Path):
    import matplotlib.pyplot as plt

    pdf_path = tmp_path / "sheet.pdf"
    fig = plt.figure(figsize=(4, 3))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.plot([0, 1], [0, 1], color="black")
    ax.axis("off")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)

    im = pdf_vector_final.render_page(pdf_path, page_idx=0, dpi=72.0)
    assert im.mode == "RGB"
    assert im.size == (288, 216)


def _build_minimal_cad_pdf(rotate: int = 0, mediabox: tuple[int, int, int, int] = (0, 0, 600, 400),
                           cropbox: tuple[int, int, int, int] | None = None, ctm: str = "1 0 0 1 0 0 cm") -> bytes:
    cb = f"/CropBox [{cropbox[0]} {cropbox[1]} {cropbox[2]} {cropbox[3]}]" if cropbox else ""
    content = (
        f"{ctm}\n".encode()
        + b"q 0.9 0.2 0.2 rg 0 0 0 RG 1 w 100 200 200 100 re B Q\n"
        + b"q [6 3] 0 d 0 0 1 RG 1 w 90 180 220 130 re S Q\n"
        + b"0 0 0 rg BT /F1 12 Tf 110 295 Td (Whg.) Tj 36 0 Td (A) Tj ET\n"
        + b"0 0 0 rg BT /F1 12 Tf 135 262 Td (Salle) Tj 29 0 Td (de) Tj 17 0 Td (bains) Tj "
        + b"-46 -14 Td (1.01) Tj 0 -14 Td (20.5) Tj 28 0 Td (m2) Tj 240 0 Td (1:100) Tj ET\n"
    )
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (f"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [{mediabox[0]} {mediabox[1]} {mediabox[2]} {mediabox[3]}] "
         f"{cb} /Rotate {rotate} /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n").encode(),
        f"4 0 obj << /Length {len(content)} >> stream\n".encode() + content + b"endstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for o in objs:
        offsets.append(len(out))
        out += o
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


@pytest.mark.parametrize(
    "name,rotate,mediabox,cropbox,ctm",
    [
        ("rot0", 0, (0, 0, 600, 400), None, "1 0 0 1 0 0 cm"),
        ("rot90", 90, (0, 0, 400, 600), None, "0 1 -1 0 400 0 cm"),
        ("rot180", 180, (0, 0, 600, 400), None, "-1 0 0 -1 600 400 cm"),
        ("rot270", 270, (0, 0, 400, 600), None, "0 -1 1 0 0 600 cm"),
        ("nonzero_mediabox", 0, (50, 80, 650, 480), None, "1 0 0 1 50 80 cm"),
        ("cropbox_inset", 0, (0, 0, 800, 600), (100, 100, 700, 500), "1 0 0 1 100 100 cm"),
    ],
)
def test_rotated_pages_mediabox_cropbox_and_kerned_text(tmp_path: Path, name, rotate, mediabox, cropbox, ctm):
    import numpy as np

    pdf_path = tmp_path / f"{name}.pdf"
    pdf_path.write_bytes(_build_minimal_cad_pdf(rotate=rotate, mediabox=mediabox, cropbox=cropbox, ctm=ctm))

    im = pdf_vector_final.render_page(pdf_path, page_idx=0, dpi=72.0)
    assert im.size == (600, 400)
    arr = np.asarray(im)
    red = (arr[:, :, 0] > 180) & (arr[:, :, 1] < 100)
    ys, xs = np.nonzero(red)
    assert len(xs) > 0
    px_bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]

    info = pdf_vector_final.read(str(pdf_path))
    assert len(info["rooms"]) == 1
    r = info["rooms"][0]
    assert r["number"] == "1.01"
    assert r["label"] == "Salle de bains"
    assert r["unit"] == "A"
    assert r["printed"] is True
    assert r["area"] == 20.5
    assert r["bbox"] == [100.0, 100.0, 300.0, 200.0]
    # Vector bbox in points aligns within 2 px of pypdfium2's 72-DPI rendered raster image
    assert all(abs(a - b) <= 2 for a, b in zip(r["bbox"], px_bbox))


@pytest.mark.skipif(not SWISS_DIR.is_dir(), reason="Swiss Dwellings dataset not mounted")
@pytest.mark.parametrize("stem", SWISS_STEMS)
def test_swiss_pdf_matches_dxf_within_2_percent(stem: str):
    pdf_path = SWISS_DIR / f"{stem}.pdf"
    dxf_path = SWISS_DIR / f"{stem}.dxf"
    assert pdf_path.is_file() and dxf_path.is_file()

    pdf_info = pdf_vector_final.read(str(pdf_path))
    _, pdf_cli_rooms = cli.rooms_of(str(pdf_path))
    _, dxf_rooms = cli.rooms_of(str(dxf_path))

    pdf_total = sum(r["area"] for r in pdf_cli_rooms if r.get("number") and common.indoor(r))
    dxf_total = sum(r["area"] for r in dxf_rooms if r.get("number") and common.indoor(r))
    assert dxf_total > 0
    rel_err = abs(pdf_total - dxf_total) / dxf_total
    assert rel_err < 0.02, f"{stem}: pdf_total={pdf_total:.2f} vs dxf_total={dxf_total:.2f} ({rel_err:.2%})"

    # Also verify pure vector polygon measured geometry matches DXF room areas within 2%
    dxf_by_num = {r["number"]: r["area"] for r in dxf_rooms if r.get("number")}
    pdf_measured = [r for r in pdf_info["rooms"] if r.get("number") in dxf_by_num and r.get("measured")]
    assert len(pdf_measured) >= max(1, len(dxf_by_num) - 2)
    meas_sum = sum(r["measured"] for r in pdf_measured)
    ref_sum = sum(dxf_by_num[r["number"]] for r in pdf_measured)
    assert abs(meas_sum - ref_sum) / ref_sum < 0.02

    # Verify pypdfium2 raster rendering works on the real Swiss PDF too
    im = pdf_vector_final.render_page(pdf_path, page_idx=0, dpi=72.0)
    assert im.mode == "RGB" and im.width > 500 and im.height > 500
