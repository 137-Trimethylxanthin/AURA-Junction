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


# ---- S1: ownership, deduplication, sheet cap, readiness & shared call budget ----------------


def _standalone_drawings(specs: list[tuple[str, str, int | None]]):
    base = json.loads(FRONTEND_DRAWING.read_text())
    out = []
    for did, title, keep in specs:
        d = copy.deepcopy(base)
        d.pop("project_id", None)
        d.pop("floor_label", None)
        d.pop("floor_order", None)
        d |= {"id": did, "title": title}
        if keep is not None:
            d["evidence"]["door"] = d["evidence"]["door"][:keep]
        out.append(tools.LoadedDrawing(id=did, dir=None, drawing=d, engine=tools.BundleEngine(d),
                                       gold=[], anchors=[]))
    return out


def test_another_users_saved_bundle_never_widens_answers_or_steals_server_bundles(tmp_path):
    """A saved bundle applies ONLY when the caller names its `project_id` AND holds its token."""
    extras = _drawings() + _standalone_drawings([("solo-a", "Solo A", 7), ("solo-b", "Solo B", 9)])
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=extras)) as c:
        # User A bundles standalone drawings solo-a (7 doors) + solo-b (9 doors)
        made = c.post("/projects", json={"id": "proj-user-a", "title": "A's Bundle",
                                         "drawing_ids": ["solo-a", "solo-b"]})
        assert made.status_code == 200
        tok_a = made.json()["token"]

        # User B asks on solo-a with no project_id -> gets 7, never 16
        r_anon = ask(c, "how many doors are there?", did="solo-a")
        assert r_anon["answer"]["value"] == 7 and "breakdown" not in r_anon
        assert r_anon["scope"] == {"kind": "floor"}

        # User B names User A's project_id without User A's token -> still 7, never 16
        r_guess = c.post("/ask", json={"drawing_id": "solo-a", "project_id": "proj-user-a",
                                       "question": "how many doors are there?"}).json()
        assert r_guess["answer"]["value"] == 7 and "breakdown" not in r_guess

        # Even User A asking on solo-a WITHOUT naming project_id gets 7 (saved bundles are never implicit)
        r_owner_implicit = c.post("/ask", json={"drawing_id": "solo-a", "question": "how many doors are there?"},
                                  headers={"x-project-token": tok_a}).json()
        assert r_owner_implicit["answer"]["value"] == 7 and "breakdown" not in r_owner_implicit

        # User A naming project_id WITH x-project-token gets the combined 7 + 9 = 16
        r_owner = c.post("/ask", json={"drawing_id": "solo-a", "project_id": "proj-user-a",
                                       "question": "how many doors are there?"},
                         headers={"x-project-token": tok_a}).json()
        assert r_owner["answer"]["value"] == 16
        assert [(b["drawing_id"], b["value"]) for b in r_owner["breakdown"]] == [("solo-a", 7), ("solo-b", 9)]

        # User A saving a custom bundle that grabs one floor of server-built proj-bldg does NOT break
        # proj-bldg for everyone else, nor expose User A's bundle in anonymous GET /projects.
        grab = c.post("/projects", json={"id": "proj-grab", "title": "Grab",
                                         "drawing_ids": ["bldg-eg", "solo-a"]})
        assert grab.status_code == 200
        public_ids = [p["id"] for p in c.get("/projects").json()["projects"]]
        assert "proj-bldg" in public_ids
        assert "proj-user-a" not in public_ids and "proj-grab" not in public_ids
        still_server = ask(c, "how many doors are there?", did="bldg-eg")
        assert still_server["answer"]["value"] == 37


def test_duplicate_floors_are_deduplicated_in_post_projects_and_plan_for(tmp_path):
    extras = _standalone_drawings([("dup-a", "EG", 8), ("dup-b", "1. OG", 5)])
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=extras)) as c:
        # Only one distinct floor -> refused with 400
        assert c.post("/projects", json={"title": "Same", "drawing_ids": ["dup-a", "dup-a"]}).status_code == 400
        assert c.post("/projects", json={"title": "SameF", "floors": [
            {"drawing_id": "dup-a", "label": "EG", "order": 0},
            {"drawing_id": "dup-a", "label": "Copy", "order": 1},
        ]}).status_code == 400

        # Duplicate floor alongside a second floor -> deduplicated to 2 floors, counted once
        created = c.post("/projects", json={
            "id": "proj-dedup",
            "title": "Dedup",
            "drawing_ids": ["dup-a", "dup-a", "dup-b", "dup-a"],
        })
        assert created.status_code == 200
        body = created.json()
        assert [f["drawing_id"] for f in body["floors"]] == ["dup-a", "dup-b"]
        ans = c.post("/ask", json={"drawing_id": "dup-a", "project_id": "proj-dedup",
                                   "question": "how many doors are there?"},
                     headers={"x-project-token": body["token"]}).json()
        assert ans["answer"]["value"] == 8 + 5

    # Direct unit check on plan_for with duplicate ProjectFloor entries
    dup_bundle = ProjectBundle.model_validate({
        "id": "p-dup", "title": "Dup",
        "floors": [
            {"drawing_id": "a", "label": "EG", "order": 0},
            {"drawing_id": "a", "label": "EG again", "order": 2},
            {"drawing_id": "b", "label": "1. OG", "order": 1},
        ],
    })
    pl = building.plan_for([dup_bundle], "a", "how many doors")
    assert pl is not None and [sh.id for sh in pl.sheets] == ["a", "b"]
    only_one = ProjectBundle.model_validate({
        "id": "p-one", "title": "One",
        "floors": [
            {"drawing_id": "a", "label": "EG", "order": 0},
            {"drawing_id": "a", "label": "EG2", "order": 1},
        ],
    })
    assert building.plan_for([only_one], "a", "how many doors") is None


def test_over_cap_sheets_refused_with_clear_message_while_subset_succeeds(tmp_path):
    base = json.loads(FRONTEND_DRAWING.read_text())
    tower = []
    for i in range(13):
        d = copy.deepcopy(base)
        did = f"twr-{i:02d}"
        d |= {"id": did, "title": f"Tower · L{i}", "project_id": "proj-tower",
              "floor_label": f"Level {i}", "floor_order": i}
        d["evidence"]["door"] = d["evidence"]["door"][:2]
        tower.append(tools.LoadedDrawing(id=did, dir=None, drawing=d, engine=tools.BundleEngine(d),
                                         gold=[], anchors=[]))
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=tower)) as c:
        over = c.post("/ask", json={"drawing_id": "twr-00", "question": "how many doors are there?"})
        assert over.status_code == 400
        err = over.json()
        assert err["code"] == "bad_request" and "13" in err["error"] and "12" in err["error"]

        chat_over = c.post("/chat", json={"drawing_id": "twr-00", "message": "how many doors are there?"})
        assert chat_over.status_code == 400 and "12" in chat_over.json()["error"]

        # Asking about a single floor or <= 12 selected sheets on the same building succeeds
        one_floor = ask(c, "how many doors on this floor?", did="twr-00")
        assert one_floor["answer"]["value"] == 2
        twelve = ask(c, "how many doors are there?", did="twr-00",
                     sheet_ids=[f"twr-{i:02d}" for i in range(12)])
        assert twelve["answer"]["value"] == 24


def test_missing_and_unready_sheets_are_checked_and_reported_in_breakdown(tmp_path):
    extras = _standalone_drawings([("sh-eg", "EG", 10), ("sh-og1", "1. OG", 6), ("sh-og2", "2. OG", 4)])
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=extras)) as c:
        created = c.post("/projects", json={
            "id": "proj-check",
            "title": "Check House",
            "floors": [
                {"drawing_id": "sh-eg", "label": "EG", "order": 0},
                {"drawing_id": "sh-og1", "label": "1. OG", "order": 1},
                {"drawing_id": "sh-og2", "label": "2. OG", "order": 2},
            ],
        }).json()
        headers = {"x-project-token": created["token"]}

        # Mark sh-og2 not ready, and remove sh-og1 from the registry (simulating deleted/missing sheet)
        svc = c.app.state.svc
        og2 = svc.registry.get("sh-og2")
        assert og2 is not None
        og2.drawing["status"] = "processing"
        svc.registry.evict("sh-og1")

        r = c.post("/ask", json={"drawing_id": "sh-eg", "project_id": "proj-check",
                                 "question": "how many doors are there?"}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["answer"] == {"kind": "count", "value": 10, "subject": "door"}
        assert body["breakdown"] == [
            {"drawing_id": "sh-eg", "label": "EG", "status": "ok", "value": 10, "unit": "door"},
            {"drawing_id": "sh-og1", "label": "1. OG", "status": "error"},
            {"drawing_id": "sh-og2", "label": "2. OG", "status": "error"},
        ]
        t_by_id = {n["id"]: n for n in body["trace"]}
        assert "missing" in t_by_id["sheet-1"]["body"]
        assert "not ready" in t_by_id["sheet-2"]["body"]

        # Asking specifically for the missing floor (1. OG) while on EG abstains with breakdown
        only_missing = c.post("/ask", json={"drawing_id": "sh-eg", "project_id": "proj-check",
                                            "question": "how many doors on 1. OG?"}, headers=headers)
        assert only_missing.status_code == 200
        om = only_missing.json()
        assert om["answer"]["kind"] == "abstain"
        assert om["breakdown"] == [{"drawing_id": "sh-og1", "label": "1. OG", "status": "error"}]


def test_shared_call_budget_caps_total_calls_across_sheets_and_isolates_sheet_meters():
    import asyncio
    from dataclasses import dataclass
    from types import SimpleNamespace

    from aura.budget import BudgetError
    from aura.contract import AskRequest
    from aura.gemini.client import Meter

    class DummyClient:
        def __init__(self):
            self.sent = 0

        async def generate(self, *args, meter=None, **kwargs):
            self.sent += 1
            if meter is not None:
                meter.add(SimpleNamespace(cost_usd=0.01, input_tokens=10, output_tokens=5))
            return SimpleNamespace(text="ok")

        async def decisions(self, *args, meter=None, **kwargs):
            return await self.generate(*args, meter=meter, **kwargs)

    @dataclass
    class FakeSvc:
        client: object

    client = DummyClient()
    svc = FakeSvc(client=client)
    p = _plan()  # 3 sheets

    async def fake_base_run(s, req, caller, context, history, meters, deadline_s, agent_deadline_s):
        # Each sheet tries 3 model calls (9 total across 3 sheets), catching BudgetError like steps.py
        for _ in range(3):
            try:
                await s.client.generate("hi", meter=meters["answer"])
            except BudgetError:
                break
        yield "answer", {"response": _resp(2, calls=meters["answer"].calls,
                                           cost_usd=round(meters["answer"].cost_usd, 8))}

    outer = {"answer": Meter(), "check": Meter()}

    async def go():
        out = None
        async for ev, data in building.run(
            svc, AskRequest(drawing_id="a", question="q"), None, meters=outer,
            plan=p, base_run=fake_base_run, max_calls=5,
        ):
            if ev == "answer":
                out = data["response"]
        return out

    resp = asyncio.run(go())
    assert client.sent == 5
    # Per-sheet meters were isolated: sum of per-sheet calls equals actual sent calls (5, not double-counted)
    assert resp["calls"] == 5 and outer["answer"].calls == 5
    assert resp["cost_usd"] == pytest.approx(0.05) and outer["answer"].cost_usd == pytest.approx(0.05)
