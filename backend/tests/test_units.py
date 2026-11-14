"""Units: reading the requested unit, conversion, formatting, and the one pipeline hook."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from aura import pipeline
from aura import units as U
from aura.contract import AskRequest, AskResponse
from tests.conftest import make_app
from tests.test_api_chat import sse

SWISS = "swiss-46639"


@pytest.mark.parametrize("q, metric_q, unit, pure", [
    ("What is the net floor area in square feet?", "What is the net floor area in m²?", "sqft", False),
    ("area of the kitchen in sq ft", "area of the kitchen in m²", "sqft", False),
    ("how wide is the hall in feet and inches?", "how wide is the hall in m?", "ftin", False),
    ("convert that to inches", None, "in", True),
    ("and in inches?", None, "in", True),
    ("in cm", None, "cm", True),
    ("in mm please", None, "mm", True),
    ("total wall length in yards", "total wall length in m", "yd", False),
    ("area of the site in acres", "area of the site in m²", "acre", False),
    ("area of the site in hectares", "area of the site in m²", "ha", False),
    ("volume of the slabs in cubic yards", "volume of the slabs in m³", "cuyd", False),
    ("volume of the slabs in cu ft", "volume of the slabs in m³", "cuft", False),
    ("volume in litres", "volume in m³", "l", False),
    ("what's the square footage of the kitchen", "what's the area in m² of the kitchen", "sqft", False),
    ("how many square feet is the kitchen", "how many m² is the kitchen", "sqft", False),
    # de / fr / it / fi
    ("Wie groß ist die Küche in Quadratfuß?", "Wie groß ist die Küche in m²?", "sqft", False),
    ("Länge der Wände in Fuß", "Länge der Wände in m", "ft", False),
    ("und in Zoll?", None, "in", True),
    ("Quelle est la surface en pieds carrés ?", "Quelle est la surface en m² ?", "sqft", False),
    ("et en pouces ?", None, "in", True),
    ("superficie della cucina in piedi quadrati", "superficie della cucina in m²", "sqft", False),
    ("lunghezza dei muri in pollici", "lunghezza dei muri in m", "in", False),
    ("keittiön pinta-ala neliöjalkoina", "keittiön pinta-ala neliömetreinä", "sqft", False),
    ("seinien pituus tuumina", "seinien pituus metreinä", "in", False),
    # quantities to compare with: rewritten to metric, shown in the user's unit
    ("rooms larger than 200 sq ft", "rooms larger than 18.58 m²", "sqft", False),
    ("doors wider than 3 ft", "doors wider than 0.9144 m", "ft", False),
    ("is the corridor at least 1.2 m / 4 ft wide", "is the corridor at least 1.2 m / 1.219 m wide", "ft", False),
    ("walls longer than 12'6\"", "walls longer than 3.81 m", "ftin", False),
    ("doors wider than 36 in?", "doors wider than 0.9144 m?", "in", False),
])
def test_plan(q, metric_q, unit, pure):
    p = U.plan(q)
    assert p.unit is not None and p.unit.key == unit and p.pure is pure
    if metric_q is not None:
        assert p.question == metric_q


@pytest.mark.parametrize("q", [
    "What is the area of room 3.17 in m²?",  # the metric default, asked explicitly: untouched
    "How many windows are drawn in apartment A?",
    "how many rooms 3 in apartment B",
    "door to the yard",
    "which rooms are in the north wing",
    "Wie viele Türen sind in dem Plan",
    "Gehe ich zu Fuß zur Küche",
    "Quelle est la surface nette de l’appartement A en m² ?",
    "doors wider than 90 cm",
])
def test_plan_leaves_other_questions_alone(q):
    p = U.plan(q)
    assert p.unit is None and p.question == q and not p.changed and not p.pure


@pytest.mark.parametrize("v, src, dst, want", [
    (341.42, "m2", "sqft", 3675.01), (1, "m", "ft", 3.28084), (1, "m", "in", 39.3701),
    (1, "m", "yd", 1.09361), (1, "m", "mm", 1000), (1, "m", "cm", 100), (1500, "m", "km", 1.5),
    (1, "m2", "sqin", 1550.0031), (1, "m2", "sqyd", 1.19599), (4046.8564224, "m2", "acre", 1),
    (10000, "m2", "ha", 1), (1, "m3", "l", 1000), (1, "m3", "cuft", 35.3147), (1, "m3", "cuyd", 1.30795),
    (200, "sqft", "m2", 18.5806), (1, "m2", "cm2", 1e4), (1, "m2", "mm2", 1e6),
])
def test_convert(v, src, dst, want):
    assert U.convert(v, src, dst) == pytest.approx(want, rel=1e-5)


def test_convert_refuses_a_dimension_mismatch_and_coerces_families():
    with pytest.raises(ValueError):
        U.convert(1, "m", "sqft")
    assert U.coerce(U.BY_KEY["ft"], 2).key == "sqft" and U.coerce(U.BY_KEY["ftin"], 3).key == "cuft"
    assert U.coerce(U.BY_KEY["in"], 2).key == "sqin" and U.coerce(U.BY_KEY["acre"], 1) is None
    assert U.unit_for("m²").key == "m2" and U.unit_for("sq ft").key == "sqft" and U.unit_for("zz") is None


def test_formatting():
    assert U.fmt_value(U.convert(341.42, "m2", "sqft"), U.BY_KEY["sqft"]) == "3,675 sq ft"
    assert U.fmt_value(41.0105, U.BY_KEY["ft"]) == "41.01 ft"
    assert U.fmt_value(0.08431, U.BY_KEY["acre"]) == "0.0843 acres"
    assert U.fmt_value(341.42, U.BY_KEY["m2"], "de") == "341,42 m²"
    assert U.fmt_value(3675.01, U.BY_KEY["sqft"], "fr") == "3 675 sq ft"
    assert U.fmt_ft_in(12.5) == "12' 6\"" and U.fmt_ft_in(12.54, 8) == "12' 6 1/2\""
    assert U.fmt_ft_in(5.999) == "6' 0\"" and U.fmt_value(12.5, U.BY_KEY["ftin"]) == "12' 6\""


def test_prose_shows_both_values():
    t = "Sum of 12 net room areas = 341.42 m², hall 12.5 m wide, 1,234.5 m2 in total; 3 mm joints."
    assert U.convert_prose(t, U.BY_KEY["sqft"]) == (
        "Sum of 12 net room areas = 3,675 sq ft (341.42 m²), hall 41.01 ft (12.5 m) wide, "
        "13,288 sq ft (1,234.5 m2) in total; 3 mm joints.")
    assert U.convert_prose("Fläche 341,42 m².", U.BY_KEY["sqft"], "de") == "Fläche 3.675 sq ft (341,42 m²).".replace(
        "3.675", "3 675")
    assert U.convert_prose("12.5 m", U.BY_KEY["acre"]) == "12.5 m"  # acres have no length


def _resp(answer: dict, **extra) -> dict:
    return {"contract_version": "1.2", "answer": answer, "evidence": {"boxes": [], "rooms": []},
            "detected_lang": "en", "canonical_en": "q", "scope": {"kind": "floor"}, "method": "cad_geometry",
            "trace": [], "calls": 0, "cost_usd": 0.0, "latency_ms": 1, **extra}


@pytest.mark.parametrize("answer, unit, text, metric", [
    ({"kind": "area", "value": 341.42, "unit": "m2"}, "sqft", "3,675 sq ft", "341.42 m²"),
    ({"kind": "area", "value": 341.42, "unit": "m2"}, "ft", "3,675 sq ft", "341.42 m²"),  # feet for an area
    ({"kind": "quantity", "value": 120.5, "unit": "m"}, "ft", "395.3 ft", "120.50 m"),
    ({"kind": "quantity", "value": 3.81, "unit": "m"}, "ftin", "12' 6\"", "3.81 m"),
    ({"kind": "quantity", "value": 12.0, "unit": "m3"}, "cuyd", "15.7 cu yd", "12.00 m³"),
    ({"kind": "note", "value": "The hall is 12.5 m wide.", "number": 12.5, "unit": "m"}, "in", "492.1 in",
     "12.50 m"),
    ({"kind": "note", "value": "Wall 2400 mm.", "number": 2400, "unit": "mm"}, "ft", "7.87 ft", "2,400 mm"),
])
def test_apply_adds_display_and_keeps_the_metric_answer(answer, unit, text, metric):
    r = _resp(dict(answer), explanation="As measured.")
    out = U.apply(r, U.BY_KEY[unit])
    AskResponse.model_validate(out)  # contract gate
    d = out["display"]
    assert d["text"] == text and d["metric_text"] == metric
    assert {k: v for k, v in out["answer"].items() if k != "value"} == {
        k: v for k, v in answer.items() if k != "value"}
    assert out["explanation"].startswith(f"{text} ({metric})")
    assert r.get("display") is None and r["explanation"] == "As measured."  # input untouched


def test_apply_on_a_count_converts_prose_only_and_reapply_restarts_from_metric():
    r = _resp({"kind": "count", "value": 5, "subject": "room"}, explanation="5 rooms are larger than 18.58 m².")
    out = U.apply(r, U.BY_KEY["sqft"])
    assert out["explanation"] == "5 rooms are larger than 200 sq ft (18.58 m²)."
    assert "value" not in out["display"] and out["display"]["factors"]["m2"] == pytest.approx(10.7639, rel=1e-5)
    again = U.apply(out, U.BY_KEY["sqyd"])
    assert again["explanation"] == "5 rooms are larger than 22.22 sq yd (18.58 m²)."
    assert U.apply(r, None) is r


def _collect(gen):
    async def go():
        return [item async for item in gen]
    return asyncio.run(go())


class _Svc:
    writer = None


def test_hook_covers_any_route_with_the_metric_question():
    """Whatever run is wrapped (exact, tool steps, cache replay, building totals) sees a metric question."""
    seen = []

    async def base(svc, req, caller, *args, **kw):
        seen.append(req.question)
        yield "understood", {"detected_lang": "en", "canonical_en": req.question, "route": "cache"}
        yield "answer", {"response": _resp({"kind": "quantity", "value": 100.0, "unit": "m"}, route="cache")}

    run = pipeline.with_units(base)
    ev = _collect(run(_Svc(), AskRequest(drawing_id="d", question="total wall length in feet"), None))
    assert seen == ["total wall length in m"]
    assert dict(ev)["answer"]["response"]["display"]["text"] == "328.1 ft"
    ev = _collect(run(_Svc(), AskRequest(drawing_id="d", question="total wall length"), None))
    assert seen[-1] == "total wall length" and "display" not in dict(ev)["answer"]["response"]


def test_hook_reshows_the_previous_answer_for_a_pure_unit_follow_up():
    async def base(svc, req, caller, *args, **kw):
        raise AssertionError("a pure unit follow-up runs nothing")
        yield

    prev = U.apply(_resp({"kind": "quantity", "value": 100.0, "unit": "m"}, answer_id="a-1",
                         conversation_id="c-1", turn_id="t-1"), U.BY_KEY["ft"])
    run = pipeline.with_units(base)
    ev = _collect(run(_Svc(), AskRequest(drawing_id="d", question="and in inches?"), None, None,
                      [{"response": prev}]))
    body = dict(ev)["answer"]["response"]
    assert body["display"]["text"] == "3,937 in" and body["answer"]["value"] == 100.0
    assert body["answer_id"] != "a-1" and "turn_id" not in body and body["calls"] == 0
    assert body["explanation"].count("(") == 1


def test_earlier_turns_reach_the_pipeline_in_metric():
    got = {}

    async def base(svc, req, caller, context=None, history=None, *a, **kw):
        got["history"] = history
        yield "answer", {"response": _resp({"kind": "count", "value": 1, "subject": "door"})}

    shown = U.apply(_resp({"kind": "note", "value": "The hall is 12.5 m wide.", "number": 12.5, "unit": "m"},
                          explanation="Width 12.5 m."), U.BY_KEY["ft"])
    assert "41.01 ft" in shown["answer"]["value"]
    hist = [{"message": "how wide is the hall in feet?", "response": shown}]
    _collect(pipeline.with_units(base)(_Svc(), AskRequest(drawing_id="d", question="how many doors?"), None, None, hist))
    (t,) = got["history"]
    assert t["message"] == "how wide is the hall in m?" and "display" not in t["response"]
    assert t["response"]["answer"]["value"] == "The hall is 12.5 m wide." and t["response"]["explanation"] == "Width 12.5 m."
    assert "display" in hist[0]["response"]  # the stored turn is untouched


# ---- through the real app ---------------------------------------------------------------------------


def test_exact_route_cache_and_default_metric(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        ask = lambda q: c.post("/ask", json={"drawing_id": SWISS, "question": q}).json()
        m = ask("What is the net floor area of apartment A in m²?")
        assert m["answer"]["kind"] == "area" and "display" not in m
        f = ask("What is the net floor area of apartment A in square feet?")
        # the same metric answer, replayed from the cache entry of the metric question, shown in sq ft
        assert f["answer"] == m["answer"] and f["route"] == "cache"
        want = U.fmt_value(m["answer"]["value"] * 10.7639104, U.BY_KEY["sqft"])
        assert f["display"]["text"] == want and f["display"]["unit"] == "sqft"
        assert f["display"]["metric_text"] == f"{m['answer']['value']:.2f} m²"
        assert want in f["explanation"] and f["display"]["metric_text"] in f["explanation"]
        again = ask("What is the net floor area of apartment A in m²?")
        assert "display" not in again and "sq ft" not in json.dumps(again)  # the cache stayed metric
        de = ask("Wie groß ist die Nettofläche von Wohnung A in Quadratfuß?")
        assert de["answer"]["kind"] == "area" and de["display"]["unit"] == "sqft"
        assert de["detected_lang"] == "de" and " " in de["display"]["text"] or de["display"]["value"] < 1000


def test_chat_follow_up_in_another_unit(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        ev = dict(sse(c, {"drawing_id": SWISS, "message": "What is the net floor area of apartment A in square feet?"}))
        first = ev["answer"]["response"]
        assert first["display"]["unit"] == "sqft"
        conv = first["conversation_id"]
        for msg, unit in (("and in square yards?", "sqyd"), ("convert that to inches", "sqin"), ("und in cm?", "cm2")):
            nxt = dict(sse(c, {"drawing_id": SWISS, "message": msg, "conversation_id": conv}))["answer"]["response"]
            assert nxt["answer"] == first["answer"] and nxt["display"]["unit"] == unit, msg
            assert nxt["calls"] == 0 and nxt["turn_id"] != first["turn_id"]
            assert nxt["display"]["metric_text"] == first["display"]["metric_text"]


def test_building_total_is_shown_in_the_requested_unit(tmp_path):
    from aura.config import Settings
    from aura.main import create_app
    from tests import test_building as tb

    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=tb._drawings())) as c:
        m = tb.ask(c, "What is the total net floor area?")
        f = tb.ask(c, "What is the total net floor area in square feet?")
        assert f["scope"] == m["scope"] and f["answer"] == m["answer"]
        if m["answer"]["kind"] in ("area", "quantity"):
            assert f["display"]["unit"] == "sqft" and f["display"]["value"] == pytest.approx(
                m["answer"]["value"] * 10.7639104, rel=1e-6)
