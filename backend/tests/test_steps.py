"""Tool steps (aura.steps): the model only names read-only tools; code computes every number."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from aura import steps, tools
from aura.config import Settings
from aura.contract import AskResponse
from aura.gemini.client import FunctionCall, GenerateResult, Meter
from tests.conftest import FRONTEND_DRAWING


class FakeClient:
    """Replays scripted model turns; each generate() is one metered request."""

    def __init__(self, turns: list, delay: float = 0.0):
        self.turns = list(turns)
        self.sent: list[dict] = []
        self.delay = delay

    async def generate(self, contents, tools=None, purpose="agent", *, meter=None, model=None,
                       config=None, **kw):
        self.sent.append({"contents": contents, "tools": tools, "config": config})
        if meter is not None:
            meter.add(SimpleNamespace(cost_usd=0.0001, input_tokens=100, output_tokens=10))
        if self.delay:
            await asyncio.sleep(self.delay)
        turn = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        if isinstance(turn, str):  # a JSON text reply (judge)
            return GenerateResult(text=turn, function_calls=[], input_tokens=100, output_tokens=10,
                                  cost_usd=0.0001, latency_ms=5, model=model or "fake-lite")
        calls = [FunctionCall(name=n, args=a) for n, a in turn]
        return GenerateResult(text="", function_calls=calls, input_tokens=100, output_tokens=10,
                              cost_usd=0.0001, latency_ms=5, model=model or "fake-lite",
                              content={"role": "model", "parts": [
                                  {"functionCall": {"name": n, "args": a}} for n, a in turn]})


@pytest.fixture(scope="module")
def d():
    return tools.load_file(FRONTEND_DRAWING)


def svc_for(client):
    return SimpleNamespace(client=client, settings=Settings())


async def ask(d, turns, question="show the doors", **kw):
    client = FakeClient(turns, **kw)
    stats = {"meter": Meter(), "route": "template"}
    built = await steps.answer_with_tools(svc_for(client), d, question, "en",
                                          SimpleNamespace(ip_hash=None, presenter=False), stats)
    return built, stats, client


def validated(built, stats) -> dict:
    body = dict(built.body, route=stats["route"], calls=stats["meter"].calls,
                cost_usd=round(stats["meter"].cost_usd, 8), latency_ms=1)
    AskResponse.model_validate(body)
    return body


def wc_rooms(d) -> list[str]:
    return sorted({b["room_id"] for b in d.drawing["evidence"]["wc"]})


async def test_rooms_with_toilets_in_one_call_from_indexed_fixtures(d):
    built, stats, _ = await ask(d, [[("rooms_containing", {"kind": "wc", "finish_as": "list"})]])
    body = validated(built, stats)
    assert stats["meter"].calls == 1
    kept = [r["room_id"] for r in body["evidence"]["rooms"]]
    assert sorted(kept) == wc_rooms(d)
    assert body["answer"] == {"kind": "count", "value": len(kept), "subject": "room"}
    assert {b["subject"] for b in body["evidence"]["boxes"]} == {"wc"}
    assert body["method"] == "cad_geometry" and stats["route"] == "choice"


async def test_numbers_come_from_tools_not_model_text(d):
    turns = [[("list_rooms", {"room_type": "bedroom", "min_area_m2": 12})],
             [("finish", {"set": "s1", "answer": "area", "restated": "Total area: 999 m2"})]]
    built, stats, _ = await ask(d, turns)
    body = validated(built, stats)
    want = [r for r in d.drawing["rooms"] if steps.room_type(r) == "bedroom" and r["area_m2"] >= 12]
    assert want, "fixture drawing has big bedrooms"
    assert body["answer"] == {"kind": "area", "value": round(sum(r["area_m2"] for r in want), 2),
                              "unit": "m2"}
    assert sorted(r["room_id"] for r in body["evidence"]["rooms"]) == sorted(r["room_id"] for r in want)
    assert stats["meter"].calls == 2


async def test_count_of_openings_uses_box_set(d):
    built, stats, _ = await ask(d, [[("list_openings", {"kind": "door", "finish_as": "count"})]])
    body = validated(built, stats)
    n = tools.count_objects(d, "door").value  # same number as the exact template
    assert body["answer"] == {"kind": "count", "value": n, "subject": "door"}
    assert len(body["evidence"]["boxes"]) == n


async def test_selected_scope_limits_the_room_pool(d):
    client = FakeClient([[("list_rooms", {"room_type": "bedroom", "finish_as": "list"})]])
    stats = {"meter": Meter(), "route": "template"}
    scope = {"kind": "unit", "unit": "A"}
    built = await steps.answer_with_tools(svc_for(client), d, "list the bedrooms", "en",
                                          SimpleNamespace(ip_hash=None, presenter=False), stats,
                                          None, scope)
    body = validated(built, stats)
    want = sorted(r["room_id"] for r in d.drawing["rooms"]
                  if r.get("unit") == "A" and steps.room_type(r) == "bedroom")
    assert want
    assert sorted(r["room_id"] for r in body["evidence"]["rooms"]) == want
    assert body["scope"] == scope


async def test_failed_call_in_a_turn_blocks_finish_as(d):
    turns = [[("rooms_containing", {"kind": "sauna"}), ("list_rooms", {"finish_as": "count"})],
             [("rooms_containing", {"kind": "wc", "finish_as": "count"})]]
    built, stats, _ = await ask(d, turns)
    body = validated(built, stats)
    assert stats["meter"].calls == 2  # re-planned instead of finishing on the unrestricted set
    assert body["answer"]["value"] == len(wc_rooms(d))


async def test_falling_back_to_all_rooms_is_not_an_answer(d):
    turns = [[("find_text", {"text": "street"}), ("list_rooms", {"finish_as": "list"})]]
    built, _, _ = await ask(d, turns)
    assert built is None


async def test_unfiltered_room_list_is_not_an_answer_unless_grouped(d):
    assert (await ask(d, [[("list_rooms", {"finish_as": "count"})]]))[0] is None
    built, stats, _ = await ask(d, [[("list_rooms", {"finish_as": "list", "group_by": "unit"})]])
    assert validated(built, stats)["evidence"]["groups"]


async def test_unrelated_judge_does_not_taint_an_exact_answer(d):
    verdict = json.dumps({"rooms": []})
    turns = [[("list_rooms", {"room_type": "bathroom"}), ("judge", {"set": "s1", "question": "x?"})],
             verdict, [("finish", {"set": "s1", "answer": "count"})]]
    built, stats, _ = await ask(d, turns)
    assert validated(built, stats)["method"] == "cad_geometry"


async def ask_with_history(d, turns, history, question="show the doors"):
    client = FakeClient(turns)
    stats = {"meter": Meter(), "route": "template"}
    built = await steps.answer_with_tools(svc_for(client), d, question, "en",
                                          SimpleNamespace(ip_hash=None, presenter=False), stats,
                                          history)
    return built, stats, client


def all_doors_turn(d) -> list[dict]:
    doors = tools.count_objects(d, "door")
    return [{"message": "show all doors", "response": {
        "answer": {"kind": "count", "value": doors.value, "subject": "door"},
        "evidence": {"boxes": [b.model_dump(exclude_none=True) for b in doors.boxes], "rooms": []}}}]


def doors_of_wc_rooms(d) -> set[str]:
    tb = steps.Toolbox(d)
    wc = set(wc_rooms(d))
    return {b.id for b in tb.boxes["door"] if wc & set(tb.box_rooms[b.id])}


async def test_doors_of_toilet_rooms_standalone_and_as_follow_up_agree(d):
    want = doors_of_wc_rooms(d)
    # every WC room has at least one door: fewer doors than WC rooms would be wrong
    assert len(want) >= len(wc_rooms(d))
    alone, st1, _ = await ask(d, [[("rooms_containing", {"kind": "wc"}),
                                   ("list_openings", {"kind": "door", "rooms": "s1", "finish_as": "list"})]])
    follow, st2, client = await ask_with_history(
        d, [[("rooms_containing", {"kind": "wc"}),
             ("list_openings", {"kind": "door", "rooms": "s2", "from": "s1", "finish_as": "list"})]],
        all_doors_turn(d))
    for built, stats in ((alone, st1), (follow, st2)):
        body = validated(built, stats)
        assert body["answer"] == {"kind": "count", "value": len(want), "subject": "door"}
        assert {b["id"] for b in body["evidence"]["boxes"]} == want
        assert body["method"] == "cad_geometry"
    index = next(p["text"] for c in client.sent[0]["contents"] for p in c["parts"]
                 if "Drawing index" in p.get("text", ""))
    assert '"previous_answer_set"' in index and '"set": "s1"' in index


async def test_follow_up_only_narrows_previous_items(d):
    history = all_doors_turn(d)
    keep = history[0]["response"]["evidence"]["boxes"][:3]
    history[0]["response"]["evidence"]["boxes"] = keep
    built, stats, _ = await ask_with_history(
        d, [[("list_openings", {"kind": "door", "from": "s1", "finish_as": "count"})]], history)
    body = validated(built, stats)
    assert {b["id"] for b in body["evidence"]["boxes"]} == {b["id"] for b in keep}


async def test_no_internal_set_refs_in_user_text(d):
    built, stats, _ = await ask(d, [[("rooms_containing", {"kind": "wc"}),
                                     ("connected_rooms", {"to": "s1", "via": "door"}),
                                     ("list_openings", {"kind": "door", "rooms": "s1", "double": True,
                                                        "finish_as": "count"})]])
    body = validated(built, stats)
    texts = [body["explanation"]] + [n["body"] for n in body["trace"]
                                     if n.get("stage") in ("filter", "answer")]
    texts += [n["detail"]["rule"] for n in body["trace"] if n.get("stage") == "filter"]
    assert not [t for t in texts if re.search(r"\bs\d+\b", t)]
    assert "rooms that contain a WC" in body["explanation"]
    assert "double door" in body["explanation"]
    answer_node = next(n for n in body["trace"] if n.get("stage") == "answer")
    assert answer_node["detail"]["source"] == "tools"


@pytest.mark.parametrize(("question", "strict"), [
    ("which of them are wider than 0.95 m", True), ("doors over 0.95 m", True),
    ("which rooms have a door wider than 0.95 m", True),  # 'door wider' is not 'or wider'
    ("which rooms have a door of 0.95 m or wider", False),
    ("doors at least 0.95 m wide", False), ("doors of 0.95 m or more", False),
    ("doors 0.95 m", False)])
def test_width_bound_strictness_follows_the_words(d, question, strict):
    tb = steps.Toolbox(d)
    w = sorted(tb.width_m[b.id] for b in tb.boxes["door"])[len(tb.boxes["door"]) // 2]
    sess = steps.Session(tb)
    sess.question = question
    rs = sess.run("list_openings", {"kind": "door", "min_width_m": w})
    widths = [tb.width_m[i] for i in rs.ids]
    assert widths and all(x > w if strict else x >= w for x in widths)
    assert (w in widths) is (not strict)  # a door exactly at the bound is the difference
    assert (f"width > {w:.2f} m" if strict else f"width ≥ {w:.2f} m") in rs.rule
    assert ("wider than" if strict else "at least") in rs.desc


@pytest.mark.parametrize(("question", "strict"), [
    ("rooms smaller than 12 m²", True), ("rooms under 12 m²", True),
    ("rooms of at most 12 m²", False), ("rooms up to 12 m²", False)])
def test_area_upper_bound_strictness(d, question, strict):
    tb = steps.Toolbox(d)
    a = next(r["area_m2"] for r in d.drawing["rooms"] if r.get("area_m2"))
    sess = steps.Session(tb)
    sess.question = question
    rs = sess.run("list_rooms", {"max_area_m2": a})
    areas = [tb.rooms[r]["area_m2"] for r in rs.ids]
    assert (a in areas) is (not strict)
    assert (f"area < {a:g} m²" if strict else f"area ≤ {a:g} m²") in rs.rule


async def test_call_limit_returns_none(d):
    built, stats, client = await ask(d, [[("list_rooms", {})]])  # never finishes
    assert built is None  # the reasoning step got no usable reply either
    plan = [c for c in client.sent if c["tools"]]
    assert len(plan) == steps.MAX_CALLS and stats["meter"].calls == steps.MAX_CALLS + 1


async def test_step_limit_returns_none(d):
    built, stats, _ = await ask(d, [[("list_rooms", {})] * (steps.MAX_STEPS + 1)])
    assert built is None and stats["meter"].calls == 2  # 1 planning call + the reasoning step


async def test_unanswerable_returns_none(d):
    built, _, _ = await ask(d, [[("finish", {"set": "none", "answer": "list"})]])
    assert built is None


async def test_bad_arguments_are_reported_back_to_the_model(d):
    turns = [[("rooms_containing", {"kind": "sauna"})],
             [("rooms_containing", {"kind": "wc", "finish_as": "count"})]]
    built, stats, client = await ask(d, turns)
    body = validated(built, stats)
    replies = [p["functionResponse"]["response"] for c in client.sent[1]["contents"]
               for p in c["parts"] if "functionResponse" in p]
    assert replies and "error" in replies[0]
    assert body["answer"] == {"kind": "count", "value": len(wc_rooms(d)), "subject": "room"}


async def test_chained_sets_in_one_turn(d):
    turns = [[("list_rooms", {"room_type": "corridor"}),
              ("connected_rooms", {"to": "s1", "via": "door", "finish_as": "list"})]]
    built, stats, _ = await ask(d, turns)
    body = validated(built, stats)
    corridors = {r["room_id"] for r in d.drawing["rooms"] if steps.room_type(r) == "corridor"}
    kept = {r["room_id"] for r in body["evidence"]["rooms"]}
    assert kept and not kept & corridors
    assert all(b["subject"] == "door" for b in body["evidence"]["boxes"])
    assert stats["meter"].calls == 1


async def test_judge_marks_method_as_model_estimate(d):
    bath = [r["room_id"] for r in d.drawing["rooms"] if steps.room_type(r) == "bathroom"]
    verdict = json.dumps({"rooms": [{"id": rid, "yes": i == 0, "confidence": 0.8}
                                    for i, rid in enumerate(bath)]})
    turns = [[("list_rooms", {"room_type": "bathroom"}),
              ("judge", {"set": "s1", "question": "Is it accessible?"})],
             verdict,
             [("finish", {"set": "s2", "answer": "list"})]]
    built, stats, _ = await ask(d, turns)
    body = validated(built, stats)
    assert [r["room_id"] for r in body["evidence"]["rooms"]] == bath[:1]
    assert body["method"] == "agent_estimate" and body["estimate"]["confidence"] == 0.8
    assert stats["meter"].calls == 3


async def test_trace_has_staged_nodes(d):
    built, stats, _ = await ask(d, [[("rooms_on_side", {"side": "north", "finish_as": "list"})]])
    assert built is not None
    trace = validated(built, stats)["trace"]
    by_stage = {n.get("stage"): n for n in trace if n.get("stage")}
    assert set(by_stage) == {"decision", "resources", "filter", "answer"}
    assert {"decider", "options", "chosen", "route"} <= set(by_stage["decision"]["detail"])
    assert {"tables", "grabbed", "ms"} <= set(by_stage["resources"]["detail"])
    assert {"rule", "kept", "dropped"} <= set(by_stage["filter"]["detail"])
    assert {"source", "calls", "cost_usd"} <= set(by_stage["answer"]["detail"])
    assert "north" in by_stage["filter"]["detail"]["rule"]


async def test_deadline_returns_none(d, monkeypatch):
    monkeypatch.setattr(steps, "DEADLINE_S", 0.05)
    built, _, _ = await ask(d, [[("list_rooms", {"finish_as": "list"})]], delay=0.5)
    assert built is None


def test_unindexed_fixture_is_an_error_not_an_empty_answer(d):
    tb = steps.Toolbox(d)
    tb.supported.discard("wc")
    with pytest.raises(steps.StepError):
        steps.Session(tb).run("rooms_containing", {"kind": "wc"})


async def test_empty_stand_in_for_unindexed_objects_is_not_a_zero(d, monkeypatch):
    real = steps.Toolbox.__init__

    def no_wc(self, dd):
        real(self, dd)
        self.supported.discard("wc")

    monkeypatch.setattr(steps.Toolbox, "__init__", no_wc)
    turns = [[("rooms_containing", {"kind": "wc"})],
             [("list_rooms", {"label": "zzz-no-such-label", "finish_as": "list"})]]
    built, _, _ = await ask(d, turns)
    assert built is None


def test_no_code_execution_tool():
    names = set(steps.TOOL_NAMES)
    assert names == {"list_rooms", "rooms_containing", "list_openings", "connected_rooms",
                     "rooms_on_side", "find_text", "judge", "finish"}


def store_doc(d, **extra):
    data = {**d.drawing, "evidence_method": {"door": "vision", "window": "vision", "wc": "vision",
                                             "room": "pdf_vector"}, **extra}
    return tools.LoadedDrawing(id=d.id, dir=None, drawing=data, engine=tools.BundleEngine(data), source="upload")


async def test_store_estimates_label_the_answer_never_cad_geometry(d):
    sd = store_doc(d)
    rooms, st1, _ = await ask(sd, [[("list_rooms", {"min_area_m2": 15, "finish_as": "list"})]])
    assert validated(rooms, st1)["method"] == "pdf_vector"
    doors, st2, _ = await ask(sd, [[("list_rooms", {"room_type": "bathroom"}),
                                    ("list_openings", {"kind": "door", "rooms": "s1", "finish_as": "count"})]])
    body = validated(doors, st2)
    assert body["method"] == "vision" and "estimate" in body["explanation"]


async def test_width_filter_abstains_when_widths_are_not_measured(d):
    """Model-detected doors carry no measured width: a width filter is not answered with a guess."""
    built, _, client = await ask(store_doc(d), [[("rooms_containing", {"kind": "door", "min_width_m": 0.9,
                                                                      "finish_as": "list"})],
                                               [("finish", {"set": "none", "answer": "list"})]])
    assert built is None
    replies = [p["functionResponse"]["response"] for c in client.sent[1]["contents"] for p in c["parts"]
               if "functionResponse" in p]
    assert "not measured" in replies[0]["error"]


def test_door_width_is_the_clear_opening_not_the_padded_box(d):
    """Swiss boxes are the door footprint plus 0.08 m on each side; the width is the footprint."""
    from aura.solvers.swiss import PAD

    tb = steps.Toolbox(d)
    mx, my = d.drawing["m_per_pct_x"], d.drawing["m_per_pct_y"]
    for b in tb.boxes["door"]:
        assert tb.width_m[b.id] == round(max(b.w * mx, b.h * my) - 2 * PAD["door"], 2)


async def test_rooms_with_a_door_wider_than_answers_with_rooms(d):
    tb = steps.Toolbox(d)
    want = sorted({r for b in tb.boxes["door"] if tb.width_m[b.id] > 0.85 for r in tb.box_rooms[b.id]})
    built, stats, _ = await ask(d, [[("rooms_containing", {"kind": "door", "min_width_m": 0.85,
                                                           "finish_as": "list"})]],
                                question="which rooms have a door wider than 0.85 m")
    body = validated(built, stats)
    assert body["answer"]["subject"] == "room"
    assert sorted(r["room_id"] for r in body["evidence"]["rooms"]) == want
    assert "clear width > 0.85 m" in body["explanation"]


async def test_no_metric_scale_skips_the_geometric_tools(d, monkeypatch):
    built, _, client = await ask(store_doc(d, m_per_pct_x=0), [[("list_rooms", {"finish_as": "list"})]])
    assert built is None and all(not c["tools"] for c in client.sent)  # no tool step, only reasoning
    flagged = tools.LoadedDrawing(id=d.id, dir=None, drawing={**d.drawing, "toolbox": False},
                                  engine=d.engine, source="upload")
    assert not steps.has_scale(flagged) and steps.has_scale(d)


async def test_a_door_set_answers_with_rooms_when_rooms_are_asked(d):
    tb = steps.Toolbox(d)
    doors = [b.id for b in tb.boxes["door"] if tb.width_m[b.id] > 0.85]
    want = sorted({r for i in doors for r in tb.box_rooms[i]})
    built, stats, _ = await ask(d, [[("list_openings", {"kind": "door", "min_width_m": 0.85,
                                                        "finish_as": "count"})]],
                                question="which rooms have a door wider than 0.85 m")
    body = validated(built, stats)
    assert body["answer"] == {"kind": "count", "value": len(want), "subject": "room"}
    assert len(want) <= len(d.drawing["rooms"])
    assert {b["id"] for b in body["evidence"]["boxes"]} == set(doors)


@pytest.mark.parametrize(("q", "rooms"), [
    ("which rooms have a door wider than 0.92 m", True), ("show me the rooms with toilets", True),
    ("ok now show only doors that are in rooms that have toilets", False),
    ("which of them are wider than 0.95 m", False), ("how many doors are wider than 1 m", False)])
def test_asks_for_rooms(q, rooms):
    assert steps.asks_for_rooms(q) is rooms


def reasoning_reply(**out):
    return json.dumps({"can_answer": True, "answer": "", "working": "", **out})


async def test_reasoning_step_answers_what_the_tools_cannot(d):
    """Kitchens x a price: the tools find the kitchens, the model does the arithmetic and is labelled
    as reasoned; the handed data and its working are in the trace, the kitchens highlighted."""
    kitchens = [r for r in d.drawing["rooms"] if steps.room_type(r) == "kitchen"]
    total = round(sum(r["area_m2"] for r in kitchens), 2)
    reply = reasoning_reply(answer=f"{len(kitchens)} kitchens, {total} m², ${round(total * 2.5, 2)}.",
                            working=f"kitchens {total} m² × 2.5 = {round(total * 2.5, 2)}",
                            used_ids=[r["room_id"] for r in kitchens] + ["nope"], number=round(total * 2.5, 2),
                            unit="$")
    q = "i will install wood floor on the kitchens, price of the whole plan at 2.5$ per m²"
    built, stats, client = await ask(d, [[("list_rooms", {"room_type": "kitchen", "finish_as": "reason"})],
                                         reply], question=q)
    body = validated(built, stats)
    assert body["method"] == "agent_estimate" and body["answer"]["kind"] == "note"
    assert body["answer"]["number"] == round(total * 2.5, 2) and body["answer"]["unit"] == "$"
    assert sorted(r["room_id"] for r in body["evidence"]["rooms"]) == sorted(r["room_id"] for r in kitchens)
    nodes = {n["id"]: n for n in body["trace"]}
    assert "Reasoned by the model" in body["explanation"]
    assert nodes["working"]["detail"]["rule"].startswith("kitchens")
    assert any("kitchen" in t for t in nodes["handed"]["detail"]["tables"])  # the tool's set was handed over
    assert nodes["answer"]["detail"]["source"] == "reasoned"
    handed = json.loads(next(p["text"] for c in client.sent[-1]["contents"] for p in c["parts"]
                             if p.get("text", "").startswith("Plan data: ")).removeprefix("Plan data: "))
    assert handed["tool_results"][0]["ids"] == [r["room_id"] for r in kitchens]
    assert stats["meter"].calls == 2


async def test_reasoning_step_cannot_answer_is_an_honest_abstain(d):
    reply = json.dumps({"can_answer": False, "answer": "-", "working": "",
                        "missing": "The plan has no wall heights; tell me the height to price the paint."})
    built, stats, _ = await ask(d, [[("finish", {"set": "none", "answer": "count"})], reply],
                                question="cost to paint the walls of room 3.04 at 4 €/m²")
    body = validated(built, stats)
    assert body["answer"]["kind"] == "abstain" and body["answer"]["reason"] == "not_answerable"
    answer = next(n for n in body["trace"] if n["id"] == "answer")
    assert "wall heights" in answer["body"] and answer["detail"]["source"] == "reasoned"


async def test_a_users_number_no_tool_used_goes_to_the_reasoning_step(d):
    """'kitchens at 2.5 $/m²': the area of the kitchens is not the answer; 2.5 still has to be used."""
    reply = reasoning_reply(answer="35 $", working="14 m² × 2.5 = 35", number=35, unit="$")
    built, stats, _ = await ask(d, [[("list_rooms", {"room_type": "kitchen", "finish_as": "area"})], reply],
                                question="price of wood floor for the kitchens at 2.5$ per m²")
    body = validated(built, stats)
    assert body["method"] == "agent_estimate" and body["answer"]["number"] == 35
    plain, st2, _ = await ask(d, [[("list_rooms", {"room_type": "bedroom", "min_area_m2": 12.01,
                                                   "finish_as": "count"})]],
                              question="how many bedrooms are larger than 12 m²")
    assert validated(plain, st2)["method"] == "cad_geometry" and st2["meter"].calls == 1


def test_unused_numbers(d):
    sess = steps.Session(steps.Toolbox(d))
    rs = sess.run("list_rooms", {"min_area_m2": 12})
    assert steps.unused_numbers("rooms larger than 12 m² at 30 € le m²", sess, rs) == ["30"]
    assert steps.unused_numbers("area of room 3.04", sess, rs) == []
    assert steps.unused_numbers("paint the living rooms at 4 €/m²", sess, rs) == ["4"]  # not inside '3.04'


BATTERY_OPEN = [  # the five battery questions the tool tier used to finish with a bare count/area
    ("How many people could comfortably live in apartment A, and why?",
     [("list_rooms", {"unit": "A", "finish_as": "count"})]),
    ("Compare apartment A and B: which is better for a family of four?",
     [("list_rooms", {"finish_as": "list", "group_by": "unit"})]),
    ("What share of the floor area is circulation (corridors and halls)?",
     [("list_rooms", {"room_type": "corridor", "finish_as": "area"})]),
    ("Where would you put a washing machine in apartment B?",
     [("list_rooms", {"unit": "B", "room_type": "storage", "finish_as": "count"})]),
    ("and if we merge the two smallest bedrooms of apartment B, how big is the new room?",
     [("list_rooms", {"unit": "B", "room_type": "bedroom", "finish_as": "list"})]),
]


@pytest.mark.parametrize(("question", "turn"), BATTERY_OPEN)
async def test_open_questions_reach_the_reasoning_step(d, question, turn):
    assert not steps.plain_question(question, d)
    reply = reasoning_reply(answer="reasoned", working="from the totals")
    built, stats, _ = await ask(d, [turn, reply], question=question)
    body = validated(built, stats)
    assert body["method"] == "agent_estimate" and body["answer"]["value"] == "reasoned"
    assert stats["meter"].calls == 2  # one planning call, one reasoning call


@pytest.mark.parametrize("question", [
    "how many bedrooms are larger than 12 m²", "which rooms have no window", "show all rooms with toilets",
    "which of them are wider than 0.95 m", "how many doors are there", "Is there a room without a window?",
    "How many double doors are there?"])
def test_plain_lookups_stay_with_the_tools(d, question):
    assert steps.plain_question(question, d)


def test_summary_has_per_room_objects_connections_and_totals(d):
    sm = steps._plan_summary(d, None)
    bath = next(r for r in sm["rooms"] if r["type"] == "bathroom")
    assert bath.get("fixtures") and bath.get("doors") and bath.get("door_widths_m") and bath.get("connects_to")
    indoor = round(sum(r["area_m2"] for r in d.drawing["rooms"] if not r.get("outdoor")), 2)
    assert sm["totals"]["area_m2"]["indoor"] == indoor
    assert len(json.dumps(sm, ensure_ascii=False)) <= steps.SUMMARY_CHARS


async def test_wrong_plan_values_are_reasked_then_flagged(d):
    total = steps._totals(d, d.drawing["rooms"], None)["area_m2"]["indoor"]
    bad = reasoning_reply(answer="x", working="w", plan_values=[{"what": "indoor area", "value": total - 2}])
    fixed = reasoning_reply(answer="fixed", working="w", plan_values=[{"what": "indoor area", "value": total}])
    built, stats, _ = await ask(d, [[("list_rooms", {"finish_as": "reason"})], bad, fixed],
                                question="how long to vacuum the floor at 20 m² per 10 minutes")
    body = validated(built, stats)
    assert body["answer"]["value"] == "fixed" and stats["meter"].calls == 3
    still, st2, _ = await ask(d, [[("list_rooms", {"finish_as": "reason"})], bad, bad],
                              question="how long to vacuum the floor at 20 m² per 10 minutes")
    body2 = validated(still, st2)
    assert "do not match the drawing data" in body2["explanation"]
    assert any(n["id"] == "check" and n["kind"] == "warn" for n in body2["trace"])


async def test_reasoning_timeout_is_an_honest_abstain(d, monkeypatch):
    monkeypatch.setattr(steps, "REASON_TIMEOUT_S", 0.05)
    built, stats, _ = await ask(d, [[("list_rooms", {"finish_as": "reason"})]], question="why is it so?",
                                delay=0.2)
    body = validated(built, stats)
    assert body["answer"]["kind"] == "abstain" and "took longer" in json.dumps(body["trace"])


def test_answer_text_must_agree_with_the_working():
    out = {"can_answer": True, "answer": "You need 33 tiles.", "working": "11.44 × 1.1 / 0.36 = 34.96 → 35",
           "number": 35}
    assert [b["value"] for b in steps.check_answer_numbers(out, {11.44}, "tiles with 10 % waste")] == [33.0, 35.0]
    working_says_33 = dict(out, working="… = 34.96, rounded up to 33")
    assert steps.check_answer_numbers(working_says_33, {11.44}, "tiles")  # text 33 vs result 35
    ok = dict(out, answer="You need 35 tiles.")
    assert steps.check_answer_numbers(ok, {11.44}, "tiles with 10 % waste") == []
