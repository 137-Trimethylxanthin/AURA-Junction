"""Wall footprints cut at their openings, and the dataset sheet aligned to an IFC storey plan."""
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon

from aura.render.ifc_original import _fit, fit_transform, model_map, space_boxes, transform_from_map
from aura.render.openings import cut_openings

WALL = [(0, 0), (10, 0), (10, 0.3), (0, 0.3)]  # 10 m long, 0.3 m thick, along x


def area(rings):
    return sum(Polygon(r).area for r in rings)


def test_window_on_the_wall_face_cuts_a_gap_and_fills_it():
    win = [(4, 0.3), (5.2, 0.3), (5.2, 0.34), (4, 0.34)]  # thin frame on the face, 1.2 m wide
    walls, wins, doors = cut_openings([WALL], [win], [])
    assert len(walls) == 2  # the wall is split around the opening
    assert area(walls) == pytest.approx(3.0 - 1.2 * 0.3)
    gap = Polygon([(4, 0), (5.2, 0), (5.2, 0.3), (4, 0.3)])
    filled = Polygon(wins[0])
    assert filled.contains(gap.buffer(-1e-6))  # the window now spans the whole wall thickness
    assert doors == []


def test_door_is_redrawn_as_its_frame_in_the_gap():
    door = [(1, -0.05), (1.9, -0.05), (1.9, 0.0), (1, 0.0)]
    walls, _, doors = cut_openings([WALL], [], [door])
    frame = Polygon(doors[0])
    assert frame.bounds == pytest.approx((1, 0, 1.9, 0.3))
    assert area(walls) == pytest.approx(3.0 - 0.9 * 0.3)


def test_opening_not_in_a_wall_is_left_alone():
    win = [(20, 20), (22, 20), (22, 20.1), (20, 20.1)]
    walls, wins, _ = cut_openings([WALL], [win], [])
    assert area(walls) == pytest.approx(3.0)
    assert Polygon(wins[0]).equals(Polygon(win))


def test_a_closed_ring_of_walls_is_not_filled_in():
    ring = [[(0, 0), (6, 0), (6, 0.3), (0, 0.3)], [(5.7, 0), (6, 0), (6, 6), (5.7, 6)],
            [(0, 5.7), (6, 5.7), (6, 6), (0, 6)], [(0, 0), (0.3, 0), (0.3, 6), (0, 6)]]
    win = [(2, 0.3), (3, 0.3), (3, 0.32), (2, 0.32)]
    walls, _, _ = cut_openings(ring, [win], [])
    total = sum(Polygon(r).area for r in ring)
    assert area(walls) < total and area(walls) > total - 0.5  # only the opening was removed, no room filled


SHEET = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="100 50 200 150">
<g class="IfcSpace" data-guid="A"><path d="M110,60 L150,60 L150,90 L110,90 L110,60"/></g>
<g class="IfcSpace" data-guid="B"><path d="M150,60 L190,60 L190,100 L150,100"/></g>
<g class="IfcDoor" data-guid="D"><path d="M0,0 A5 5 0 0 1 999 999"/></g>
<g class="IfcWallStandardCase" data-guid="W"><path d="M110,58 L190,58 L190,60 L110,60"/></g>
</svg>"""


def test_sheet_boxes_keep_rooms_and_walls_only():
    vb, boxes = space_boxes(SHEET)
    assert vb == (100, 50, 200, 150)
    assert set(boxes) == {'A', 'B', 'W'}
    assert boxes['A'] == (110, 60, 150, 90)


def test_fit_recovers_scale_and_offset_and_drops_an_outlier():
    # Frame px = 4 * mm - 300 in x, 4 * mm - 100 in y. Room B's outline in our model is wrong on purpose.
    def T(x, y):
        return (x, y)
    rooms = [
        {'room_id': 'A', 'storey': 'L1', 'polygon': [(140, 140), (300, 140), (300, 260), (140, 260)]},
        {'room_id': 'B', 'storey': 'L1', 'polygon': [(0, 0), (5, 0), (5, 5)]},
        {'room_id': 'C', 'storey': 'L1', 'polygon': [(140, 132), (460, 132), (460, 140), (140, 140)]},
        {'room_id': 'E', 'storey': 'L1', 'polygon': [(300, 140), (460, 140), (460, 300), (300, 300)]},
    ]
    boxes = {'A': (110, 60, 150, 90), 'B': (150, 60, 190, 100), 'C': (110, 58, 190, 60), 'E': (150, 60, 190, 100)}
    model = SimpleNamespace(rooms=rooms, storey='L1', footprints={}, T=T)
    ax, bx, ay, by, resid, matched = fit_transform(model, boxes)
    assert (ax, bx, ay, by) == pytest.approx((4, -300, 4, -100))
    assert matched == 3 and resid == pytest.approx(0, abs=1e-6)


def test_two_agreeing_outlines_are_not_enough():
    rooms = [{'room_id': 'A', 'storey': 'L1', 'polygon': [(140, 140), (300, 140), (300, 260), (140, 260)]},
             {'room_id': 'C', 'storey': 'L1', 'polygon': [(140, 132), (460, 132), (460, 140), (140, 140)]}]
    model = SimpleNamespace(rooms=rooms, storey='L1', footprints={}, T=lambda x, y: (x, y))
    with pytest.raises(ValueError):
        fit_transform(model, {'A': (110, 60, 150, 90), 'C': (110, 58, 190, 60)})


def test_a_model_space_mapping_carries_over_to_a_storey_with_another_frame():
    # Storey 1 frame: px = 40·(X − 0) + 10, py = 40·(10 − Y) + 10. Sheet: mm = 360.5 + 10·X, my = 347 − 10·Y.
    s1 = SimpleNamespace(T=lambda x, y: (40 * x + 10, 40 * (10 - y) + 10))
    t1 = (4.0, 10 - 4 * 360.5, 4.0, 10 + 40 * 10 - 4 * 347)
    mm = model_map(s1, t1)
    assert mm == pytest.approx((10, 360.5, -10, 347))
    # Storey 2 has a different frame (shifted and cropped): its transform comes out of the same model map.
    s2 = SimpleNamespace(T=lambda x, y: (40 * (x - 1) + 5, 40 * (8 - y) + 5))
    ax, bx, ay, by = transform_from_map(s2, mm)
    for X, Y in [(0, 0), (3, 7.5), (12, 10)]:
        sx, sy = 360.5 + 10 * X, 347 - 10 * Y
        assert (ax * sx + bx, ay * sy + by) == pytest.approx(s2.T(X, Y))


def test_fit_needs_two_points():
    with pytest.raises(ValueError):
        _fit([(1, 1)])
