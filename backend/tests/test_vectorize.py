"""Classical raster -> vector plan (aura.vectorize): synthetic plan with an L-shaped room, SVG contract."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from aura.vectorize import plan_svg, vectorize_image

W, H, T = 640, 480, 8
# L room interior (pixel faces): area 300*150 + 150*150
L = [(60, 60), (360, 60), (360, 210), (210, 210), (210, 360), (60, 360)]
L_AREA = 300 * 150 + 150 * 150


def _shoelace(pts) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


@pytest.fixture(scope='module')
def plan(tmp_path_factory) -> Path:
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)

    def wall(x0, y0, x1, y1):
        d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(20, 20, 20))

    wall(52, 52, 368, 60)      # top of L
    wall(52, 60, 60, 368)      # left
    wall(60, 360, 218, 368)    # bottom of L
    wall(210, 210, 218, 360)   # inner vertical
    wall(210, 210, 368, 218)   # inner horizontal
    wall(360, 52, 368, 100)    # L / room B wall with a 40 px door gap
    wall(360, 140, 368, 210)
    wall(368, 52, 568, 60)
    wall(560, 60, 568, 368)
    wall(368, 210, 420, 218)   # B / C wall with a 40 px door gap
    wall(460, 210, 560, 218)
    wall(218, 360, 568, 368)
    d.rectangle([52, 260, 59, 299], fill='white')  # entrance gap in the L's left wall
    d.line([52, 30, 568, 30], fill=(0, 0, 0), width=1)        # dimension line
    d.line([20, 52, 20, 368], fill=(0, 0, 0), width=1)
    d.line([80, 180, 320, 180], fill=(90, 90, 90), width=1)   # free-floating line inside the L
    d.text((120, 120), 'Living 24.5 m2', fill='black')
    d.text((250, 18), '12.40', fill='black')
    p = tmp_path_factory.mktemp('vec') / 'synth.png'
    im.save(p)
    return p


@pytest.fixture(scope='module')
def vec(plan):
    return vectorize_image(plan)


def test_l_room_is_a_true_polygon(vec):
    assert vec['width'] == W and vec['height'] == H
    truth = Polygon(L)
    best = max(vec['rooms'], key=lambda r: Polygon(r['points']).buffer(0).intersection(truth).area)
    pts = [tuple(p) for p in best['points']]
    assert len(pts) > 4, pts
    assert abs(_shoelace(pts) - L_AREA) / L_AREA < 0.05
    assert abs(best['area_px'] - L_AREA) / L_AREA < 0.05


def test_three_rooms_and_walls_ignore_thin_lines(vec):
    assert len(vec['rooms']) == 3
    assert vec['walls'] and all(len(w['outer']) >= 4 for w in vec['walls'])
    # no wall reaches the dimension lines at y=30 / x=20
    for w in vec['walls']:
        assert min(p[1] for p in w['outer']) > 40 and min(p[0] for p in w['outer']) > 40


def test_svg_contract(vec):
    svg = plan_svg(vec)
    assert f'viewBox="0 0 {W} {H}"' in svg
    assert 'fill="#2f343c"' in svg and 'fill="#eef0f3"' in svg
    assert re.search(r'<rect width="\d+" height="\d+" fill="#ffffff"/>', svg)


@pytest.mark.parametrize('name', ['b1.jpg', 'b2.jpeg'])
def test_phone_photo_smoke(name):
    p = Path('/root/test-data') / name
    if not p.exists():
        pytest.skip('phone photos not present')
    v = vectorize_image(p)
    assert v['style'] == 'double-line' and len(v['rooms']) >= 4 and v['walls']


def test_blank_and_tiny_images(tmp_path):
    for size in [(1, 1), (40, 30), (300, 200)]:
        p = tmp_path / f'blank_{size[0]}.png'
        Image.new('RGB', size, 'white').save(p)
        v = vectorize_image(p)
        assert (v['width'], v['height']) == size and v['rooms'] == [] and v['walls'] == []
        assert f'viewBox="0 0 {size[0]} {size[1]}"' in plan_svg(v)
