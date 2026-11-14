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
    assert "measured on the plan, not printed" in b.body["explanation"]
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
