"""Enclosed spaces without a label that are not rooms (jstore.space_guard): the rule, on synthetic geometry."""
from shapely.geometry import box

from jstore import space_guard as sg

BY = "jstore.interpret.raster_plan"


def ring(x0, y0, x1, y1, t=0.2):
    """Four wall strips around a rectangle."""
    return [box(x0 - t, y0 - t, x1 + t, y0), box(x0 - t, y1, x1 + t, y1 + t),
            box(x0 - t, y0, x0, y1), box(x1, y0, x1 + t, y1)]


def space(eid, g, label=None, by=BY):
    return dict(entity_id=eid, label=label, by=by, geom=g)


def test_room_inside_walls_stays():
    rooms = [space(1, box(0, 0, 4, 3), "Living"), space(2, box(4.2, 0, 6, 3))]
    walls = ring(0, 0, 4, 3) + ring(4.2, 0, 6, 3)
    assert sg.false_spaces(rooms, walls, box(-5, -5, 15, 10)) == {}


def test_balcony_held_by_one_wall_and_a_named_room_stays():
    # a balcony: the façade on one long side, railings (no wall) on the others, but it is small and runs along a
    # labelled room for most of the wall side - 108's balcony is fully wall-bounded; this is the weaker case
    rooms = [space(1, box(0, 0, 4, 3), "Living"), space(2, box(0, 3.2, 4, 4.2))]
    walls = ring(0, 0, 4, 3) + ring(0, 3.2, 4, 4.2)
    assert sg.false_spaces(rooms, walls, box(-5, -5, 15, 10)) == {}


def test_strip_between_dimension_line_and_facade_goes():
    # wall on one long side only: 4 of 10 m of outline
    rooms = [space(1, box(0, 0, 4, 3), "Living"), space(2, box(0, 3.2, 4, 4.2))]
    out = sg.false_spaces(rooms, ring(0, 0, 4, 3), box(-5, -5, 15, 10))
    assert list(out) == [2] and "wall" in out[2]


def test_space_between_page_border_and_building_goes():
    page = box(0, 0, 10, 14)
    border = ring(0.15, 0.15, 9.85, 13.85, 0.15)          # a black border, read as wall
    rooms = [space(1, box(1, 4, 9, 10), "OH"), space(2, box(6, 10.3, 9.8, 13.8))]
    walls = border + ring(1, 4, 9, 10)
    out = sg.false_spaces(rooms, walls, page)
    assert list(out) == [2] and "edge of the page" in out[2]


def test_only_spaces_of_the_free_space_analysis_are_judged():
    rooms = [space(1, box(0, 0, 4, 3), "Living"), space(2, box(20, 20, 24, 23), by=None),
             space(3, box(30, 30, 34, 33), "Garden")]
    assert sg.false_spaces(rooms, ring(0, 0, 4, 3)) == {}
    assert sg.false_spaces([space(2, box(20, 20, 24, 23))], []) == {}      # no walls: nothing can be said
