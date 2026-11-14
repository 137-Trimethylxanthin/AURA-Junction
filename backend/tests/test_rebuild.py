"""Rooms of an image upload take the drawn region their label sits in (polygons, not label boxes)."""
from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from aura import rebuild

W, H = 1000, 500
L_SHAPE = [[100, 100], [500, 100], [500, 250], [300, 250], [300, 400], [100, 400]]  # px
OPEN_PLAN = [[520, 100], [900, 100], [900, 400], [520, 400]]


def drawing(**kw):
    box = [[10.0, 20.0], [50.0, 20.0], [50.0, 80.0], [10.0, 80.0]]
    return {
        "id": "up-x", "m_per_pct_x": 0.2, "m_per_pct_y": 0.1,
        "rooms": [
            {"room_id": "a", "num": "a", "name": "LIVING", "points": box, "label_at": [20.0, 40.0], "area_m2": None},
            {"room_id": "b", "num": "b", "name": "KITCHEN", "points": box, "label_at": [60.0, 40.0], "area_m2": None},
            {"room_id": "c", "num": "c", "name": "DINING", "points": box, "label_at": [85.0, 40.0], "area_m2": 12.5,
             "area_source": "printed"},
            {"room_id": "d", "num": "d", "name": "YARD", "points": box, "label_at": [5.0, 5.0], "area_m2": None},
        ],
    } | kw


VEC = {"width": W, "height": H, "walls": [[[0, 0], [1, 0], [1, 1]]],
       "rooms": [{"points": L_SHAPE}, {"points": OPEN_PLAN}, {"points": [[0, 0], [W, 0], [W, H], [0, H]]}]}


def test_label_in_l_shape_gets_the_l_polygon_and_its_area():
    out = rebuild.refine_rooms(drawing(), VEC)
    a = out["rooms"][0]
    assert len(a["points"]) == 6
    pct_area = Polygon(a["points"]).area  # in pct² units
    assert pct_area == pytest.approx((400 * 150 + 200 * 150) / (W * H) * 100 * 100, rel=1e-3)
    assert a["area_m2"] == pytest.approx(round(pct_area * 0.2 * 0.1, 2))
    assert a["area_source"] == "outline"


def test_two_labels_in_one_region_split_it_and_printed_areas_stay():
    out = rebuild.refine_rooms(drawing(), VEC)
    b, c = out["rooms"][1], out["rooms"][2]
    pb, pc = Polygon(b["points"]), Polygon(c["points"])
    assert pb.intersection(pc).area < 1e-6
    assert pb.area + pc.area == pytest.approx(38 * 60, rel=1e-3)
    assert c["area_m2"] == 12.5 and c["area_source"] == "printed"


def test_label_outside_every_region_and_the_sheet_region_are_left_alone():
    out = rebuild.refine_rooms(drawing(), VEC)
    assert out["rooms"][3]["points"] == drawing()["rooms"][3]["points"]


def test_no_scale_no_area():
    out = rebuild.refine_rooms(drawing(m_per_pct_x=0, m_per_pct_y=0), VEC)
    assert out["rooms"][0]["area_m2"] is None and len(out["rooms"][0]["points"]) == 6


def test_apply_never_fails_the_upload(tmp_path):
    d = drawing()
    assert rebuild.apply(tmp_path, d) is d  # no original.png: unchanged


def test_apply_end_to_end_on_a_drawn_plan(tmp_path):
    """A drawn L-shaped room: plan.svg with walls and the room name; the room's box becomes the L polygon."""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (800, 600), "white")
    g = ImageDraw.Draw(im)
    ring = [(100, 100), (600, 100), (600, 300), (350, 300), (350, 500), (100, 500), (100, 100)]
    g.line(ring, fill="black", width=10, joint="curve")
    im.save(tmp_path / "original.png")
    d = {"id": "up-t", "m_per_pct_x": 0.1, "m_per_pct_y": 0.1, "images": {"rebuilt": "/drawings/up-t/original.png"},
         "rooms": [{"room_id": "1", "num": "1", "name": "LIVING", "label_at": [25.0, 50.0],
                    "points": [[20.0, 40.0], [30.0, 40.0], [30.0, 60.0], [20.0, 60.0]], "area_m2": None}]}
    out = rebuild.apply(tmp_path, d)
    svg = (tmp_path / "plan.svg").read_text()
    assert 'fill="#2f343c"' in svg and ">LIVING</text>" in svg and 'viewBox="0 0 800 600"' in svg
    assert out["images"]["rebuilt"] == "/drawings/up-t/plan.svg"
    assert out["images"]["original"] == "/drawings/up-t/original.png"
    room = out["rooms"][0]
    assert len(room["points"]) >= 6
    # Net floor area, inside the 10 px walls: the centre-line L (150 000 px²) less half a wall along its 2000 px
    # perimeter; m² at 0.1 m per pct.
    truth = (500 * 200 + 250 * 200 - 2000 * 5) / (800 * 600) * 100 * 100 * 0.1 * 0.1
    assert room["area_m2"] == pytest.approx(truth, rel=0.05)
