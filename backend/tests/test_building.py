"""Whole-building answers: a sheet of a multi-plan project is asked for the whole building by default."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from aura import building, tools
from aura.config import Settings
from aura.contract import ProjectBundle
from aura.main import create_app
from tests.conftest import FRONTEND_DRAWING

SHEETS = [("bldg-eg", "EG", 0, None), ("bldg-og1", "1. OG", 1, 10), ("bldg-og2", "2. OG", 2, 5)]


def _drawings():
    base = json.loads(FRONTEND_DRAWING.read_text())
    out = []
    for did, label, order, keep in SHEETS:
        d = copy.deepcopy(base)
        d |= {"id": did, "title": f"Haus · {label}", "project_id": "proj-bldg", "floor_label": label,
              "floor_order": order}
        if keep is not None:
            d["evidence"]["door"] = d["evidence"]["door"][:keep]
        out.append(tools.LoadedDrawing(id=did, dir=None, drawing=d, engine=tools.BundleEngine(d),
                                       gold=[], anchors=[]))
    return out


@pytest.fixture
def client(tmp_path):
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=_drawings())) as c:
        yield c


def ask(c, q, did="bldg-eg", **extra):
    r = c.post("/ask", json={"drawing_id": did, "question": q, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_default_is_the_whole_building(client):
    r = ask(client, "how many doors are there?")
    assert r["answer"] == {"kind": "count", "value": 22 + 10 + 5, "subject": "door"}
    assert r["scope"] == {"kind": "project", "project_id": "proj-bldg",
                          "drawing_ids": ["bldg-eg", "bldg-og1", "bldg-og2"]}
    assert [(b["label"], b["value"]) for b in r["breakdown"]] == [("EG", 22), ("1. OG", 10), ("2. OG", 5)]
    assert r["method"] == "cad_geometry"
    boxes = r["evidence"]["boxes"]
    assert len(boxes) == 37 and len({b["id"] for b in boxes}) == 37  # colliding ids are made unique
    assert {b["drawing_id"] for b in boxes} == {"bldg-eg", "bldg-og1", "bldg-og2"}
    scope = next(n for n in r["trace"] if n["id"] == "scope")
    assert scope["stage"] == "scope" and scope["detail"]["decider"] == "default whole building"
    assert [n["id"] for n in r["trace"] if n["id"].startswith("sheet-")] == ["sheet-0", "sheet-1", "sheet-2"]
    # every trace node's parent exists
    ids = {n["id"] for n in r["trace"]}
    assert all(n.get("parent") in ids for n in r["trace"] if n.get("parent"))


def test_this_floor_limits_to_the_active_sheet(client):
    r = ask(client, "how many doors are on this floor?", did="bldg-og1")
    assert r["answer"]["value"] == 10
    assert r["scope"]["kind"] != "project"
    assert next(n for n in r["trace"] if n["id"] == "scope")["detail"]["decider"] == "explicit words"


@pytest.mark.parametrize("q", ["how many doors on the first floor", "how many doors on level 1",
                               "combien de portes au 1er étage", "wie viele Türen im 1. OG",
                               "montako ovea 2. kerroksessa"])
def test_named_floor_in_five_languages(client, q):
    r = ask(client, q, did="bldg-eg")
    assert r["answer"]["value"] == 10, q


def test_named_floor_by_label_and_union(client):
    assert ask(client, "how many doors on EG")["answer"]["value"] == 22
    r = ask(client, "how many doors on the ground floor and the second floor")
    assert r["answer"]["value"] == 27
    assert [b["label"] for b in r["breakdown"]] == ["EG", "2. OG"]


def test_selected_sheets(client):
    r = ask(client, "how many doors are there?", sheet_ids=["bldg-eg", "bldg-og2"])
    assert r["answer"]["value"] == 27
    assert r["scope"]["drawing_ids"] == ["bldg-eg", "bldg-og2"]
    assert next(n for n in r["trace"] if n["id"] == "scope")["detail"]["decider"] == "user selection"


def test_total_area_is_the_sum_of_the_floors(client):
    whole = ask(client, "what is the total net floor area of all rooms?")
    assert whole["answer"]["kind"] == "area"
    per = [ask(client, "what is the total net floor area of all rooms on this floor?", did=i)["answer"]["value"]
           for i, *_ in SHEETS]
    assert whole["answer"]["value"] == pytest.approx(sum(per), abs=0.02)


def test_a_drawing_outside_any_project_is_unchanged(tmp_path):
    from tests.conftest import make_app

    with TestClient(make_app(tmp_path)) as c:
        r = ask(c, "how many doors are there?", did="swiss-46639")
        assert r["answer"]["value"] == 22 and "breakdown" not in r and r["scope"] == {"kind": "floor"}


def test_chat_stream_carries_the_building_answer(client):
    r = client.post("/chat", json={"drawing_id": "bldg-eg", "message": "how many doors are there?"})
    assert r.status_code == 200
    events = [ln.removeprefix("event: ") for ln in r.text.splitlines() if ln.startswith("event: ")]
    assert events[0] == "turn" and "understood" in events and events[-2:] == ["answer", "done"]


# ---- combine(): mixed methods and failures --------------------------------------------------


def _resp(value, method="cad_geometry", kind="count", **extra):
    ans = ({"kind": "count", "value": value, "subject": "door"} if kind == "count"
           else {"kind": "abstain", "reason": "not_on_drawing"})
    return {"answer": ans, "evidence": {"boxes": [{"id": "door-01", "x": 1, "y": 1, "w": 1, "h": 1,
                                                    "subject": "door"}], "rooms": []},
            "detected_lang": "en", "canonical_en": "How many doors", "scope": {"kind": "floor"},
            "method": method, "trace": [{"id": "q", "title": "Q", "tag": "t", "body": "b", "kind": "info"}],
            "calls": 1, "cost_usd": 0.01, "latency_ms": 5, "route": "template"} | extra


def _plan():
    sheets = [building.Sheet("a", "EG", 0), building.Sheet("b", "1. OG", 1), building.Sheet("c", "2. OG", 2)]
    return building.Plan("p", "House", sheets, sheets, "a", building.DECIDER_DEFAULT)


def test_combine_takes_the_weakest_method_and_sums_cost():
    p = _plan()
    out = building.combine(p, "q", [(p.sheets[0], _resp(3)), (p.sheets[1], _resp(4, "agent_estimate")),
                                    (p.sheets[2], _resp(5, "pdf_vector"))], 9)
    assert out["answer"]["value"] == 12 and out["method"] == "agent_estimate"
    assert out["calls"] == 3 and out["cost_usd"] == pytest.approx(0.03)


def test_one_failing_sheet_is_reported_not_fatal():
    p = _plan()
    out = building.combine(p, "q", [(p.sheets[0], _resp(3)), (p.sheets[1], RuntimeError("boom")),
                                    (p.sheets[2], _resp(5))], 9)
    assert out["answer"]["value"] == 8
    assert [b["status"] for b in out["breakdown"]] == ["ok", "error", "ok"]
    assert "Could not answer: 1. OG" in next(n for n in out["trace"] if n["id"] == "answer")["body"]


def test_abstains_only_when_no_sheet_can_answer_and_names_the_sheets():
    p = _plan()
    out = building.combine(p, "q", [(s, _resp(0, kind="abstain")) for s in p.sheets[:2]]
                           + [(p.sheets[2], RuntimeError("x"))], 9)
    assert out["answer"]["kind"] == "abstain" and "EG, 1. OG, 2. OG" in out["answer"]["where_to_find"]
    with pytest.raises(RuntimeError):
        building.combine(p, "q", [(s, RuntimeError("x")) for s in p.sheets], 1)


def test_plan_resolution_rules():
    proj = ProjectBundle.model_validate({"id": "p", "title": "H", "floors": [
        {"drawing_id": "a", "label": "EG", "order": 0}, {"drawing_id": "b", "label": "1. OG", "order": 1}]})
    ids = lambda pl: [s.id for s in pl.sheets]
    assert ids(building.plan_for([proj], "a", "how many doors")) == ["a", "b"]
    assert ids(building.plan_for([proj], "b", "doors on this floor")) == ["b"]
    assert ids(building.plan_for([proj], "a", "doors in the first floor")) == ["b"]
    assert ids(building.plan_for([proj], "a", "doors", scope={"kind": "region", "rect": {}})) == ["a"]
    assert ids(building.plan_for([proj], "a", "doors", sheet_ids=["b", "zzz"])) == ["b"]
    assert building.plan_for([proj], "x", "doors") is None
    assert building.plan_for([proj], "a", "how heavy is 5 kg") is not None
    assert ids(building.plan_for([proj], "a", "how heavy is 5 kg")) == ["a", "b"]
