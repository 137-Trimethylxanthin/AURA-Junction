"""Unit conversion in natural language: exact factors, metric default, 0 model calls."""
from __future__ import annotations

import pytest

from aura import units
from aura.contract import AskResponse


@pytest.mark.parametrize("q, value, target", [
    ("convert 12 m² to sq ft", 129.1669, "ft2"),
    ("12 m2 in square feet?", 129.1669, "ft2"),
    ("What is 100 sq ft in m2?", 9.290304, "m2"),
    ("how many feet is 3 m", 9.84252, "ft"),
    ("wie viel sind 5 m in Fuß", 16.4042, "ft"),
    ("10 ft to m", 3.048, "m"),
    ("how many inches are in 2.5 m?", 98.4252, "in"),
    ("convert 5 acres to hectares", 2.023428, "ha"),
    ("what is 3,40 m in cm", 340.0, "cm"),
    ("convert 1,200 sqft to m²", 111.4836, "m2"),
    ("12'6\" in meters", 3.81, "m"),
    ("Combien font 20 m² en pieds carrés ?", 215.278, "ft2"),
])
def test_pure_conversions(q, value, target):
    c = units.parse_conversion(q)
    assert c is not None, q
    assert c.target == target
    assert c.result == pytest.approx(value, rel=1e-4)


@pytest.mark.parametrize("q", [
    "how big is the kitchen", "How many rooms are in the plan?", "area of room 3.04 in m2",
    "which rooms are larger than 12 m²?", "convert 12 m² to kg", "12 m² to m²",
])
def test_drawing_questions_are_not_conversions(q):
    assert units.parse_conversion(q) is None


@pytest.mark.parametrize("q, unit, cleaned", [
    ("What is the area of the kitchen in square feet?", "ft2", "What is the area of the kitchen?"),
    ("total area in sq ft", "ft2", "total area"),
    ("How big is the living room (in imperial)?", "imperial", "How big is the living room?"),
    ("Fläche der Küche in Quadratfuß?", "ft2", "Fläche der Küche?"),
    ("how many rooms are there in the plan?", None, "how many rooms are there in the plan?"),
    ("How many rooms are larger than 200 in square feet?", None, "How many rooms are larger than 200 in square feet?"),
    ("How many doors in MI?", None, "How many doors in MI?"),
    ("What is the net floor area of the room 'LIFT (113)' on '1.korrus', in m²?", "m2",
     "What is the net floor area of the room 'LIFT (113)' on '1.korrus', in m²?"),
])
def test_requested_unit(q, unit, cleaned):
    assert units.requested_unit(q) == (unit, cleaned)


def test_target_for_lifts_lengths_to_areas():
    assert units.target_for("ft", "area") == "ft2"
    assert units.target_for("imperial", "length") == "ft"
    assert units.target_for("ft2", "length") is None


def test_api_convert_is_answered_by_code(client):
    r = client.post("/ask", json={"drawing_id": "swiss-46639", "question": "convert 12 m² to sq ft"})
    assert r.status_code == 200, r.text
    body = r.json()
    AskResponse.model_validate(body)
    assert body["answer"]["kind"] == "note"
    assert body["answer"]["number"] == pytest.approx(129.1669, rel=1e-4)
    assert body["answer"]["unit"] == "sq ft"
    assert body["calls"] == 0


def test_api_area_in_square_feet_carries_converted(client):
    plain = client.post("/ask", json={"drawing_id": "swiss-46639",
                                      "question": "What is the total area of apartment A?"}).json()
    asked = client.post("/ask", json={"drawing_id": "swiss-46639",
                                      "question": "What is the total area of apartment A in square feet?"}).json()
    AskResponse.model_validate(asked)
    assert plain["answer"]["kind"] == "area" and asked["answer"] == plain["answer"]
    assert asked["converted"]["unit"] == "sq ft"
    assert asked["converted"]["value"] == pytest.approx(plain["answer"]["value"] / 0.09290304, rel=1e-6)
    assert "converted" not in plain


def test_numbered_room_keeps_its_number_but_thresholds_keep_their_unit():
    assert units.requested_unit("What is the area of bedroom 2 in square feet?") == (
        "ft2", "What is the area of bedroom 2?")
    assert units.requested_unit("rooms over 20 in sq ft")[0] is None
