"""Measurement questions answered from the printed dimensions of a scan (drawing.dimensions), 0 model calls."""
from fastapi.testclient import TestClient

from aura import router, tools
from tests.conftest import FRONTEND_DRAWING

SWISS = "swiss-46639"
FT = 0.3048
# what the drawing store exports for a scan: text, metres, the span and the text box in percent of the page
DIMS = [
    {"id": "dim-01", "text": "98'-0\"", "metres": round(98 * FT, 4), "p0_pct": [6.8, 74.0], "p1_pct": [92.9, 75.0],
         "box": {"x": 47.1, "y": 72.0, "w": 5.2, "h": 3.6}, "orientation": "h", "read": "ocr", "used_for_scale": True, "chain": 1},
    {"id": "dim-02", "text": "25'-5\"", "metres": round(25 * FT + 5 * 0.0254, 4), "p0_pct": [5.4, 63.0], "p1_pct": [5.5, 18.0],
         "box": {"x": 4.6, "y": 29.0, "w": 1.6, "h": 10.0}, "orientation": "v", "read": "reocr", "used_for_scale": True, "chain": 3},
    {"id": "dim-03", "text": "35'-1\"", "metres": round(35 * FT + 0.0254, 4), "p0_pct": [6.8, 16.0], "p1_pct": [37.7, 16.5],
         "box": {"x": 19.7, "y": 14.7, "w": 5.2, "h": 3.2}, "orientation": "h", "read": "ocr", "used_for_scale": True, "chain": 0},
    {"id": "dim-04", "text": "20'-11\"", "note": "Wall #1", "metres": round(20 * FT + 11 * 0.0254, 4), "p0_pct": [60.8, 25.4],
         "p1_pct": [78.3, 25.0], "box": {"x": 68.0, "y": 22.0, "w": 5.0, "h": 2.5}, "orientation": "h", "read": "gemini_ends",
         "used_for_scale": False, "chain": 5},
]


def app(tmp_path):
    from aura.config import Settings
    from aura.main import create_app

    d = tools.load_file(FRONTEND_DRAWING)
    d.drawing["dimensions"] = DIMS
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild",
                 AURA_STORE_DATA_DIR=tmp_path / "store")
    return create_app(s, extra_drawings=[d], use_gemini=False)


def ask(c, q):
    r = c.post("/ask", json={"drawing_id": SWISS, "question": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_router_sends_measurement_questions_to_the_dimensions_only_when_there_are_some():
    ctx = router.DrawingCtx(labels=["Kitchen"], has_dimensions=True)
    assert router.route("what are the overall dimensions of the building", ctx).subject == "overall"
    assert router.route("How wide is the plan?", ctx).subject == "width"
    assert router.route("what is the length of wall #1", ctx).subject == "wall:1"
    assert router.route("what is the length of the kitchen", ctx).intent != "dimension"
    plain = router.route("what are the overall dimensions of the building", router.DrawingCtx())
    assert plain.intent != "dimension"  # a drawing without dimension strings: as before


def test_length_questions_are_answered_from_printed_dimensions(tmp_path):
    with TestClient(app(tmp_path)) as c:
        overall = ask(c, "what are the overall dimensions of the building")
        wide = ask(c, "how wide is the plan")
        wall = ask(c, "what is the length of wall #1")
    for b in (overall, wide, wall):
        assert b["calls"] == 0 and b["answer"]["kind"] == "note" and b["answer"]["unit"] == "m", b["answer"]
        assert b["evidence"]["boxes"] and all(x["subject"] == "label" for x in b["evidence"]["boxes"])
    assert abs(overall["answer"]["number"] - 98 * FT) < 0.01
    assert "29.87 m × 7.75 m" in overall["answer"]["value"]
    assert {x["id"] for x in overall["evidence"]["boxes"]} == {"dim-01", "dim-02"}
    assert abs(wide["answer"]["number"] - 29.87) < 0.01
    assert abs(wall["answer"]["number"] - 6.375) < 0.01 and "Wall #1" in wall["answer"]["value"]
    assert [x["id"] for x in wall["evidence"]["boxes"]] == ["dim-04"]


def test_dimension_route_never_answers_the_size_of_a_thing_on_the_plan():
    from aura.router import DrawingCtx, route

    ctx = DrawingCtx(labels=["BEDROOM #1", "KITCHEN"], kind="raster", has_dimensions=True)
    for q in ("How wide are the doors in the plan?", "What is the total length of the walls in the building?",
              "How long is the corridor in the house?", "What is the depth of the balcony on the plan?",
              "What size are the windows on this plan?"):
        assert route(q, ctx).intent != "dimension", q
    for q, sub in (("What are the overall dimensions of the building?", "overall"),
                   ("How wide is the building?", "width"), ("What is the length of wall #1?", "wall:1")):
        r = route(q, ctx)
        assert (r.intent, r.subject) == ("dimension", sub), q


def test_german_width_of_the_building_is_a_dimension_question():
    from aura.router import DrawingCtx, route

    ctx = DrawingCtx(labels=["BEDROOM #1"], kind="raster", has_dimensions=True)
    r = route("Wie breit ist das Gebäude?", ctx)
    assert (r.intent, r.subject) == ("dimension", "width")
