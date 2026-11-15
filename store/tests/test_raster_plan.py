"""raster_plan: nominal scale, the two wall methods and their choice, openings, and the database side (offline)."""
from __future__ import annotations

import math
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from jstore.interpret import raster_plan as RP

MPP = 0.02                                    # 50 px per metre
# two rooms side by side, 4 m x 3 m each, 0.2 m wall between and around them
R1 = box(100, 100, 300, 250)
R2 = box(310, 100, 510, 250)
DOOR = ("door", 281.0, 250.0, 329.0, 298.0)   # a 0.96 m square box under the bottom wall of room 1... (leaf + swing)
WINDOW = ("window", 150.0, 88.0, 250.0, 102.0)


def _sheet(filled: bool) -> np.ndarray:
    img = np.full((400, 620, 3), 255, np.uint8)
    if filled:
        cv2.rectangle(img, (90, 90), (520, 260), (0, 0, 0), -1)
        for r in (R1, R2):
            x0, y0, x1, y1 = (int(v) for v in r.bounds)
            cv2.rectangle(img, (x0, y0), (x1, y1), (255, 255, 255), -1)
        cv2.rectangle(img, (150, 90), (250, 99), (255, 255, 255), -1)           # the window: a gap with thin lines
        cv2.line(img, (150, 95), (250, 95), (0, 0, 0), 1)
    else:                                                                       # walls as two thin lines
        cv2.rectangle(img, (90, 90), (520, 260), (0, 0, 0), 1)
        for r in (R1, R2):
            x0, y0, x1, y1 = (int(v) for v in r.bounds)
            cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 1)
    cv2.putText(img, "PLAN", (30, 380), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3)   # bold text far from the rooms
    return img


def test_scale_from_doors_and_fixtures():
    doors = [("door", 0, 0, 48, 50), ("door", 100, 0, 150, 47), ("door", 200, 0, 248, 48), ("door", 0, 100, 130, 110)]
    e = RP.estimate_scale(doors, [200 * 150], 620, 400)
    assert e["source"] == "doors" and e["votes"]["door"]["n"] == 3              # the sliding door does not vote
    assert e["mpp"] == pytest.approx(0.965 / 50, rel=0.03)
    e = RP.estimate_scale(doors + [("toilet", 0, 0, 20, 34), ("bathtub", 0, 0, 80, 38)], [], 620, 400)
    assert e["source"] == "doors and fixtures" and e["uncertainty"] < RP.UNCERTAINTY["doors"]
    assert e["mpp"] == pytest.approx(0.02, rel=0.05)


def test_scale_combines_estimators_and_records_each():
    # doors drawn large (a vote of 0.0141 m/px) against a toilet and typed rooms that say 0.0175: the doors are outvoted
    doors = [("door", 0, 0, 68, 66)] * 6
    rooms = [dict(px=20.0 / 0.0175 ** 2, type="living_room"), dict(px=12.5 / 0.0175 ** 2, type="bedroom"),
             dict(px=11.0 / 0.0175 ** 2, type="bedroom"), dict(px=8.0 / 0.0175 ** 2, type="kitchen")]
    e = RP.estimate_scale(doors + [("toilet", 0, 0, 22, 39)], rooms, 800, 700)
    assert e["mpp"] == pytest.approx(0.0175, rel=0.04)
    assert set(e["votes"]) == {"door", "toilet", "room_types"} and all("used" in v and "mpp" in v for v in e["votes"].values())
    alone = RP.estimate_scale(doors, [r["px"] for r in rooms], 800, 700)
    assert alone["mpp"] == pytest.approx(0.965 / 68, rel=1e-6) and alone["checks"] == {} or alone["uncertainty"] >= 0.3
    assert e["uncertainty"] > RP.estimate_scale([("door", 0, 0, 55, 55)] * 6 + [("toilet", 0, 0, 22, 39)], rooms, 800, 700)["uncertainty"]
    # a printed area or agreeing dimension lines decide; a disagreeing door vote is dropped and recorded as unused
    e = RP.estimate_scale(doors, [dict(px=40000.0, type="bedroom", printed_m2=16.0), dict(px=30000.0, printed_m2=12.0)], 800, 700)
    assert e["mpp"] == pytest.approx(0.02, rel=1e-6) and e["source"] == "printed areas" and not e["votes"]["door"]["used"]
    e = RP.estimate_scale(doors, [40000.0], 800, 700, dims=[(100.0, 2.5), (200.0, 5.0), (80.0, 2.02), (50.0, 9.0)])
    assert e["mpp"] == pytest.approx(0.025, rel=0.01) and e["source"] == "printed dimensions"
    # a usual room that comes out implausibly small is reported and lowers the confidence
    e = RP.estimate_scale([("door", 0, 0, 100, 100)] * 3, [dict(px=40000.0, type="bedroom")], 800, 700)
    assert e["checks"] or e["mpp"] > 0.012


def _core_sheet() -> tuple[np.ndarray, list]:
    """Two labelled rooms, a bathroom between walls without a label (with a door swing and a toilet inside), a balcony
    drawn with thin lines beside the building, and a frame around the sheet."""
    img = np.full((500, 700, 3), 255, np.uint8)
    cv2.rectangle(img, (90, 90), (520, 380), (0, 0, 0), -1)
    for x0, y0, x1, y1 in ((100, 100, 300, 250), (310, 100, 510, 250), (100, 260, 220, 370), (230, 260, 510, 370)):
        cv2.rectangle(img, (x0, y0), (x1, y1), (255, 255, 255), -1)
    cv2.ellipse(img, (100, 370), (45, 45), 0, 270, 360, (0, 0, 0), 1)         # a door swing in the bathroom
    cv2.rectangle(img, (180, 270), (205, 310), (0, 0, 0), 1)                  # the toilet
    cv2.rectangle(img, (520, 120), (600, 240), (0, 0, 0), 1)                  # the balcony
    cv2.rectangle(img, (10, 10), (690, 490), (0, 0, 0), 2)                    # sheet frame
    return img, [box(100, 100, 300, 250), box(310, 100, 510, 250), box(230, 260, 510, 370)]


def test_enclosed_spaces_without_a_label_become_rooms():
    img, rooms = _core_sheet()
    sp = RP.unlabelled_spaces(img, rooms, MPP, [("toilet", 180.0, 270.0, 205.0, 310.0), ("door", 100.0, 325.0, 145.0, 370.0)])
    assert len(sp) == 2
    bath = next(x for x in sp if x["indoor"])
    assert bath["type"] == "bathroom" and bath["fixtures"] == ["toilet"]
    assert bath["poly"].area == pytest.approx(120 * 110, rel=0.08)             # the whole space, door swing and toilet too
    assert bath["poly"].intersection(box(100, 260, 220, 370)).area > 0.95 * bath["poly"].area
    out = next(x for x in sp if not x["indoor"])
    assert out["type"] == "balcony" and out["poly"].area == pytest.approx(80 * 120, rel=0.1)
    # nothing is added on a plan whose rooms are all labelled, and the sheet background is never a room
    assert RP.unlabelled_spaces(_sheet(True), [R1, R2], MPP, [DOOR, WINDOW]) == []
    assert RP._space_type(["toilet"], 1.6, True)[0] == "toilet" and RP._space_type([], 6.0, True)[0] == "room"


def test_scale_falls_back_to_room_size_then_image():
    e = RP.estimate_scale([("window", 0, 0, 90, 8)], [150.0 * 150.0, 160.0 * 160.0, 100.0 * 100.0], 620, 400)
    assert e["source"] == "room size" and e["mpp"] == pytest.approx(3.15 / 150, rel=1e-6)
    assert e["uncertainty"] > RP.UNCERTAINTY["doors"]
    assert RP.estimate_scale([], [], 3000, 1000)["source"] == "image size"


def test_filled_walls_are_vectorised_from_the_pixels():
    p = RP.build(_sheet(True), [R1, R2], [True, True], [DOOR, WINDOW], MPP)
    assert p["method"] == "pixel_walls" and p["coverage"]["pixel_walls"] > 0.8
    walls = unary_union(p["walls"])
    assert walls.area == pytest.approx(430 * 170 - 2 * 200 * 150 - 100 * 10 - 42 * 10, rel=0.08)   # less window gap, door
    assert walls.intersection(unary_union([R1, R2])).area < 0.02 * walls.area   # walls lie between, not on, the rooms
    assert not walls.intersects(box(20, 340, 200, 390))                         # the bold text is not a wall
    assert walls.contains(box(302, 150, 308, 200))                              # the wall between the two rooms


def test_thin_line_walls_come_from_the_room_polygons():
    p = RP.build(_sheet(False), [R1, R2], [True, True], [DOOR, WINDOW], MPP)
    assert p["method"] == "room_gaps" and p["coverage"]["pixel_walls"] < 0.2
    assert p["thickness_px"] == pytest.approx(10, abs=1.5)                      # the gap between the two rooms
    walls = unary_union(p["walls"])
    assert walls.contains(box(302, 150, 308, 200))                              # between the rooms
    assert walls.intersects(box(95, 150, 99, 200)) and walls.intersects(box(511, 150, 515, 200))   # around them
    assert walls.intersection(unary_union([R1, R2])).area < 0.02 * walls.area
    # the door: a rectangle on the bottom wall, cut out of the walls; the window: on the top wall, wall kept
    door, axis = p["openings"][0]
    assert axis == "h" and 250 <= door.centroid.y <= 262 and door.bounds[2] - door.bounds[0] == pytest.approx(42, abs=3)
    assert walls.intersection(door).area < 0.05 * door.area
    win, axis = p["openings"][1]
    assert axis == "h" and win.bounds[0] == 150 and win.bounds[2] == 250 and walls.intersection(win).area > 0.5 * win.area


def test_outdoor_rooms_get_no_wall_band_of_their_own():
    balcony = box(100, 262, 300, 320)
    walls, _ = RP.room_gap_walls([R1, R2, balcony], [True, True, False], MPP)
    assert not unary_union(walls).intersects(box(150, 325, 250, 335))           # nothing below the balcony
    assert unary_union(walls).intersects(box(150, 252, 250, 260))               # the wall between room and balcony


# ------------------------------------------------------------------------------------------------ database side
FID = "feedface00000001"


@pytest.fixture
def scan(seeded):
    from jstore.db import DB, js
    data = Path(os.environ["JSTORE_DATA_DIR"])
    cv2.imwrite(str(data / "renders" / "scan1.png"), _sheet(False))
    with DB() as db:
        db.run("delete from jstore.project where project_id = 'test/rplan'")
        db.run("insert into jstore.project (project_id, title, source) values ('test/rplan', 'rplan', 'test')")
        db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status)
                  values (:f, 'test/rplan', 'uploads/rplan/scan1.png', 'png', 6, 'test_scan', 'interpreted')""", f=FID)
        db.run("""insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h)
                  values (:f, 0, '1', 620, 400, 'px', 'renders/scan1.png', 620, 400)""", f=FID)
        det = """insert into jstore.raster_detection (file_id, page_no, class, x0, y0, x1, y1, detector, attrs, geom)
                 values (:f, 0, :c, cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8), cast(:y1 as float8), :det,
                         '{}', st_makeenvelope(cast(:x0 as float8), cast(:y0 as float8), cast(:x1 as float8),
                                               cast(:y1 as float8))) returning id"""
        ent = """insert into jstore.entity (project_id, drawing_key, kind, label, attrs) values
                 ('test/rplan', 'test_scan', :k, :l, cast(:a as jsonb)) returning entity_id"""
        src = """insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role, bbox)
                 values (:e, :f, 0, :t, :k, :r, cast(:b as double precision[]))"""
        ids = {}
        for name, r, table in (("Kitchen", R1, "raster_detection"), ("Bedroom", R2, "region")):
            x0, y0, x1, y1 = r.bounds
            d = db.one(det, f=FID, c="room_outline", x0=x0, y0=y0, x1=x1, y1=y1, det="raster_rooms rr-1 outline")["id"]
            attrs = {"indoor": True} if table == "raster_detection" else {"indoor": True, "outline_detection": d}
            e = db.one(ent, k="room", l=name, a=js(attrs))["entity_id"]
            db.run(src, e=e, f=FID, t=table, k=str(d) if table == "raster_detection" else "room 1", r="outline",
                   b="{" + ",".join(str(v) for v in r.bounds) + "}")
            ids[name] = e
        for cls, x0, y0, x1, y1 in (DOOR, WINDOW, ("toilet", 120.0, 110.0, 140.0, 144.0)):
            d = db.one(det, f=FID, c=cls, x0=x0, y0=y0, x1=x1, y1=y1, det="gemini")["id"]
            e = db.one(ent, k=cls, l=None, a=js({"box_px": [x0, y0, x1, y1]}))["entity_id"]
            db.run(src, e=e, f=FID, t="raster_detection", k=str(d), r="symbol", b=None)
            ids[cls] = e
        yield db, ids
        db.run("delete from jstore.project where project_id = 'test/rplan'")


def test_interpret_gives_a_nominal_frame_polygons_walls_and_is_idempotent(scan):
    db, ids = scan
    f = db.one("select * from jstore.source_file where file_id = :f", f=FID)
    assert RP.plan(db, "test_scan") is None                                     # nothing placed yet
    first = RP.interpret(db, f)
    assert first["method"] == "room_gaps" and first["walls"] >= 1 and first["rooms_placed"] == 2
    assert first["scale_from"] == "doors and fixtures"
    fr = db.one("select m, method from jstore.frame where file_id = :f", f=FID)
    assert fr["method"] == "nominal_scale" and fr["m"][0] == pytest.approx(0.02, rel=0.05)
    mpp = fr["m"][0]
    room = db.one("""select st_area(geom) as a, geometrytype(geom) as t, attrs from jstore.entity where entity_id = :e""",
                  e=ids["Bedroom"])
    assert room["t"] == "POLYGON" and room["a"] == pytest.approx(200 * 150 * mpp * mpp, rel=1e-6)
    assert room["attrs"]["scale"] == "nominal" and room["attrs"]["scale_uncertainty"] > 0
    claims = db.run("select property, value_num, method, confidence from jstore.claim where entity_id = :e", e=ids["Kitchen"])
    assert {c["property"] for c in claims} == {"net_area_m2", "perimeter_m"}
    assert all(c["method"] == "nominal_outline" and c["confidence"] < 0.45 for c in claims)
    from jstore.interpret import reconcile
    reconcile.run(db, "test_scan")
    db.run("select set_config('jstore.drawings', 'test_scan', false)")
    assert db.one("select area_source from q.rooms where entity_id = :e", e=ids["Kitchen"])["area_source"] == "nominal_outline"
    d = db.one("select method, spec, confidence from jstore.definition where file_id = :f and concept = 'units'", f=FID)
    assert d["method"] == "nominal_scale" and d["spec"]["estimated_from"] == "doors and fixtures" and d["confidence"] <= 0.3
    d = db.one("select method, spec from jstore.definition where file_id = :f and concept = 'wall'", f=FID)
    assert d["method"] == "room_gaps" and set(d["spec"]["coverage_of_room_outlines"]) == {"pixel_walls", "room_gaps"}
    door = db.one("select attrs, st_ymax(geom) - st_ymin(geom) as h from jstore.entity where entity_id = :e", e=ids["door"])
    assert door["attrs"]["wall_axis"] == "h" and door["h"] == pytest.approx(door["attrs"]["wall_thickness_m"], abs=0.02)
    assert db.one("select geom is not null as g from jstore.entity where entity_id = :e", e=ids["toilet"])["g"]

    def counts():
        return (db.one("select count(*) as n from jstore.entity where drawing_key = 'test_scan' and kind = 'wall'")["n"],
                db.one("select count(*) as n from jstore.raster_detection where file_id = :f and class = 'wall'", f=FID)["n"],
                db.one("select count(*) as n from jstore.claim where file_id = :f", f=FID)["n"],
                db.one("select count(*) as n from jstore.entity_source where file_id = :f", f=FID)["n"],
                db.one("select count(*) as n from jstore.definition where file_id = :f", f=FID)["n"])
    before = counts()
    again = RP.interpret(db, f)
    assert counts() == before and again["walls"] == first["walls"] and before[0] == before[1] == first["walls"]

    p = RP.plan(db, "test_scan")
    assert p["frame"]["nominal"] and p["frame"]["method"] == "nominal_scale" and p["wall_method"] == "room_gaps"
    assert p["frame"]["source"].startswith("doors and fixtures") and 0 < p["frame"]["uncertainty"] <= 0.6
    assert len(p["rooms"]) == 2 and len(p["doors"]) == 1 and len(p["windows"]) == 1 and len(p["fixtures"]) == 1 and p["walls"]
    k = next(r for r in p["rooms"] if r["label"] == "Kitchen")
    xs, ys = [q[0] for q in k["px"][0]], [q[1] for q in k["px"][0]]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((100, 100, 300, 250), abs=0.01)
    assert max(q[0] for q in k["m"][0]) - min(q[0] for q in k["m"][0]) == pytest.approx(200 * mpp, abs=2e-3)
    assert math.isclose(p["frame"]["metres_per_pixel"], mpp)
    # walls are not fixtures
    assert db.one("select count(*) as n from q.fixtures where drawing = 'test_scan' and kind = 'wall'")["n"] == 0
    assert db.one("select count(*) as n from q.walls where drawing = 'test_scan'")["n"] == before[0]

    RP.drop_nominal_frame(db, FID)                                              # what raster_rooms does on a re-run
    assert db.one("select 1 as x from jstore.frame where file_id = :f", f=FID) is None


def test_the_plan_of_a_scan_is_a_vector_drawing_with_estimate_areas(scan):
    """aura_model: plan.svg of an image plan is drawn from the model (walls, rooms, openings), never the scan;
    the scan stays the original; rooms carry polygons and areas under a nominal scale are estimates."""
    from jstore.service import aura_model as M
    db, _ = scan
    db.run("update jstore.page set to_image = cast('{1,0,0,0,1,0}' as double precision[]) where file_id = :f", f=FID)
    RP.interpret(db, db.one("select * from jstore.source_file where file_id = :f", f=FID))
    m = M.build(db, "test_scan")
    assert m.vector_scan and m.nominal and m.walls, (m.base, len(m.rooms), [r["points"] for r in m.rooms])
    doc = M.drawing_doc(m)
    assert doc["images"] == {"rebuilt": "/drawings/test_scan/plan.svg", "original": "/drawings/test_scan/original.png",
                             "original_alignment": "exact"}
    assert doc["m_per_pct_x"] > 0 and len(doc["rooms"]) == 2
    assert all(len(r["points"]) >= 4 and r["area_source"] == "estimate" and r["area_m2"] > 0 for r in doc["rooms"])
    svg = M.plan_svg(db, m)
    assert "<image" not in svg and "data:image" not in svg
    assert 'data-plan="vector-scan"' in svg and 'data-scale="nominal"' in svg and M.ESTIMATE_NOTE in svg
    for look in (f'fill="{M.WALL_FILL}"', f'fill="#ffffff" stroke="{M.WINDOW_STROKE}"', f'fill="none" stroke="{M.DOOR_STROKE}"',
                 f'fill="none" stroke="{M.FIXTURE_STROKE}"'):
        assert look in svg, look                       # the styles the 3D view reads its layers by
    assert svg.count("<polygon") == 2 and ">Kitchen<" in svg and "≈ " in svg
    # the Kitchen polygon in the drawing is the room outline in percent of the scan (620 x 400 px)
    k = next(r for r in doc["rooms"] if r["name"] == "Kitchen")
    xs, ys = [p[0] for p in k["points"]], [p[1] for p in k["points"]]
    assert (min(xs), max(xs)) == pytest.approx((100 / 6.2, 300 / 6.2), abs=0.05)
    assert (min(ys), max(ys)) == pytest.approx((100 / 4.0, 250 / 4.0), abs=0.05)

    db.run("update jstore.entity set status = 'retired' where drawing_key = 'test_scan' and kind in ('room', 'wall')")
    bare = M.build(db, "test_scan")                    # no model of the plan: its picture only, nothing to compare
    assert not bare.vector_scan and "original" not in M.drawing_doc(bare)["images"]


def test_a_long_door_box_across_a_wall_opens_on_that_wall():
    """Leaves drawn open to both sides make a door's box long across its wall: the opening follows the wall, not
    the box.  A long box with wall along its centre line (a sliding or double door) stays along the box."""
    from shapely.geometry import box
    from jstore.interpret.raster_plan import opening
    wall = box(0, 95, 400, 105)                               # a horizontal wall, 10 px thick
    g, axis = opening((100, 30, 170, 170), "door", wall, 10.0)   # 70 x 140, the wall through its middle
    assert axis == "h" and g.bounds == (100.0, 95.0, 170.0, 105.0)
    g, axis = opening((100, 92, 240, 108), "door", wall, 10.0)   # 140 x 16, lying in the wall
    assert axis == "h" and g.bounds[0] == 100.0 and g.bounds[2] == 240.0
    g, axis = opening((100, 130, 170, 270), "door", wall, 10.0)  # no wall in the box: the long axis, as before
    assert axis == "v"
    g, axis = opening((100, 30, 170, 170), "window", wall, 10.0)
    assert axis == "v"


def test_model_estimate_votes_and_outweighs_a_lone_door_estimate():
    from jstore.interpret import scale_model as SM
    doors = [("door", 0, 0, 48, 48)] * 4                                 # doors say 0.965 / 48 = 0.0201 m per pixel
    alone = RP.estimate_scale(doors, [200 * 150], 620, 400)
    ans = dict(objects=[dict(what="bathtub", box_2d=[0, 0, 100, 200], long_side_m=1.7),     # 124 px -> 0.0137
                        dict(what="bed", box_2d=[0, 0, 400, 100], long_side_m=2.0),         # 160 px -> 0.0125
                        dict(what="door", box_2d=[0, 0, 100, 100], long_side_m=0.85),       # 62 px -> 0.0137
                        dict(what="nonsense", box_2d=[1, 2], long_side_m=3)],
               image_width_m=8.5)                                                           # 620 px -> 0.0137
    m = SM.read(ans, 620, 400)
    assert m["n"] == 3 and abs(m["objects_mpp"] - 0.0137) < 2e-4 and abs(m["mpp"] - 0.0137) < 2e-4
    e = RP.estimate_scale(doors, [200 * 150], 620, 400, None, m)
    assert abs(e["mpp"] - m["mpp"]) < 1e-6 and e["votes"]["model"]["used"] and not e["votes"]["door"]["used"]
    assert "judged by the model" in e["source"] and abs(alone["mpp"] - 0.0201) < 2e-4
    near = RP.estimate_scale(doors, [200 * 150], 620, 400, None, dict(mpp=0.019, n=9))
    assert near["votes"]["door"]["used"] and near["source"].startswith("doors and ")
    assert SM.read(dict(objects=[dict(what="door", box_2d=[0, 0, 100, 100], long_side_m=0.85)]), 620, 400) is None
    assert SM.read(None, 620, 400) is None
