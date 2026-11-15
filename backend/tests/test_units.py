"""Tests for natural-language unit conversion (aura.units + /ask + /chat + whole-building)."""

from __future__ import annotations

import pytest

from aura.units import (
    extract_target_unit,
    try_followup_conversion,
    try_standalone_conversion,
)


def test_standalone_area_m2_to_sqft():
    built = try_standalone_conversion("convert 12 m² to sq ft")
    assert built is not None
    ans = built.body["answer"]
    assert ans["kind"] == "note"
    assert ans["number"] == pytest.approx(129.17, abs=0.01)
    assert ans["unit"] == "sq ft"
    assert "129.17 sq ft" in ans["value"]


@pytest.mark.parametrize("q,expected_num,expected_unit", [
    ("12 m2 in square feet", 129.17, "sq ft"),
    ("how many sq ft are in 12 m²?", 129.17, "sq ft"),
    ("convert 250 sq ft", 23.23, "m²"),             # default: metric!
    ("250 sq ft in metric", 23.23, "m²"),           # explicit metric
    ("convert 36 inches", 0.91, "m"),               # default: metric!
    ("14' 6\" in m", 4.42, "m"),                    # compound feet + inches -> metric
    ("convert 14 ft 6 in to cm", 441.96, "cm"),     # compound feet + inches -> cm
    ("convert 4.42 m to feet and inches", 14.5, "ft"),
    ("2.5 m to in", 98.43, "in"),
    ("100 cu ft to m3", 2.83, "m³"),
])
def test_standalone_conversions_matrix(q: str, expected_num: float, expected_unit: str):
    built = try_standalone_conversion(q)
    assert built is not None, q
    ans = built.body["answer"]
    assert ans["number"] == pytest.approx(expected_num, abs=0.02), (q, ans)
    assert ans["unit"] == expected_unit, (q, ans)


def test_incompatible_dimensions_abstain_cleanly():
    built = try_standalone_conversion("convert 12 m² to inches")
    assert built is not None
    assert built.body["answer"]["kind"] == "abstain"
    assert built.body["answer"]["reason"] == "unclear_question"


def test_drawing_queries_never_hijacked_as_standalone():
    for q in (
        "What is the area of apartment A in sq ft?",
        "How many doors are in apartment A?",
        "Which rooms are larger than 200 sq ft?",
        "How many square feet is room 3.04?",
        "What is the price at 12 $/m²?",
    ):
        assert try_standalone_conversion(q) is None, q


def test_extract_target_unit_preserves_metric_default_and_thresholds():
    # Default metric: None
    u, _ = extract_target_unit("What is the net floor area of apartment A?")
    assert u is None
    u, _ = extract_target_unit("What is the net floor area of apartment A in m²?")
    assert u is None
    # Preposition 'in' before 'apartment A' is NOT inches!
    u, _ = extract_target_unit("How many doors are in apartment A?")
    assert u is None
    # Threshold is NOT output unit!
    u, _ = extract_target_unit("Which rooms are larger than 200 sq ft?")
    assert u is None

    # Non-metric target units extracted cleanly
    u, norm = extract_target_unit("What is the net floor area of apartment A in sq ft?")
    assert u is not None and u.canonical == "sq ft"
    assert norm == "What is the net floor area of apartment A?"

    u, norm = extract_target_unit("How many square feet is room 3.04?")
    assert u is not None and u.canonical == "sq ft"
    assert "3.04" in norm


def test_followup_conversion_preserves_evidence():
    prev = {
        "response": {
            "answer": {"kind": "area", "value": 12.0, "unit": "m2"},
            "scope": {"kind": "room", "room_id": "3.04"},
            "method": "cad_geometry",
            "evidence": {
                "boxes": [],
                "rooms": [{"room_id": "3.04", "included": True, "area_m2": 12.0}],
            },
        }
    }
    fu = try_followup_conversion("in sq ft?", "en", [prev])
    assert fu is not None
    assert fu.body["answer"]["number"] == pytest.approx(129.17, abs=0.01)
    assert fu.body["answer"]["unit"] == "sq ft"
    assert fu.body["evidence"]["rooms"] == [{"room_id": "3.04", "included": True, "area_m2": 12.0}]
    assert fu.body["scope"] == {"kind": "room", "room_id": "3.04"}

    # Follow-up back to metric
    prev2 = {"response": fu.body}
    fu_metric = try_followup_conversion("and in metric?", "en", [prev2])
    assert fu_metric is not None
    assert fu_metric.body["answer"]["number"] == pytest.approx(12.0, abs=0.02)
    assert fu_metric.body["answer"]["unit"] == "m²"


def test_ask_endpoint_standalone_and_room_area_conversions(client):
    drawings = client.get("/drawings").json()["drawings"]
    did = drawings[0]["id"]

    # 1. Standalone "convert 12 m² to sq ft" via /ask
    r1 = client.post("/ask", json={"drawing_id": did, "question": "convert 12 m² to sq ft"})
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["answer"]["kind"] == "note"
    assert b1["answer"]["number"] == pytest.approx(129.17, abs=0.01)
    assert b1["answer"]["unit"] == "sq ft"
    assert b1["calls"] == 0

    # 2. Default metric floor area vs converted sq ft floor area on the real fixture drawing
    r_m = client.post("/ask", json={"drawing_id": did, "question": "What is the net floor area of apartment A?"})
    assert r_m.status_code == 200
    bm = r_m.json()
    assert bm["answer"]["kind"] == "area"
    assert bm["answer"]["unit"] == "m2"
    m2_val = bm["answer"]["value"]
    inc_rooms_m = [rm["room_id"] for rm in bm["evidence"]["rooms"] if rm["included"]]
    assert len(inc_rooms_m) > 0

    r_ft = client.post("/ask", json={"drawing_id": did, "question": "What is the net floor area of apartment A in sq ft?"})
    assert r_ft.status_code == 200
    bft = r_ft.json()
    assert bft["answer"]["kind"] == "note"
    assert bft["answer"]["unit"] == "sq ft"
    assert bft["answer"]["number"] == pytest.approx(round(m2_val / (0.3048 ** 2), 2), abs=0.02)
    inc_rooms_ft = [rm["room_id"] for rm in bft["evidence"]["rooms"] if rm["included"]]
    assert inc_rooms_ft == inc_rooms_m

    # 3. Whole-building ("All x Floors") project area conversion + standalone conversion under project scope
    drawings_2 = drawings[:2] if len(drawings) >= 2 else [drawings[0], drawings[0]]
    if len({d["id"] for d in drawings_2}) >= 2:
        p_res = client.post(
            "/projects",
            json={
                "id": "proj-unit-test",
                "title": "Unit Test Building",
                "floors": [
                    {"drawing_id": drawings_2[0]["id"], "label": "EG", "order": 0},
                    {"drawing_id": drawings_2[1]["id"], "label": "1. OG", "order": 1},
                ],
            },
        )
        assert p_res.status_code == 200
        tok = p_res.json()["token"]
        headers = {"x-project-token": tok}

        # Standalone conversion with project_id set does NOT duplicate across floors
        rs = client.post(
            "/ask",
            headers=headers,
            json={
                "drawing_id": drawings_2[0]["id"],
                "project_id": "proj-unit-test",
                "question": "convert 12 m² to sq ft",
            },
        )
        assert rs.status_code == 200
        assert rs.json()["answer"]["number"] == pytest.approx(129.17, abs=0.01)
        assert rs.json()["answer"]["unit"] == "sq ft"

        # Whole-building total area in sq ft sums both floors and converts breakdown items
        ra_m = client.post(
            "/ask",
            headers=headers,
            json={
                "drawing_id": drawings_2[0]["id"],
                "project_id": "proj-unit-test",
                "question": "What is the net floor area?",
            },
        ).json()
        ra_ft = client.post(
            "/ask",
            headers=headers,
            json={
                "drawing_id": drawings_2[0]["id"],
                "project_id": "proj-unit-test",
                "question": "What is the net floor area in sq ft?",
            },
        ).json()
        assert ra_ft["answer"]["kind"] == "note"
        assert ra_ft["answer"]["unit"] == "sq ft"
        assert ra_ft["answer"]["number"] == pytest.approx(
            round(ra_m["answer"]["value"] / (0.3048 ** 2), 2), abs=0.05
        )
        assert all(b["unit"] == "sq ft" for b in ra_ft["breakdown"] if b["status"] == "ok")

