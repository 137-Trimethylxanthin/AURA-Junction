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


def test_combine_does_not_mutate_cached_per_sheet_responses():
    full = _plan()
    p = building.Plan("p", "House", full.sheets[:2], full.sheets[:2], "a", building.DECIDER_DEFAULT)
    r_a = _resp(1, evidence={"boxes": [{"id": "b1", "subject": "door", "x": 1, "y": 2, "w": 3, "h": 4}], "rooms": []})
    r_b = _resp(1, evidence={"boxes": [{"id": "b1", "subject": "door", "x": 5, "y": 6, "w": 3, "h": 4}], "rooms": []})
    snap_b = copy.deepcopy(r_b)

    out = building.combine(p, "how many doors", [(p.sheets[0], r_a), (p.sheets[1], r_b)], 10)
    assert [b["id"] for b in out["evidence"]["boxes"]] == ["b1", "b:b1"]
    assert r_b == snap_b  # cached per-sheet dict in AnswerCache must stay untouched


def test_deleting_upload_purges_combined_answers_caches_turns_and_sessions(tmp_path):
    from aura.cache import answer_key
    from tests.test_agent_client import parse_sse_raw
    from tests.test_uploads import plain_dxf, wait_status

    base = json.loads(FRONTEND_DRAWING.read_text())
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s)) as c:
        up1 = c.post("/uploads", files={"file": ("eg.dxf", plain_dxf(), "application/dxf")}).json()
        up2 = c.post("/uploads", files={"file": ("og1.dxf", plain_dxf(), "application/dxf")}).json()
        u1, tok1 = up1["drawing_id"], up1["delete_token"]
        u2, tok2 = up2["drawing_id"], up2["delete_token"]
        assert wait_status(c, u1)["status"] == "ready"
        assert wait_status(c, u2)["status"] == "ready"

        # Give u1 (3 doors) and u2 (5 uniquely tagged doors) distinct door boxes on disk so items are unmistakable
        svc = c.app.state.svc
        for uid, lbl, n_doors, prefix in [(u1, "EG", 3, "u1-door"), (u2, "1. OG", 5, "u2-door")]:
            dp = s.uploads_dir / uid / "drawing.json"
            d_json = copy.deepcopy(base)
            d_json |= {"id": uid, "title": f"House · {lbl}", "source": "upload", "floor_label": lbl}
            doors = []
            for idx in range(n_doors):
                box = dict(base["evidence"]["door"][idx])
                box["id"] = f"{prefix}-{idx}"
                doors.append(box)
            d_json["evidence"]["door"] = doors
            dp.write_text(json.dumps(d_json))
            svc.registry.evict(uid)

        proj = c.post(
            "/projects",
            json={"id": "proj-s2", "title": "S2 House", "drawing_ids": [u1, u2]},
            headers={"x-delete-token": f"{tok1},{tok2}"},
        )
        assert proj.status_code == 200, proj.text
        owner_headers = {"x-project-token": proj.json()["token"]}

        # 1. Single-floor answer on u1 only (must survive deleting u2)
        ans_u1 = c.post(
            "/ask",
            json={"drawing_id": u1, "project_id": "proj-s2", "question": "how many doors on this floor?"},
            headers=owner_headers,
        ).json()
        assert ans_u1["answer"]["value"] == 3
        u1_ans_id = ans_u1["answer_id"]

        # 2. Combined whole-building answer asked from u1 across [u1, u2]
        ans_both = c.post(
            "/ask",
            json={"drawing_id": u1, "project_id": "proj-s2", "question": "how many doors are there?"},
            headers=owner_headers,
        ).json()
        assert ans_both["answer"]["value"] == 8
        both_ans_id = ans_both["answer_id"]
        assert any(b["id"].startswith("u2-door-") and b["drawing_id"] == u2 for b in ans_both["evidence"]["boxes"])

        # Verify sheet_ids column persisted [u1, u2] and evidence CSV works before delete
        c.portal.call(svc.writer.flush)
        saved_row = svc.store._q("SELECT drawing_id, sheet_ids FROM answers WHERE id=?", (both_ans_id,))[0]
        assert saved_row["drawing_id"] == u1
        assert json.loads(saved_row["sheet_ids"]) == [u1, u2]
        csv_before = c.get(f"/answers/{both_ans_id}/evidence.csv")
        assert csv_before.status_code == 200 and "u2-door-0" in csv_before.text

        # 3. Seed a project-scoped AnswerCache entry under u1 (both RAM & SQLite) containing u2's items
        proj_scope = {"kind": "project", "project_id": "proj-s2", "drawing_ids": [u1, u2]}
        ckey = answer_key(u1, "how many doors are there?", proj_scope, None, None, True)
        svc.answers.put(ckey, ans_both, drawing_id=u1, question="how many doors are there?")
        assert svc.answers.get(ckey) is not None

        # 4. Two-turn /chat conversation started on u1: turn 1 is u1-only, turn 2 is combined [u1, u2]
        evs1, _ = parse_sse_raw(
            c.post(
                "/chat",
                json={"drawing_id": u1, "project_id": "proj-s2", "message": "how many doors on this floor?"},
                headers=owner_headers,
            ).text
        )
        conv_id = dict(evs1)["turn"]["conversation_id"]
        evs2, _ = parse_sse_raw(
            c.post(
                "/chat",
                json={
                    "drawing_id": u1,
                    "project_id": "proj-s2",
                    "conversation_id": conv_id,
                    "message": "how many doors across all floors?",
                },
                headers=owner_headers,
            ).text
        )
        assert dict(evs2)["answer"]["response"]["answer"]["value"] == 8

        # 5. Browser session mirror on u1 with both a u1-only turn and the combined [u1, u2] turn
        client_hdr = {"X-Aura-Client": "a" * 32}
        sess_res = c.post(
            "/sessions",
            json={
                "id": "sess-s2",
                "drawing_id": u1,
                "project_id": "proj-s2",
                "title": "Doors check",
                "turns": [
                    {"id": "t1", "question": "this floor?", "response": ans_u1},
                    {"id": "t2", "question": "all floors?", "response": ans_both},
                ],
            },
            headers=client_hdr,
        )
        assert sess_res.status_code in (200, 201), sess_res.text

        # 6. Delete upload u2!
        deleted = c.delete(f"/drawings/{u2}", headers={"x-delete-token": tok2})
        assert deleted.status_code == 204

        # Combined answer is gone (404 on CSV); u1-only answer survives (200 on CSV)
        assert c.get(f"/answers/{both_ans_id}/evidence.csv").status_code == 404
        assert c.get(f"/answers/{u1_ans_id}/evidence.csv").status_code == 200

        # No row in answers, answer_cache, turns, check_cache, or chat_sessions still contains u2 or u2-door-*
        for table, col in [
            ("answers", "response"),
            ("answer_cache", "response"),
            ("turns", "response"),
            ("chat_sessions", "body"),
        ]:
            rows = svc.store._q(f"SELECT {col} AS payload FROM {table} WHERE {col} IS NOT NULL")
            for row in rows:
                payload = row["payload"]
                assert u2 not in payload, f"{table} leaked deleted sheet {u2}"
                assert "u2-door-" not in payload, f"{table} leaked items from deleted sheet {u2}"

        # In-memory AnswerCache has neither ckey nor any entry referencing u2
        assert svc.answers.get(ckey) is None
        for k, v in svc.answers._d.items():
            blob = json.dumps([k, v])
            assert u2 not in blob and "u2-door-" not in blob

        # Conversation conv_id kept turn 1 (u1-only) and dropped turn 2 ([u1, u2])
        conv = c.get(f"/conversations/{conv_id}").json()
        assert len(conv["turns"]) == 1
        assert conv["turns"][0]["response"]["answer"]["value"] == 3

        # Mirrored browser session kept turn 1 (u1-only) and redacted turn 2 ([u1, u2])
        sessions = c.get("/sessions", params={"drawing_id": u1}, headers=client_hdr).json()["sessions"]
        assert len(sessions) == 1
        assert [t["id"] for t in sessions[0]["turns"]] == ["t1"]


def test_explicit_project_scope_deduplicates_enforces_cap_and_persists_sheet_ids(tmp_path):
    """Explicit `scope: {kind: 'project'}` deduplicates `drawing_ids`, enforces MAX_SHEETS, and stores `sheet_ids`."""
    from tests.test_agent_client import MockSidecarState

    base = json.loads(FRONTEND_DRAWING.read_text())
    tower = _drawings()  # bldg-eg (22), bldg-og1 (10), bldg-og2 (5) in proj-bldg
    for i in range(13):
        d = copy.deepcopy(base)
        did = f"twr-{i:02d}"
        d |= {"id": did, "title": f"Tower · L{i}", "project_id": "proj-tower",
              "floor_label": f"Level {i}", "floor_order": i}
        d["evidence"]["door"] = d["evidence"]["door"][:2]
        tower.append(tools.LoadedDrawing(id=did, dir=None, drawing=d, engine=tools.BundleEngine(d),
                                         gold=[], anchors=[]))
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild", AURA_ANSWERER="hybrid")
    with TestClient(create_app(s, extra_drawings=tower, sidecar_transport=MockSidecarState().handler)) as c:
        # 1. Duplicate drawing_ids in explicit project scope count once (22 + 10 = 32, never 54)
        dup_scope = {"kind": "project", "project_id": "proj-bldg",
                     "drawing_ids": ["bldg-eg", "bldg-eg", "bldg-og1", "bldg-eg"]}
        r = c.post("/ask", json={"drawing_id": "bldg-eg", "question": "how many doors are there?",
                                 "scope": dup_scope})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["answer"] == {"kind": "count", "value": 22 + 10, "subject": "door"}
        assert len(body["evidence"]["boxes"]) == 32
        assert len({b["id"] for b in body["evidence"]["boxes"]}) == 32

        # Persisted answers row carries sheet_ids = ["bldg-eg", "bldg-og1"]
        svc = c.app.state.svc
        c.portal.call(svc.writer.flush)
        row = svc.store._q("SELECT sheet_ids FROM answers WHERE id=?", (body["answer_id"],))[0]
        assert json.loads(row["sheet_ids"]) == ["bldg-eg", "bldg-og1"]

        # 2. > 12 distinct sheets in explicit project scope refused with 400 on /ask and /chat
        over_scope = {"kind": "project", "project_id": "proj-tower",
                      "drawing_ids": [f"twr-{i:02d}" for i in range(13)]}
        over_ask = c.post("/ask", json={"drawing_id": "twr-00", "question": "how many doors are there?",
                                        "scope": over_scope})
        assert over_ask.status_code == 400
        assert over_ask.json()["code"] == "bad_request" and "12" in over_ask.json()["error"]
        over_chat = c.post("/chat", json={"drawing_id": "twr-00", "message": "how many doors are there?",
                                          "scope": over_scope})
        assert over_chat.status_code == 400 and "12" in over_chat.json()["error"]

        # <= 12 distinct sheets in explicit project scope succeeds (even if repeated in the array)
        ok_scope = {"kind": "project", "project_id": "proj-tower",
                    "drawing_ids": [f"twr-{i:02d}" for i in range(12)] + ["twr-00"]}
        ok_ask = c.post("/ask", json={"drawing_id": "twr-00", "question": "how many doors are there?",
                                      "scope": ok_scope})
        assert ok_ask.status_code == 200 and ok_ask.json()["answer"]["value"] == 24


def test_cross_floor_single_sheet_tags_drawing_id_and_ownerless_saved_row_visibility(tmp_path):
    """Asking about another named floor tags evidence with that floor's `drawing_id`; legacy ownerless
    library rows stay visible in GET /projects and explicit `project_id`, never implicitly."""
    extras = _drawings() + _standalone_drawings([("leg-a", "Leg A", 4), ("leg-b", "Leg B", 6)])
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, extra_drawings=extras)) as c:
        # Asking about 1. OG while viewing bldg-eg tags returned boxes with bldg-og1
        r = ask(c, "how many doors on 1. OG?", did="bldg-eg")
        assert r["answer"]["value"] == 10
        assert len(r["evidence"]["boxes"]) == 10
        assert {b["drawing_id"] for b in r["evidence"]["boxes"]} == {"bldg-og1"}

        # Insert a pre-token legacy ownerless library bundle (token_hash IS NULL, no up-* floors)
        svc = c.app.state.svc
        floors = [{"drawing_id": "leg-a", "label": "EG", "order": 0},
                  {"drawing_id": "leg-b", "label": "1. OG", "order": 1}]
        pbody = {"id": "proj-legacy", "title": "Legacy", "floors": floors, "auto_matched": False}
        c.portal.call(
            svc.writer.execute,
            "INSERT INTO projects(id, t, title, floors, auto_matched, body, token_hash) VALUES (?,?,?,?,?,?,NULL)",
            ("proj-legacy", 1.0, "Legacy", json.dumps(floors), 0, json.dumps(pbody)),
        )

        # Visible in GET /projects and usable when explicitly named, but never implicit on leg-a alone
        assert "proj-legacy" in [p["id"] for p in c.get("/projects").json()["projects"]]
        solo = ask(c, "how many doors are there?", did="leg-a")
        assert solo["answer"]["value"] == 4 and "breakdown" not in solo
        explicit = ask(c, "how many doors are there?", did="leg-a", project_id="proj-legacy")
        assert explicit["answer"]["value"] == 10


def test_inflight_upload_delete_excludes_deleted_sheet_and_purges_all_memory_caches(tmp_path):
    """If an upload sheet is deleted while `building.run` is mid-flight, its items never enter the
    combined answer or survive in SQLite / RAM caches (including score & door_leaves caches)."""
    from aura import door_leaves
    from aura.cache import answer_key
    from aura.contract import AskRequest
    from aura.pipeline import Caller
    from tests.test_uploads import plain_dxf, wait_status

    base = json.loads(FRONTEND_DRAWING.read_text())
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s)) as c:
        up1 = c.post("/uploads", files={"file": ("eg.dxf", plain_dxf(), "application/dxf")}).json()
        up2 = c.post("/uploads", files={"file": ("og1.dxf", plain_dxf(), "application/dxf")}).json()
        u1, tok1 = up1["drawing_id"], up1["delete_token"]
        u2 = up2["drawing_id"]
        assert wait_status(c, u1)["status"] == "ready"
        assert wait_status(c, u2)["status"] == "ready"

        svc = c.app.state.svc
        for uid, lbl, n_doors, prefix in [(u1, "EG", 3, "u1-d"), (u2, "1. OG", 5, "u2-d")]:
            dp = s.uploads_dir / uid / "drawing.json"
            d_json = copy.deepcopy(base) | {"id": uid, "title": lbl, "source": "upload", "floor_label": lbl}
            d_json["evidence"]["door"] = [
                dict(base["evidence"]["door"][idx]) | {"id": f"{prefix}-{idx}"} for idx in range(n_doors)
            ]
            dp.write_text(json.dumps(d_json))
            svc.registry.evict(uid)

        # Seed prefix-keyed check cache ("score:<u2>") and door_leaves._MEM ("<u2>:...")
        svc.checks._mem[f"score:{u2}"] = {"families": {"room": {"score": 4.0, "confidence": 0.9}}}
        door_leaves._MEM[f"{u2}:u2-d-0:deadbeef:0,0,1,1"] = {"verdict": "double"}

        sheets = [building.Sheet(u1, "EG", 0), building.Sheet(u2, "1. OG", 1)]
        plan = building.Plan("proj-race", "Race House", sheets, sheets, u1, building.DECIDER_DEFAULT)

        async def racing_run():
            from aura import main as main_mod
            from aura import pipeline

            async def racing_base_run(sv, req, *args, **kwargs):
                async for ev, data in pipeline.run(sv, req, *args, **kwargs):
                    if req.drawing_id == u2 and ev == "answer":
                        # u2 finished computing & caching right BEFORE/AS delete runs mid-flight,
                        # and also simulates a late cache write arriving just after delete!
                        await main_mod._remove_upload(svc, u2, c.app.state.drawing_store)
                        late_k = answer_key(u2, req.question, None, None, None, True)
                        svc.answers.put(late_k, data["response"], drawing_id=u2, question=req.question)
                    yield ev, data

            persist = lambda *a, **kw: building.persist_answer(*a, plan=plan, **kw)
            out = None
            async for ev, data in building.run(
                svc,
                AskRequest(drawing_id=u1, question="how many doors are there?"),
                Caller(ip_hash="test", presenter=False),
                plan=plan,
                base_run=racing_base_run,
                persist=persist,
            ):
                if ev == "answer":
                    out = data["response"]
            await svc.writer.flush()
            return out

        resp = c.portal.call(racing_run)
        assert resp is not None
        # Deleted sheet u2 was caught before combine(): count is u1 only (3, not 8), u2 marked error
        assert resp["answer"] == {"kind": "count", "value": 3, "subject": "door"}
        assert resp["breakdown"] == [
            {"drawing_id": u1, "label": "EG", "status": "ok", "value": 3, "unit": "door"},
            {"drawing_id": u2, "label": "1. OG", "status": "error"},
        ]
        assert all("u2-d-" not in b["id"] and b.get("drawing_id") != u2 for b in resp["evidence"]["boxes"])

        # Zero surviving items or cache entries for u2 in SQLite or RAM (including score & door_leaves)
        for table, col in [("answers", "response"), ("answer_cache", "response")]:
            for row in svc.store._q(f"SELECT {col} AS payload FROM {table}"):
                assert "u2-d-" not in row["payload"]
        assert all(not k.startswith(f'["{u2}"') for k in svc.answers._d)
        assert f"score:{u2}" not in svc.checks._mem
        assert not any(k.startswith(f"{u2}:") for k in door_leaves._MEM)
        assert tok1  # u1 stays untouched


