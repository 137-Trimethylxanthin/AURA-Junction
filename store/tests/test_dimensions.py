"""Dimensions printed on the sheet: the parsers (table-driven), the scale from dimension lines, and the reader on
synthetic data (a scan with scripted Gemini answers, a CAD file with DIMENSION entities)."""
from __future__ import annotations

import os

import pytest
from PIL import Image

from jstore.db import DB, apply_schema, js
from jstore.interpret import dimensions as D

FT, IN = 0.3048, 0.0254

LENGTHS = [
    # text, hint, metres, unit
    ("3.60", None, 3.60, "m"),
    ("3,60", None, 3.60, "m"),
    ("3.60 m", None, 3.60, "m"),
    ("360 cm", None, 3.60, "cm"),
    ("3600mm", None, 3.60, "mm"),
    ("360", "cm", 3.60, "cm"),
    ("3600", "mm", 3.60, "mm"),
    ("12", "in", 12 * IN, "in"),
    ("12'-6\"", None, 12 * FT + 6 * IN, "ft-in"),
    ("12' - 6\"", None, 12 * FT + 6 * IN, "ft-in"),
    ("12'6\"", None, 12 * FT + 6 * IN, "ft-in"),
    ("22' - 3 1/8\"", None, 22 * FT + 3.125 * IN, "ft-in"),
    ("12'", None, 12 * FT, "ft"),
    ("12' - 0\"", None, 12 * FT, "ft-in"),
    ("4 1/2\"", None, 4.5 * IN, "in"),
    ("3/4\"", None, 0.75 * IN, "in"),
    ("6\"", None, 6 * IN, "in"),
    ("12’-6”", None, 12 * FT + 6 * IN, "ft-in"),          # typographic quotes
    ("5'-0\" TYP.", None, 5 * FT, "ft-in"),
    ("3'-6\" MIN. CLR", None, 3.5 * FT, "ft-in"),
    ("ca. 4.25 m", None, 4.25, "m"),
    ("12 ft 6 in", None, 12 * FT + 6 * IN, "ft-in"),
]


@pytest.mark.parametrize("text, hint, metres, unit", LENGTHS)
def test_lengths(text, hint, metres, unit):
    got = D.parse(text, hint)
    assert got and got["kind"] == "linear", text
    assert got["value_m"] == pytest.approx(metres, abs=1e-4)
    assert got["unit"] == unit


@pytest.mark.parametrize("text", ["360", "1200"])
def test_a_bare_integer_has_no_value_without_the_unit_of_the_sheet(text):
    assert D.parse_length(text)["value_m"] is None
    assert D.parse_length(text, "cm")["value_m"] == pytest.approx(float(text) / 100)


ROOM_SIZES = [
    ("3.20 x 4.10", None, 3.20, 4.10),
    ("3,20 × 4,10 m", None, 3.20, 4.10),
    ("320 x 410", "cm", 3.20, 4.10),
    ("320 x 410 cm", None, 3.20, 4.10),
    ("12'0\" x 10'6\"", None, 12 * FT, 10.5 * FT),
    ("12'-0\" X 10'-6\"", None, 12 * FT, 10.5 * FT),
    ("2.40 x 3.10", None, 2.40, 3.10),
    ("11' 4\" by 9' 2\"", None, 11 * FT + 4 * IN, 9 * FT + 2 * IN),
]


@pytest.mark.parametrize("text, hint, a, b", ROOM_SIZES)
def test_room_sizes(text, hint, a, b):
    got = D.parse(text, hint)
    assert got and got["kind"] == "room_size", text
    assert (got["a_m"], got["b_m"]) == (pytest.approx(a, abs=1e-4), pytest.approx(b, abs=1e-4))
    assert got["value_m"] == pytest.approx(max(a, b), abs=1e-4)


AREAS = [("12.5 m²", 12.5), ("12,5 m2", 12.5), ("12.5 qm", 12.5), ("147 SF", 147 * FT * FT),
         ("147 sq. ft.", 147 * FT * FT), ("18.36 m²", 18.36), ("NF: 21.4 m2", 21.4)]


@pytest.mark.parametrize("text, m2", AREAS)
def test_areas(text, m2):
    got = D.parse(text)
    assert got and got["kind"] == "area"
    assert got["area_m2"] == pytest.approx(m2, abs=1e-3)


LEVELS = [("+2.80", 2.80), ("-0.15", -0.15), ("±0.00", 0.0), ("EL. 100'-0\"", 100 * FT), ("FFL +3.25", 3.25)]


@pytest.mark.parametrize("text, metres", LEVELS)
def test_levels(text, metres):
    got = D.parse(text)
    assert got and got["kind"] == "level"
    assert got["value_m"] == pytest.approx(metres, abs=1e-4)


@pytest.mark.parametrize("text", ["", "Kitchen", "A-101", "1:100", "2.01", "10/11/2019", "608.251.8777", "B2",
                                  "1/8\" = 1'-0\"", "ROOM 12 x"])
def test_what_is_not_a_dimension(text):
    got = D.parse(text)
    # a bare decimal is a length only by context (the reader asks for dimension strings), everything else is nothing
    assert got is None or (text == "2.01" and got["kind"] == "linear")


def test_unit_of_bare_numbers_follows_the_sheet():
    assert D.infer_unit(["360", "425", "1210", "88"]) == "cm"
    assert D.infer_unit(["3600", "4250", "12100"]) == "mm"
    assert D.infer_unit(["12'-6\"", "4'-0\"", "36"]) == "in"
    assert D.infer_unit(["360"], "mm") == "mm"
    assert D.infer_unit(["3.60", "4.25"]) is None


def test_scale_needs_three_lines_that_agree_and_ignores_the_misread():
    true = 0.02
    pairs = [(180, 3.6), (212, 4.25), (500, 10.0), (90, 1.8), (300, 6.1), (100, 9.0)]     # the last is a misread
    sc = D.scale_from(pairs)
    assert sc and sc["n"] == 5 and sc["of"] == 6
    assert sc["mpp"] == pytest.approx(true, rel=0.02)
    assert D.scale_from(pairs[:2]) is None
    assert D.scale_from([(100, 1.0), (100, 2.0), (100, 4.0)]) is None


def test_bare_numbers_take_the_unit_under_which_the_lines_agree():
    # centimetres on a sheet that states no unit: 0.01 m per pixel
    items = [dict(text=t, said="linear", box=[0, 0, 10, 10], line=[(0, 0), (px, 0)], room=None, edge=50)
             for t, px in (("360", 360), ("425", 425), ("1210", 1210), ("88", 88), ("5", 300))]
    assert D.understand(items, "unknown") == "cm"
    sc = D.page_scale(items)
    assert sc["mpp"] == pytest.approx(0.01, rel=0.01) and sc["n"] == 4


def test_answers_of_overlapping_tiles_are_one_reading():
    left = D.items_of({"dimensions": [{"text": "3.60", "kind": "linear", "text_box": [480, 880, 520, 960],
                                       "p1": [500, 700], "p2": [500, 990]}]}, (0, 0, 1000, 1000))
    right = D.items_of({"dimensions": [{"text": "3.60", "kind": "linear", "text_box": [480, 130, 520, 195],
                                        "p1": [500, 20], "p2": [500, 240]},
                                       {"text": "MEZZANINE +2.80", "kind": "level", "text_box": [100, 500, 120, 600]}]},
                       (800, 0, 1800, 1000))
    both = D.dedupe(left + right)
    assert sorted(i["text"] for i in both) == ["3.60", "MEZZANINE +2.80"]
    kept = next(i for i in both if i["text"] == "3.60")
    assert kept["line"] == [(820.0, 500.0), (1040.0, 500.0)]          # the reading farther from a tile border
    D.understand(both, "m")
    assert next(i for i in both if i["said"] == "level")["parsed"]["value_m"] == pytest.approx(2.80)


# ------------------------------------------------------------------ the reader, on synthetic data
PNG, DXF = "d" * 15 + "1", "d" * 15 + "2"


@pytest.fixture()
def sheet(monkeypatch):
    """A scan (1000 x 800 px, 0.01 m per pixel, y down) with one room, and a DXF with two DIMENSION entities."""
    data = os.environ["JSTORE_DATA_DIR"]
    os.makedirs(os.path.join(data, "renders"), exist_ok=True)
    Image.new("RGB", (1000, 800), "white").save(os.path.join(data, "renders", "dims.png"))
    apply_schema()
    with DB() as db:
        db.run("delete from jstore.project where project_id = 'test/dims'")
        db.run("insert into jstore.project (project_id, title, source) values ('test/dims', 'dims', 'test')")
        for fid, fmt, key in ((PNG, "png", "test_dims_scan"), (DXF, "dxf", "test_dims_cad")):
            db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status)
                      values (:f, 'test/dims', :p, :fmt, 1, :k, 'parsed')""", f=fid, p=f"uploads/dims/x.{fmt}", fmt=fmt, k=key)
        db.run("""insert into jstore.page (file_id, page_no, width, height, unit, image, image_w, image_h, to_image)
                  values (:f, 0, 1000, 800, 'px', 'renders/dims.png', 1000, 800, '{1,0,0,0,1,0}')""", f=PNG)
        db.run("insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, '{0.01,0,0,0,-0.01,8}', 'pixel_scale')", f=PNG)
        db.run("insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, '{0.001,0,0,0,0.001,0}', 'units')", f=DXF)
        db.run("""insert into jstore.entity (project_id, drawing_key, kind, number, label, geom)
                  values ('test/dims', 'test_dims_scan', 'room', '1', 'Kitchen', st_geomfromtext('POLYGON((1 2,4.6 2,4.6 6,1 6,1 2))'))""")
        w = db.one("""insert into jstore.raster_word (file_id, page_no, text, conf, x0, y0, x1, y1, engine)
                      values (:f, 0, '3.60', 0.9, 262, 92, 298, 108, 'test') returning id""", f=PNG)["id"]
        for handle, text, meas, wkt in (("D1", None, 3600.0, "LINESTRING(1000 0,4600 0)"),
                                        ("D2", "<> TYP", 900.0, "LINESTRING(0 0,0 900)")):
            db.run("""insert into jstore.cad_entity (file_id, handle, dxftype, layer, text, geom, attrs)
                      values (:f, :h, 'DIMENSION', 'DIM', :t, st_geomfromtext(:g), cast(:a as jsonb))""",
                   f=DXF, h=handle, t=text, g=wkt, a=js(dict(measurement=meas, dimtype=32, text_override=text or "")))
    answer = dict(units="m", items=[
        dict(text="3.60", said="linear", box=[260, 90, 300, 110], line=[(100, 120), (460, 120)], room=None, edge=80),
        dict(text="4.00", said="linear", box=[60, 380, 80, 420], line=[(90, 200), (90, 600)], room=None, edge=60),
        dict(text="8.50", said="linear", box=[400, 40, 440, 60], line=[(100, 70), (950, 70)], room=None, edge=40),
        dict(text="2.00", said="linear", box=[700, 300, 740, 320], line=[(700, 330), (900, 330)], room=None, edge=90),
        dict(text="3.60 x 4.00", said="room_size", box=[240, 420, 330, 440], line=None, room="Kitchen", edge=200),
        dict(text="14.4 m2", said="area", box=[250, 445, 320, 462], line=None, room="Kitchen", edge=200),
        dict(text="see detail 4", said="linear", box=[600, 600, 700, 620], line=None, room=None, edge=100)])
    monkeypatch.setattr(D, "read_page", lambda im, stats, log: dict(answer, items=[dict(i) for i in answer["items"]]))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    yield dict(word=w)
    with DB() as db:
        db.run("delete from jstore.issue where project_id = 'test/dims'")
        db.run("delete from jstore.project where project_id = 'test/dims'")


def _scope(db: DB, key: str, *files: str) -> None:
    db.run("select set_config('jstore.drawings', :d, false), set_config('jstore.files', :f, false)", d=key, f=",".join(files))


def test_reader_on_a_scan(sheet):
    with DB() as db:
        f = db.one("select * from jstore.source_file where file_id = :f", f=PNG)
        out = D.interpret(db, f)
        assert out["new"] == 6 and out["room_claims"] == 3
        _scope(db, "test_dims_scan", PNG)
        rows = {r["text"]: r for r in db.run("""select entity_id, kind, text, value_m, unit, orientation, room, a_m, b_m,
                                                       area_m2, "check", round(st_length(geom)::numeric, 2) as len
                                                from q.dimensions""")}
        assert set(rows) == {"3.60", "4.00", "8.50", "2.00", "3.60 x 4.00", "14.4 m2"}
        d = rows["3.60"]
        assert (d["kind"], d["value_m"], d["orientation"], float(d["len"]), d["check"]) == ("linear", 3.6, "horizontal", 3.6, 1.0)
        assert rows["4.00"]["orientation"] == "vertical"
        assert rows["8.50"]["kind"] == "overall"
        assert rows["2.00"]["room"] is None and rows["3.60 x 4.00"]["room"] == "1"
        assert (rows["3.60 x 4.00"]["a_m"], rows["3.60 x 4.00"]["b_m"]) == (3.6, 4.0)
        assert rows["14.4 m2"]["area_m2"] == 14.4
        # sources: the detection of the reader, and the OCR word under the string
        src = db.run("select ref_table, ref_key, role from q.entity_sources where entity_id = :e order by 1", e=d["entity_id"])
        assert [(s["ref_table"], s["role"]) for s in src] == [("raster_detection", "dimension"), ("raster_word", "text")]
        assert src[1]["ref_key"] == str(sheet["word"])
        assert db.one("select count(*) n from q.definitions where concept = 'dimension'")["n"] == 1
        # the room: printed size and the printed area nothing else had read
        size = db.one("select length_m, width_m, size_source from q.room_sizes where number = '1'")
        assert (size["length_m"], size["width_m"], size["size_source"]) == (4.0, 3.6, "printed")
        room = db.one("select area_m2, area_source from q.rooms where number = '1'")
        assert (room["area_m2"], room["area_source"]) == (14.4, "printed")
        assert db.one("select count(*) n from q.items where kind = 'dimension'")["n"] == 0
        # read once per drawing
        assert "skipped" in D.interpret(db, f)


def test_room_size_is_measured_where_none_is_printed(sheet):
    with DB() as db:
        _scope(db, "test_dims_scan", PNG)
        size = db.one("select length_m, width_m, size_source from q.room_sizes where number = '1'")
        assert (size["length_m"], size["width_m"], size["size_source"]) == (4.0, 3.6, "measured_bounding_box")


def test_reader_on_cad_dimensions(sheet):
    with DB() as db:
        f = db.one("select * from jstore.source_file where file_id = :f", f=DXF)
        assert D.interpret(db, f)["new"] == 2
        _scope(db, "test_dims_cad", DXF)
        rows = {r["text"]: r for r in db.run("select text, value_m, orientation, round(st_length(geom)::numeric, 3) as len from q.dimensions")}
        assert rows["3600"]["value_m"] == 3.6 and rows["3600"]["orientation"] == "horizontal" and float(rows["3600"]["len"]) == 3.6
        assert rows["900 TYP"]["value_m"] == 0.9 and rows["900 TYP"]["orientation"] == "vertical"
        src = db.one("select ref_table, ref_key from q.entity_sources where kind = 'dimension' order by ref_key limit 1")
        assert (src["ref_table"], src["ref_key"]) == ("cad_entity", "D1")


def test_scale_rung_gives_the_scale_of_a_scan(sheet):
    with DB() as db:
        f = db.one("select * from jstore.source_file where file_id = :f", f=PNG)
        page = db.one("select * from jstore.page where file_id = :f", f=PNG)
        got = D.scale_rung(db, f, page, {})
        assert got["mpp"] == pytest.approx(0.01, rel=0.01) and got["confidence"] >= 0.6
        assert "4 of 4" in got["source"]
