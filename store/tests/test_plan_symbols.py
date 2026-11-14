"""Door symbols of an image plan: held to a plausible width, anchored to the wall, never dominating."""
import shapely
from shapely.geometry import LineString, Polygon

from jstore.service.aura_model import _walls_around_rooms
from jstore.service.plan_symbols import door_paths, door_symbol, opening_cut

U = 0.01    # one unit is a centimetre
# a horizontal wall 20 thick at y 90..110, with a 90 wide opening at x 200..290
WALL = shapely.union_all([shapely.box(0, 90, 200, 110), shapely.box(290, 90, 600, 110)])


def length(sym) -> float:
    return LineString([sym["a"], sym["b"]]).length


def test_an_opening_in_a_wall_is_a_single_door_swinging_off_the_wall():
    sym = door_symbol(200, 95, 90, 10, WALL, U)
    assert sym["mode"] == "single" and sym["anchored"] and abs(length(sym) - 90) < 1
    paths = door_paths(sym, "#000")
    assert sum(" A" in p or "A90" in p for p in paths) == 1          # one swing, of the leaf's own radius
    assert "A90.0,90.0" in "".join(paths)


def test_a_swing_box_is_anchored_to_the_edge_that_lies_on_the_wall():
    sym = door_symbol(200, 110, 90, 90, WALL, U)                      # the box hangs below the wall
    assert sym["mode"] == "single" and sym["anchored"]
    assert sym["a"][1] == sym["b"][1] == 110 and sym["n"] == (0.0, 1.0)


def test_an_oversized_box_never_gets_an_oversized_swing():
    sym = door_symbol(150, 110, 260, 250, WALL, U)                    # 2.6 m: no door is that wide
    assert sym["mode"] in ("single", "marker")
    assert length(sym) * U <= 1.25
    if sym["mode"] == "single":                                       # the wall's own gap is the door
        assert 195 <= min(sym["a"][0], sym["b"][0]) and max(sym["a"][0], sym["b"][0]) <= 295
    far = door_symbol(1000, 1000, 260, 250, WALL, U)                  # no wall anywhere near: a plain marker
    assert far["mode"] == "marker" and not far["anchored"] and length(far) * U <= 1.0
    assert not any("A" in p.split('d="')[1].split('"')[0] for p in door_paths(far, "#000"))


def test_a_wide_opening_in_a_wall_is_a_double_door_of_two_half_leaves():
    wall = shapely.union_all([shapely.box(0, 90, 200, 110), shapely.box(380, 90, 600, 110)])
    sym = door_symbol(200, 95, 180, 10, wall, U)
    assert sym["mode"] == "double"
    assert "".join(door_paths(sym, "#000")).count("A90.0,90.0") == 2
    assert door_symbol(200, 95, 320, 10, wall, U)["mode"] == "marker"  # 3.2 m: an opening, no leaf


def test_a_door_detected_as_a_bare_line_is_still_a_door():
    sym = door_symbol(200, 100, 90, 0, WALL, U)
    assert sym["mode"] == "single" and abs(length(sym) - 90) < 1


def test_the_opening_of_a_placed_door_cuts_the_wall():
    solid = shapely.box(0, 90, 600, 110)                              # walls derived without openings
    sym = door_symbol(200, 95, 90, 10, solid, U)
    cut = opening_cut(sym, solid, U)
    assert cut is not None and abs(cut.area - 90 * 20) < 1


def test_a_room_inside_wall_fill_opens_the_fill():
    walls = [Polygon([(0, 0), (40, 0), (40, 40), (0, 40)])]
    rooms = [dict(points=[[10, 10], [30, 10], [30, 30], [10, 30]]),           # swallowed by the fill
             dict(points=[[38, 0], [80, 0], [80, 40], [38, 40]])]             # merely touching a wall
    out = shapely.union_all(_walls_around_rooms(walls, rooms))
    assert not out.contains(shapely.Point(20, 20)) and out.contains(shapely.Point(39, 20))
    assert abs(out.area - (1600 - 400)) < 1e-6
