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


async def ask(d, turns, question="q", **kw):
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
    built = await steps.answer_with_tools(svc_for(client), d, "q", "en",
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


async def ask_with_history(d, turns, history, question="q"):
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


async def test_call_limit_returns_none(d):
    built, stats, client = await ask(d, [[("list_rooms", {})]])  # never finishes
    assert built is None
    assert stats["meter"].calls == steps.MAX_CALLS == len(client.sent)


async def test_step_limit_returns_none(d):
    built, stats, _ = await ask(d, [[("list_rooms", {})] * (steps.MAX_STEPS + 1)])
    assert built is None and stats["meter"].calls == 1


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
