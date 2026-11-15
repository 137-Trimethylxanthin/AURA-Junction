"""Finish of an image plan's vector drawing: labels, ragged outlines, wall fragments, duplicate symbols."""
import shapely
from shapely.geometry import Polygon, box

from jstore.service import plan_tidy as T

U = 0.02     # metres per unit: 50 units = 1 m


def test_pole_is_in_the_wide_part_of_an_l_shaped_room():
    room = Polygon([(0, 0), (100, 0), (100, 20), (20, 20), (20, 100), (0, 100)])
    x, y, r = T.pole(room)
    assert room.contains(shapely.Point(x, y)) and 9 <= r <= 12


def test_labels_never_overlap_and_shrink_or_shorten_to_fit():
    big = dict(poly=box(0, 0, 200, 100), lines=["Corridor", "56.88 m²"])
    small = dict(poly=box(200, 40, 214, 43), lines=["Store", "5.93 m²"])        # fits its name only
    tiny = dict(poly=box(300, 0, 304, 4), lines=["Riser", "0.20 m²"])           # fits nothing
    over = dict(poly=box(50, 20, 150, 80), lines=["Reception", "26.66 m²"])     # lies over the big room's middle
    out = T.place_labels([big, small, tiny, over], 3.0, 11.0)
    assert out[0]["lines"] == big["lines"] and out[0]["size"] == 11.0
    assert out[1]["lines"] == ["Store"] and out[1]["size"] < 11.0
    assert out[2] is None

    def rect(lab):
        w = T.CHAR_W * max(len(t) for t in lab["lines"]) * lab["size"]
        h = T.LINE_H * lab["size"] * len(lab["lines"])
        return box(lab["x"] - w / 2, lab["y"] - h / 2, lab["x"] + w / 2, lab["y"] + h / 2)
    drawn = [rect(x) for x in out if x]
    assert all(not a.intersects(b) for i, a in enumerate(drawn) for b in drawn[i + 1:])
    assert out[3] is None or not rect(out[3]).intersects(rect(out[0]))


def test_ragged_outline_is_straightened_inside_its_walls():
    cell = box(0, 0, 100, 80)                                                    # 2 m x 1.6 m between walls
    walls = cell.buffer(8, join_style="mitre").difference(cell)
    ragged = cell.difference(box(0, 0, 40, 30)).difference(box(60, 50, 100, 80)).difference(
        Polygon([(45, 80), (50, 60), (55, 80)]))                                 # carved by a tub, a basin and a spike
    out = T.tidy_outline(ragged, U, walls, None)
    assert out.geom_type == "Polygon" and len(out.exterior.coords) - 1 == 4
    assert out.symmetric_difference(cell).area < 0.03 * cell.area
    assert out.intersection(walls).area < 1.0
    neighbour = box(0, 0, 40, 30)                                                # another room is never covered
    kept = T.tidy_outline(ragged, U, walls, neighbour)
    assert kept.intersection(neighbour).area < 1.0
    assert T.tidy_outline(ragged, 0.0, walls, None) is ragged                    # no scale: left alone


def test_wall_fragments_go_and_openings_are_cut():
    room = box(0, 0, 200, 150)
    shell = room.buffer(10, join_style="mitre").difference(room)
    sliver = box(50, 60, 90, 62)             # 0.8 m x 0.04 m inside the room
    stub = box(120, 70, 128, 90)             # 0.16 m x 0.4 m
    column = box(150, 100, 170, 120)         # 0.4 m x 0.4 m: stays
    far = box(900, 900, 1000, 1000)          # fill away from every room
    door = box(80, -10, 125, 0)              # the opening of a placed door in the lower wall
    out = shapely.union_all(T.clean_walls([shell, sliver, stub, column, far], U, room, door))
    assert not out.intersects(sliver) and not out.intersects(stub) and not out.intersects(far)
    assert out.contains(column)
    assert out.intersection(door).area < 1e-6 and out.area > shell.area - door.area - 1


def test_duplicate_door_symbols_and_windows():
    a = dict(mode="single", a=(100, 50), b=(145, 50), n=(0, 1), anchored=True)
    twin = dict(mode="marker", a=(95, 52), b=(160, 52), n=(0, 1), anchored=True, oversized=True)
    across = dict(mode="single", a=(145, 50), b=(145, 95), n=(1, 0), anchored=True)      # the next wall: its own door
    beside = dict(mode="single", a=(150, 50), b=(195, 50), n=(0, 1), anchored=True)      # the next opening
    assert T.same_opening(a, twin, U) and not T.same_opening(a, across, U) and not T.same_opening(a, beside, U)
    assert T.door_rank(a) > T.door_rank(twin)
    assert T.duplicate_boxes([(0, 0, 60, 6), (2, 0, 50, 6), (100, 0, 60, 6)]) == {1}


def test_labels_of_an_outline_that_crosses_itself():
    """A bow-tie outline is two pieces once made valid: the label goes into the larger one (this was an error, and
    the whole plan failed to draw: Käärmetalo sheet mfa.01a30b06)."""
    import shapely
    bow = shapely.Polygon([(0, 0), (40, 0), (40, 30), (60, 50), (60, 30), (40, 50), (40, 30), (0, 30)])
    assert not bow.is_valid
    out = T.place_labels([dict(poly=bow, lines=["Room", "12 m²"])], 3.0, 11.0)
    assert out[0] is not None and 0 < out[0]["x"] < 40
