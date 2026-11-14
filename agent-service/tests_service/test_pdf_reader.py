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
