"""Printed dimensions: the parser, dimension chains on a drawn plan, and two phone photos of US plans.

The photos are not in the repository: /root/test-data/b1.jpg and b2.jpeg (skipped when missing).
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from jstore import dimensions as D

FT, IN = 0.3048, 0.0254


def ftin(f: int, i: float = 0) -> float:
    return f * FT + i * IN


@pytest.mark.parametrize("text, kw, want", [
    ("35'-1\"", {}, ftin(35, 1)),
    ("35' 1\"", {}, ftin(35, 1)),
    ("35’-1”", {}, ftin(35, 1)),          # curly marks
    ("35'-1", {}, ftin(35, 1)),                      # no closing inch mark
    ("5'10\"", {}, ftin(5, 10)),
    ("98'-0\"", {}, ftin(98)),
    ("12'6\"", {}, ftin(12, 6)),
    ("7' ", {}, ftin(7)),
    ("3 1/2\"", {}, 3.5 * IN),
    ("10'-8 1/2\"", {}, ftin(10, 8.5)),
    ("10'-8½\"", {}, ftin(10, 8.5)),
    ("5*10\"", {}, ftin(5, 10)),                     # RapidOCR reads the foot mark as a star
    ("2|1'-1\"", {}, ftin(21, 1)),                   # an extension line read as a bar inside the number
    ("-19'-10\"", {}, ftin(19, 10)),                 # the dimension line read as a leading dash
    ("35''-1\"", {}, ftin(35, 1)),
    ("20-11", {"imperial": True}, ftin(20, 11)),     # dash only: imperial plans only
    ("20-11", {}, None),
    ("3.40", {}, 3.40),
    ("3,40", {}, 3.40),
    ("3.40 m", {}, 3.40),
    ("340 cm", {}, 3.40),
    ("2450 mm", {}, 2.45),
    ("340", {}, None),                               # a bare integer: room number? level? unit unknown
    ("340", {"metric_hint": "cm"}, 3.40),
    ("2450", {"metric_hint": "mm"}, 2.45),
    ("12.5 m²", {}, None),                           # areas are not dimensions
    ("150 sq ft", {}, None),
    ("150 SF", {}, None),
    ("BEDROOM", {}, None),
    ("3.40 4.20", {}, None),                         # two dimensions are not one
])
def test_parse_dimension(text, kw, want):
    got = D.parse_dimension(text, **kw)
    if want is None:
        assert got is None
    else:
        assert got == pytest.approx(want, abs=1e-6)


@pytest.mark.parametrize("text, want", [
    ("12.5 m²", 12.5), ("12,5 m2", 12.5), ("150 sq ft", 150 * FT * FT), ("150 SF", 150 * FT * FT),
    ("150 ft²", 150 * FT * FT), ("3.40", None), ("35'-1\"", None),
])
def test_parse_area(text, want):
    got = D.parse_area(text)
    assert (got is None) if want is None else got == pytest.approx(want, rel=1e-6)


def test_two_dimensions_in_one_ocr_line_are_both_found():
    got = D.find_dimensions("10\"| 7'-0\"-")
    assert [round(d["metres"], 4) for d in got] == [round(10 * IN, 4), round(ftin(7), 4)]


# ------------------------------------------------------------------------------------------- drawn plan
def _font(size: int):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _plan(mpp: float = 0.01):
    """A plan drawn at `mpp` metres per pixel: a top chain of three dimensions with crossing ticks and the text in
    the gaps of the line, the overall below, a vertical one at the left.  -> (gray image, words)."""
    W, H = int(450 + 10.35 / mpp), int(500 + 6.20 / mpp)
    im = Image.new("L", (W, H), 245)
    dr = ImageDraw.Draw(im)
    font = _font(30)
    x0, y0 = 200, 250
    segs = [("3.40", 3.40), ("2.85", 2.85), ("4,10", 4.10)]
    depth = 6.20
    xs = [x0]
    for _, m in segs:
        xs.append(xs[-1] + m / mpp)
    x1, y1 = xs[-1], y0 + depth / mpp
    dr.rectangle([x0, y0, x1, y1], outline=20, width=8)            # the walls
    words = []

    def dim_h(a, b, y, text):
        tw = dr.textbbox((0, 0), text, font=font)
        w, h = tw[2] - tw[0], tw[3] - tw[1]
        cx = (a + b) / 2
        dr.line([a, y, cx - w / 2 - 8, y], fill=30, width=2)
        dr.line([cx + w / 2 + 8, y, b, y], fill=30, width=2)
        for t in (a, b):
            dr.line([t, y - 22, t, y + 22], fill=30, width=2)
        dr.text((cx - w / 2 - tw[0], y - h / 2 - tw[1]), text, font=font, fill=30)
        words.append((text, (cx - w / 2 - 3, y - h / 2 - 5, cx + w / 2 + 3, y + h / 2 + 5)))

    for (text, _), a, b in zip(segs, xs[:-1], xs[1:]):
        dim_h(a, b, y0 - 80, text)
    dim_h(x0, x1, y1 + 90, "10.35")
    # the vertical one: text turned to read bottom to top
    text = "6.20"
    tw = dr.textbbox((0, 0), text, font=font)
    w, h = tw[2] - tw[0], tw[3] - tw[1]
    t = Image.new("L", (w + 10, h + 10), 245)
    ImageDraw.Draw(t).text((5 - tw[0], 5 - tw[1]), text, font=font, fill=30)
    t = t.rotate(90, expand=True)
    xv, cy = x0 - 90, (y0 + y1) / 2
    im.paste(t, (int(xv - t.width / 2), int(cy - t.height / 2)))
    dr.line([xv, y0, xv, cy - t.height / 2 - 8], fill=30, width=2)
    dr.line([xv, cy + t.height / 2 + 8, xv, y1], fill=30, width=2)
    for yy in (y0, y1):
        dr.line([xv - 22, yy, xv + 22, yy], fill=30, width=2)
    words.append((text, (xv - t.width / 2, cy - t.height / 2, xv + t.width / 2, cy + t.height / 2)))
    words.append(("KITCHEN", (500, 500, 650, 535)))
    dr.text((500, 500), "KITCHEN", font=font, fill=30)
    return np.asarray(im), words


@pytest.mark.parametrize("mpp", [0.01, 0.0073])
def test_dimension_chains_on_a_drawn_plan_give_the_scale(mpp):
    gray, words = _plan(mpp)
    dims = D.read_dimensions(gray, words, reread=False)
    got = Counter(round(d.metres, 2) for d in dims)
    assert got == Counter({3.40: 1, 2.85: 1, 4.10: 1, 10.35: 1, 6.20: 1}), [(d.text, d.px_length) for d in dims]
    assert {d.orientation for d in dims} == {"h", "v"}
    for d in dims:
        assert d.px_length == pytest.approx(d.metres / mpp, rel=0.02), d
    m, conf, used = D.scale_from_dimensions(dims)
    assert m == pytest.approx(mpp, rel=0.02)
    assert len(used) == 5 and conf >= 0.8                      # the chain adds up to the overall: confirmed
    assert D._chain_checks(used)["confirmed"] == 1


def test_a_wrong_dimension_is_kept_out_of_the_scale():
    gray, words = _plan(0.01)
    words = [("9.99", b) if t == "2.85" else (t, b) for t, b in words]
    dims = D.read_dimensions(gray, words, reread=False)
    m, conf, used = D.scale_from_dimensions(dims)
    assert m == pytest.approx(0.01, rel=0.02)
    assert [d.text for d in dims if not d.used] == ["9.99"]


# ------------------------------------------------------------------------------------------- the photos
PHOTOS = Path("/root/test-data")
TRUTH = {
    "b1.jpg": dict(chains=["35'-1\"", "5'-10\"", "7'-0\"", "14'-0\"", "26'-11\"", "9'-2\"", "98'-0\"", "25'-5\""],
                   notes=[]),
    "b2.jpeg": dict(chains=["35'-1\"", "5'-10\"", "7'-0\"", "19'-10\"", "21'-1\"", "9'-2\"",
                            "35'-1\"", "5'-10\"", "10'-8\"", "12'-10\"", "12'-11\"", "17'-3\"", "3'-5\"",
                            "98'-0\"", "25'-5\""],
                    notes=["20'-11\"", "7'-11\"", "3'-6\"", "4'-3\"", "2'-7\""]),
}


def _hits(dims, truth: list[str]) -> int:
    have = Counter(round(d.metres, 2) for d in dims)
    n = 0
    for t in truth:
        v = round(D.parse_dimension(t), 2)
        if have[v] > 0:
            have[v] -= 1
            n += 1
    return n


def _gemini_key() -> str | None:
    env = Path.home() / ".config" / "junction" / "env"
    if os.environ.get("JSTORE_TEST_GEMINI") != "1" or not env.exists():
        return None
    for line in env.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_dimensions_read_on_phone_photos(name, monkeypatch):
    """OCR path (always) and, with JSTORE_TEST_GEMINI=1 and a key in ~/.config/junction/env, the Gemini pass
    snapped onto the drawn end marks.  The overall 98'-0\" must measure within 3 % under the scale found."""
    path = PHOTOS / name
    if not path.exists():
        pytest.skip(f"{path} not here")
    from jstore.ingest.raster import load, ocr_image
    bgr, _, _ = load(path)
    lines, _ = ocr_image(bgr)
    gray = bgr[:, :, 1].copy()
    dims = D.read_dimensions(gray, lines)
    truth = TRUTH[name]
    assert _hits(dims, truth["chains"]) >= 0.8 * len(truth["chains"]), [d.text for d in dims]
    key = _gemini_key()
    if key:
        monkeypatch.setenv("GEMINI_API_KEY", key)
        g = D.gemini_dimensions(bgr)
        assert "error" not in g, g
        D.add_gemini(dims, g["dims"], gray.shape)
        everything = truth["chains"] + truth["notes"]
        assert _hits(dims, everything) >= 0.8 * len(everything), [d.text for d in dims]
    m, conf, used = D.scale_from_dimensions(dims)
    overall = next(d for d in dims if d.text.replace(" ", "") in ("98'-0\"", "98'-0") and d.orientation == "h")
    assert overall.px_length * m == pytest.approx(ftin(98), rel=0.03)
    assert conf >= 0.6
