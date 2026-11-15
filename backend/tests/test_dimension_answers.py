"""Plain length questions: answered from printed dimensions, else the measured outline; never a count."""
from __future__ import annotations

from aura import dimension_answers as da
from aura import steps, units
from aura.drawing_store import normalise
from aura.tools import LoadedDrawing


def _dim(i, text, value_m, orientation, line, kind="linear", unit="ft-in", boxed=True, **kw):
    x = {"id": f"dim-{i:02d}", "store_ref": 100 + i, "kind": kind, "text": text, "value_m": value_m, "unit": unit,
         "orientation": orientation, "line": line, "method": "vision", **kw}
    if boxed:
        xs, ys = [p[0] for p in line], [p[1] for p in line]
        x |= {"x": min(xs), "y": min(ys) - 1, "w": max(max(xs) - min(xs), 1), "h": max(max(ys) - min(ys), 1) + 1}
    return x


def _drawing(dims=(), rooms=(), extent=None) -> LoadedDrawing:
    doc = {"id": "st-x", "kind": "raster", "rooms": list(rooms), "units": [], "dimensions": list(dims),
           "printed_dimensions": len(dims), "evidence_method": {"room": "vision"}}
    if extent:
        doc["extent_m"] = extent
    return LoadedDrawing(id="st-x", dir=None, drawing=doc, engine=None)  # type: ignore[arg-type]


def _room(rid, name, size=None, rtype=None):
    r = {"room_id": rid, "num": "", "name": name, "points": [], "label_at": [50, 50], "area_m2": 12.0}
    if size:
        r["size"] = size
    if rtype:
        r["room_type"] = rtype
    return r


OVERALL = _dim(1, "80' - 0\"", 24.384, "horizontal", [[10, 90], [80, 90]], kind="overall")
CHAIN = [_dim(2, "12' - 6\"", 3.81, "horizontal", [[10, 10], [30, 10]]),
         _dim(3, "10' - 0\"", 3.048, "horizontal", [[30, 10], [46, 10]]),
         _dim(4, "4' - 0\"", 1.2192, "vertical", [[5, 20], [5, 28]])]


def test_overall_width_is_the_printed_overall_dimension_with_its_box():
    b = da.try_answer("What is the overall width of the building?", "en", _drawing([OVERALL, *CHAIN]))
    assert b.body["answer"] == {"kind": "quantity", "value": 24.384, "unit": "m"}
    assert "80' - 0\" as printed = 24.38 m" in b.body["explanation"]
    assert [x["id"] for x in b.body["evidence"]["boxes"]] == ["dim-01"]
    assert b.body["evidence"]["boxes"][0]["note"] == "80' - 0\""


def test_chain_of_dimensions_is_summed_and_said_so():
    b = da.try_answer("How wide is the house?", "en", _drawing(CHAIN))
    assert b.body["answer"]["kind"] == "quantity" and abs(b.body["answer"]["value"] - 6.858) < 1e-6
    assert "sum of 2 dimensions" in b.body["explanation"] and "no overall dimension is printed" in b.body["explanation"]
    assert [x["id"] for x in b.body["evidence"]["boxes"]] == ["dim-02", "dim-03"]


def test_a_short_chain_does_not_stand_for_the_plan_when_the_outline_is_measured():
    ext = {"width_m": 11.82, "depth_m": 8.78, "source": "rooms", "estimate": False, "box": {"x": 1, "y": 2, "w": 70, "h": 50}}
    b = da.try_answer("What is the overall width of the building?", "en", _drawing(CHAIN, extent=ext))
    assert b.body["answer"] == {"kind": "quantity", "value": 11.82, "unit": "m"}
    assert "traced on the scan, not printed: an estimate" in b.body["explanation"] and b.body["basis"] == "estimate"
    assert b.body["evidence"]["boxes"][0]["id"] == "extent"
    est = da.try_answer("overall size of the plan?", "en", _drawing([], extent=ext | {"estimate": True}))
    assert est.body["answer"]["kind"] == "note" and "≈ 11.82 m" in est.body["answer"]["value"]
    assert "estimate" in est.body["answer"]["value"]


def test_a_lone_dimension_is_a_lower_bound_not_the_width():
    b = da.try_answer("What is the overall width of the building?", "en", _drawing([CHAIN[0]]))
    assert b.body["answer"]["kind"] == "note" and "at least" in b.body["answer"]["value"]
    assert b.body["evidence"]["boxes"]


def test_nothing_to_measure_abstains_and_never_counts():
    b = da.try_answer("What is the overall width of the building?", "en",
                      _drawing([_dim(9, "+2.40", 2.4, None, [[1, 1], [2, 2]], kind="level")]))
    assert b.body["answer"] == {"kind": "abstain", "reason": "not_answerable"}


def test_room_size_printed_else_measured_in_any_language():
    rooms = [_room("r1", "Küche", {"length_m": 4.1, "width_m": 3.2, "source": "printed", "text": "3.20 x 4.10"}),
             _room("r2", "TUPAK", {"length_m": 8.27, "width_m": 4.24, "source": "estimate"}, rtype="kitchen_dining"),
             _room("r3", "OFFICE")]
    d = _drawing([], rooms[:1])
    b = da.try_answer("Wie breit ist die Küche?", "de", d)
    assert b.body["answer"] == {"kind": "quantity", "value": 3.2, "unit": "m"}
    assert "as printed (3.20 x 4.10)" in b.body["explanation"]
    assert b.body["evidence"]["rooms"][0]["room_id"] == "r1"
    b = da.try_answer("What are the dimensions of the kitchen?", "en", _drawing([], rooms[1:]))
    assert b.body["answer"]["value"].startswith("TUPAK: ≈ 8.27 m × 4.24 m (an estimate")
    b = da.try_answer("What are the dimensions of the office?", "en", _drawing([], rooms[1:]))
    assert b.body["answer"]["kind"] == "abstain"          # no size, no outline: not a count of offices


def test_longest_printed_dimension_carries_its_evidence():
    b = da.try_answer("What is the longest printed dimension?", "en", _drawing([OVERALL, *CHAIN]))
    assert b.body["answer"]["value"] == 24.384 and [x["id"] for x in b.body["evidence"]["boxes"]] == ["dim-01"]
    b = da.try_answer("shortest dimension printed on the sheet", "en", _drawing(CHAIN))
    assert b.body["answer"]["value"] == 1.219


def test_other_length_questions_are_left_to_the_usual_path():
    d = _drawing([OVERALL, *CHAIN], [_room("r1", "Kitchen", {"length_m": 4, "width_m": 3, "source": "measured"})])
    for q in ("how wide is the door of the kitchen?", "which rooms have a door wider than 0.9 m",
              "what is the perimeter of the kitchen?", "how many doors are there?", "what is the area of the kitchen?",
              "overall, how many windows?", "how high is the building?"):
        assert da.try_answer(q, "en", d) is None, q


def test_feet_convert_a_printed_answer_and_keep_what_is_printed():
    b = da.try_answer("What is the overall width of the building?", "en", _drawing([OVERALL]))
    out = units.apply(b.body, units.plan("how wide is the building in feet").unit)
    assert out["display"]["text"] == "80 ft" and out["answer"]["value"] == 24.384
    assert "80' - 0\" as printed" in out["explanation"] and "(24.38 m)" in out["explanation"]


def test_count_is_refused_as_the_finish_of_a_length_question():
    assert steps.asks_for_measure("What is the overall width of the building?")
    assert steps.asks_for_measure("Wie lang ist der Korridor?")
    assert not steps.asks_for_measure("how many doors are wider than 0.9 m in width?")
    assert not steps.asks_for_measure("how many rooms are there?")


def test_normalise_passes_dimensions_sizes_and_types_through():
    doc = {"rooms": [{"room_id": "a", "name": "MH", "size": {"length_m": 4.0, "width_m": 3.0, "source": "measured"}}],
           "dimensions": [{"id": "dim-01", "store_ref": 7, "kind": "linear", "text": "910", "value_m": 0.91,
                           "x": 1, "y": 1, "w": 2, "h": 1}],
           "extent_m": {"width_m": 10.0, "depth_m": 8.0}}
    model = {"items": [], "rooms": [{"room_id": "a", "entity_id": 3, "room_type": "bedroom"}]}
    out = normalise({"id": "up-1"}, doc, {}, model, {7: ["raster_detection"], 3: ["cad_entity"]})
    assert out["dimensions"][0]["method"] == "vision" and out["printed_dimensions"] == 1
    assert out["rooms"][0]["room_type"] == "bedroom" and out["rooms"][0]["size"]["width_m"] == 3.0
    assert out["extent_m"]["width_m"] == 10.0


# ---- a CAD drawing outside the store (the bundled library): sizes follow from the room outlines ----

def _bundle(rooms) -> LoadedDrawing:
    doc = {"id": "lib-x", "kind": "cad_dxf", "rooms": rooms, "units": [], "m_per_pct_x": 0.2, "m_per_pct_y": 0.4}
    return LoadedDrawing(id="lib-x", dir=None, drawing=doc, engine=None)  # type: ignore[arg-type]


def _outline(rid, name, x0, y0, x1, y1, area, outdoor=False):
    return {"room_id": rid, "num": rid, "name": name, "area_m2": area, "outdoor": outdoor,
            "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "label_at": [(x0 + x1) / 2, (y0 + y1) / 2]}


BUNDLE_ROOMS = [_outline("1.01", "Küche", 10, 10, 30, 20, 16.0),      # 4.0 m x 4.0 m
                _outline("1.02", "Reduit", 30, 10, 60, 30, 48.0),     # 6.0 m x 8.0 m
                _outline("1.03", "Balkon", 60, 10, 90, 20, 24.0, outdoor=True)]


def test_bundled_cad_drawing_answers_overall_size_from_its_outlines():
    d = _bundle(BUNDLE_ROOMS)
    b = da.try_answer("What is the overall width of the building?", "en", d)
    assert b.body["answer"] == {"kind": "quantity", "value": 10.0, "unit": "m"}   # indoor rooms: x 10..60 at 0.2 m
    assert "measured on the plan, not printed" in b.body["explanation"]
    b = da.try_answer("How long is the building?", "en", d)
    assert b.body["answer"] == {"kind": "quantity", "value": 8.0, "unit": "m"}
    assert "extent_m" not in d.drawing and "size" not in d.drawing["rooms"][0]   # the served document is untouched


def test_bundled_cad_drawing_answers_room_sizes_never_a_count():
    d = _bundle(BUNDLE_ROOMS)
    b = da.try_answer("How wide is the Reduit 1.02?", "en", d)
    assert b.body["answer"] == {"kind": "quantity", "value": 6.0, "unit": "m"}
    b = da.try_answer("What are the dimensions of the Küche?", "en", d)
    assert b.body["answer"]["kind"] == "note" and "4.00 m wide (across the page) × 4.00 m deep (up the page)" in b.body["answer"]["value"]


def test_width_of_the_largest_room_picks_the_room_by_area():
    b = da.try_answer("How wide is the largest room?", "en", _bundle(BUNDLE_ROOMS))
    assert b.body["answer"] == {"kind": "quantity", "value": 6.0, "unit": "m"}
    assert "largest room by area, Reduit 1.02 (48.00 m²)" in b.body["explanation"]


def test_longest_printed_dimension_without_dimension_lines_says_so_and_never_counts():
    area = {"id": "dim-01", "kind": "area", "text": "12.5", "value_m": None, "method": "cad_geometry"}
    for d in (_drawing([area]), _drawing([]), _bundle(BUNDLE_ROOMS)):
        b = da.try_answer("What is the longest dimension printed on this sheet?", "en", d)
        assert b.body["answer"] == {"kind": "abstain", "reason": "not_answerable"}
        assert "No dimension line" in b.body["explanation"]
    assert "printed values are room areas" in da.try_answer(
        "What is the longest dimension printed on this sheet?", "en", _drawing([area])).body["explanation"]
    assert "10.00 m × 8.00 m" in da.try_answer(
        "What is the longest dimension printed on this sheet?", "en", _bundle(BUNDLE_ROOMS)).body["explanation"]


def test_a_bare_number_in_drawing_units_is_not_quoted_as_if_it_were_metres():
    raw = _dim(9, "2471.375", 62.773, "horizontal", [[5, 5], [95, 5]], unit=None)
    b = da.try_answer("What is the longest dimension printed on this sheet?", "en", _drawing([raw]))
    assert "62.77 m (written 2471.375 in the drawing's own units)" in b.body["explanation"]
    lone = _dim(8, "864", 21.946, "vertical", [[5, 20], [5, 80]], unit=None)
    b = da.try_answer("How long is the building?", "en", _drawing([lone]))
    assert "at least that deep here" in b.body["answer"]["value"] and "wide here" not in b.body["answer"]["value"]


# ---- rooms with a label but no outline: the dimension lines printed beside the label -----------------


def _label_room(rid, name, at, area):
    return {"room_id": rid, "num": "", "name": name, "label_at": list(at), "area_m2": area, "area_source": "printed",
            "points": [[at[0] - 1, at[1] - 1], [at[0] + 1, at[1] - 1], [at[0] + 1, at[1] + 1], [at[0] - 1, at[1] + 1]]}


# 4 page percent per metre: a 5 m line is 20 percent long
SHEET = [_dim(1, "16' - 5\"", 5.0, "horizontal", [[30, 62], [50, 62]]),       # below the bedroom: its width
         _dim(2, "13' - 1 1/2\"", 4.0, "vertical", [[28, 44], [28, 60]]),      # left of it: its depth
         _dim(3, "3 1/2\"", 0.089, "horizontal", [[39, 50], [39.4, 50]]),      # a wall thickness at the label
         _dim(4, "9' - 10\"", 3.0, "horizontal", [[70, 20], [82, 20]])]        # far away


def test_room_without_outline_takes_the_dimension_lines_beside_its_label():
    d = _drawing(SHEET, [_label_room("r1", "BEDROOM", (40, 52), 20.0)])
    b = da.try_answer("What are the dimensions of the bedroom?", "en", d)
    v = b.body["answer"]["value"]
    assert "16' - 5\" as printed = 5.00 m wide (across the page)" in v
    assert "13' - 1 1/2\" as printed = 4.00 m deep (up the page)" in v
    assert "agrees with the room's printed area" in v and "Width is the side across the page" in v
    assert [m["value"] for m in b.body["measures"]] == [5.0, 4.0]
    assert {x["id"] for x in b.body["evidence"]["boxes"]} == {"dim-01", "dim-02"}
    # width is across the page even when it is the longer side: never the depth for a width question
    b = da.try_answer("How wide is the bedroom?", "en", d)
    assert b.body["answer"] == {"kind": "quantity", "value": 5.0, "unit": "m"}
    b = da.try_answer("How deep is the bedroom?", "en", d)
    assert b.body["answer"] == {"kind": "quantity", "value": 4.0, "unit": "m"}
    assert b.body["trace"][-1]["tag"] == "read from the sheet · as printed"


def test_room_without_outline_and_without_a_plausible_line_abstains_with_the_reason():
    d = _drawing(SHEET[2:], [_label_room("r1", "BEDROOM", (40, 52), 20.0)])
    b = da.try_answer("How wide is the bedroom?", "en", d)
    assert b.body["answer"]["kind"] == "abstain"
    assert "no dimension line printed beside the label is a plausible side" in b.body["explanation"]


def test_every_length_of_a_composite_answer_is_converted_from_its_value():
    d = _drawing(SHEET, [_label_room("r1", "BEDROOM", (40, 52), 20.0)])
    b = da.try_answer("What are the dimensions of the bedroom?", "en", d)
    out = units.apply(b.body, units.plan("dimensions of the bedroom in feet").unit)
    ms = out["display"]["measures"]
    assert [(m["label"], m["text"], m["printed"]) for m in ms] == [("width", "16.4 ft", "16' - 5\""),
                                                                   ("depth", "13.12 ft", "13' - 1 1/2\"")]
    assert "16.4 ft (5.00 m)" in out["answer"]["value"] and out["measures"][0]["value"] == 5.0
    from aura.contract import AskResponse
    AskResponse.model_validate(out | {"calls": 0, "cost_usd": 0, "latency_ms": 1, "source": "api"})


def test_basis_says_where_the_value_comes_from():
    from aura import answer as A
    d = _drawing(SHEET, [_label_room("r1", "BEDROOM", (40, 52), 20.0)])
    assert da.try_answer("How wide is the bedroom?", "en", d).body["basis"] == "printed"      # read by a model, still printed
    scan = _drawing([], [_room("r2", "TUPAK", {"length_m": 8.27, "width_m": 4.24, "source": "measured"}, rtype="kitchen")])
    b = da.try_answer("What are the dimensions of the kitchen?", "en", scan)
    assert b.body["basis"] == "estimate" and "traced on the scan" in b.body["answer"]["value"]
    assert da.try_answer("How wide is the Reduit 1.02?", "en", _bundle(BUNDLE_ROOMS)).body["basis"] == "measured"
    # areas: printed on an upload is printed although its rooms were found by a model; bundles carry no basis
    rooms = [{"room_id": "a", "area_source": "printed"}, {"room_id": "b", "area_source": "outline"}, {"room_id": "c"}]
    up = LoadedDrawing(id="u", dir=None, drawing={"rooms": rooms}, engine=None)  # type: ignore[arg-type]
    assert A.area_basis(up, ["a"], "vision") == {"basis": "printed"}
    assert A.area_basis(up, ["a", "b"], "vision") == {"basis": "estimate"}
    assert A.area_basis(up, ["b"], "cad_geometry") == {"basis": "measured"}
    assert A.area_basis(up, ["c"], "cad_geometry") == {} and A.area_basis(up, [], "vision") == {}


def test_a_pdf_sheet_whose_rooms_were_traced_on_its_page_image_is_not_called_a_scan():
    sheet = _drawing([], [_room("r2", "BEDROOM", {"length_m": 5.21, "width_m": 3.84, "source": "measured"}, rtype="bedroom")])
    sheet.drawing["source_ext"] = "pdf"
    b = da.try_answer("What are the dimensions of the bedroom?", "en", sheet)
    assert "traced on the sheet image" in b.body["answer"]["value"] and "scan" not in b.body["answer"]["value"]
    assert b.body["basis"] == "estimate"


def test_room_words_match_the_abbreviations_a_sheet_prints_without_reinterpretation():
    from aura import room_words as rw
    for label, kind in [("MEZZ.", "mezzanine"), ("BR", "bedroom"), ("BDRM 2", "bedroom"), ("BATH", "bathroom"),
                        ("BA", "bathroom"), ("KIT.", "kitchen"), ("LR", "living_room"), ("LIV.", "living_room"),
                        ("DR", "dining_room"), ("DIN.", "dining_room"), ("MECH.", "technical"), ("STOR.", "storage"),
                        ("CL", "storage"), ("CLO.", "storage"), ("CORR.", "corridor"), ("ELEV.", "elevator"),
                        ("UTIL.", "technical"), ("OFF.", "office"), ("STAIR", "staircase")]:
        assert rw.label_type(label) == kind, label
    assert rw.label_type("ROOM UNDER MEZZ.") is None and rw.label_type("CLINIC") is None
    size = {"length_m": 6.0, "width_m": 3.0, "source": "measured"}
    rooms = [_room("r1", "STAIR", size, rtype="staircase"), _room("r2", "MEZZ.", size), _room("r3", "ELEV.", size),
             _room("r4", "Mech. Room", size, rtype="room")]
    d = _drawing([], rooms)
    for q, rid in [("How wide is the stair?", "r1"), ("How wide is the mezzanine?", "r2"),
                   ("How wide is the elevator?", "r3"), ("Wie breit ist die Treppe?", "r1"),
                   ("Quanto è larga la scala?", "r1"), ("How wide is the mechanical room?", "r4")]:
        b = da.try_answer(q, "en", d)
        assert b is not None and b.body["answer"] == {"kind": "quantity", "value": 3.0, "unit": "m"}, q
        assert [r["room_id"] for r in b.body["evidence"]["rooms"]] == [rid], q
    assert da.classify("How wide is the stair door?", d) is None      # a door, not the room
    assert da.classify("How wide are the stairs?", _drawing([], rooms[1:])) is None   # no stair room on this sheet
