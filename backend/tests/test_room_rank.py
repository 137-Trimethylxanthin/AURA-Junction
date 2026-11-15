"""Superlative room questions and 'that room' follow-ups are answered exactly (0 model calls).

Two-stage answering: grab candidates from the indexed drawing, filter them in code, and say so in
the trace (stages resources → decision → filter → answer).
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from aura import tools
from aura.router import route
from tests.conftest import FRONTEND_DRAWING
from tests.test_agent_client import (
    SWISS,
    MockGeminiDecisionsTransport,
    MockSidecarState,
    make_hybrid_app,
    parse_sse_raw,
)


@pytest.mark.parametrize(("q", "order", "concept"), [
    ("what is the biggest room", "max", None),
    ("which room is biggest", "max", None),
    ("biggest room by area", "max", None),
    ("smallest room", "min", None),
    ("largest bedroom", "max", "bedroom"),
    ("Welches Zimmer ist am grössten?", "max", None),
])
def test_superlative_room_questions_route_to_room_rank(q, order, concept):
    r = route(q)
    assert (r.intent, r.subject, r.topic) == ("room_rank", order, concept)


@pytest.mark.parametrize("q", [
    "can you highlight all doors leading to that room",
    "how many windows does it have",
    "show only the doors of that room",
])
def test_anaphoric_or_qualified_questions_never_hit_the_plain_count_template(q):
    r = route(q)
    assert r.intent != "count" and "anaphora" in r.matched


def test_unrelated_superlatives_still_need_the_model():
    assert route("which bedroom has the most windows").intent == "unknown"
    assert route("what is the largest apartment").intent == "unknown"
    assert route("doors leading to room 3.02").intent == "adjacent"


def _chat(c, conv, msg):
    body = {"drawing_id": SWISS, "message": msg, **({"conversation_id": conv} if conv else {})}
    evs, _ = parse_sse_raw(c.post("/chat", json=body).text)
    conv = next(d for n, d in evs if n == "turn")["conversation_id"]
    return conv, next(d for n, d in evs if n == "answer")["response"]


def test_biggest_room_then_doors_leading_to_that_room_is_exact(tmp_path):
    d = tools.load_file(FRONTEND_DRAWING)
    big = tools.rank_rooms(d, "max").meta["winner"]
    doors_in = tools.adjacent_boxes(d, "door", [big["id"]])
    all_doors = tools.count_objects(d, "door", {"kind": "floor"})
    assert 0 < doors_in.value < all_doors.value

    sc, gem = MockSidecarState(), MockGeminiDecisionsTransport()
    with TestClient(make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)) as c:
        conv, r1 = _chat(c, None, "what is the biggest room")
        assert r1["answer"]["kind"] == "note" and r1["answer"]["number"] == round(big["area_m2"], 2)
        assert big["id"] in r1["answer"]["value"] and r1["calls"] == 0 and r1["method"] == "cad_geometry"
        assert [x["room_id"] for x in r1["evidence"]["rooms"] if x["included"]] == [big["id"]]
        stages = [n["stage"] for n in r1["trace"] if n.get("stage")]
        assert stages == ["decision", "resources", "filter", "answer"]
        dec = next(n for n in r1["trace"] if n.get("stage") == "decision")
        assert dec["detail"]["chosen"] == "room_rank" and dec["detail"]["route"] == "exact"

        conv, r2 = _chat(c, conv, "can you highlight all doors leading to that room")
        assert r2["answer"] == {"kind": "count", "value": int(doors_in.value), "subject": "door"}
        assert sorted(b["id"] for b in r2["evidence"]["boxes"]) == sorted(b.id for b in doors_in.boxes)
        assert r2["calls"] == 0 and r2["scope"] == {"kind": "room", "room_id": big["id"]}
        ref = next(n for n in r2["trace"] if n.get("stage") == "decision")
        assert ref["detail"]["decider"] == "context" and ref["detail"]["chosen"] == [big["id"]]

        # "it" still means that room after an answer that highlighted doors only.
        conv, r3 = _chat(c, conv, "how many windows does it have")
        wins = tools.adjacent_boxes(d, "window", [big["id"]])
        assert r3["answer"]["value"] == int(wins.value) and r3["calls"] == 0

        assert sc.runs == [] and len(gem.calls) == 0  # neither the agent nor a model was asked


def test_demo_trace_detail_keeps_only_decision_and_answer(tmp_path):
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState(),
                                    AURA_TRACE_DETAIL="demo")) as c:
        _, r = _chat(c, None, "smallest room")
        stages = [n.get("stage") for n in r["trace"] if n.get("stage")]
        assert stages == ["decision", "answer"] and all("detail" not in n for n in r["trace"])


def test_decision_model_exact_route_answers_and_is_shown_in_the_trace(tmp_path):
    """gemini-decision-1 says a plain door count answers it: exact answer, decision node with its
    calibrated outputs and runner-up (this path used to crash on a missing attribute)."""
    def exact(path, body):
        q = body.get("questions") or {}
        if "exact_sufficient" not in q:
            return None
        ans = {"on_drawing": {"noul": 0.97}, "exact_sufficient": {"noul": 0.91},
               "intent": {"choice": "count", "confidence": 0.88, "probabilities": {"count": 0.88, "area": 0.1}},
               "subject": {"choice": "door", "confidence": 0.93, "probabilities": {"door": 0.93, "window": 0.05}},
               "scope": {"choice": "floor", "confidence": 0.95, "probabilities": {"floor": 0.95}}}
        if "cache_match" in q:
            ans["cache_match"] = {"choice": "none", "confidence": 0.9, "probabilities": {"none": 0.9}}
        return 200, {"answers": ans, "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 10}}

    sc = MockSidecarState()
    with TestClient(make_hybrid_app(tmp_path, sidecar=sc, gemini=MockGeminiDecisionsTransport(exact))) as c:
        _, r = _chat(c, None, "tell me about the doors please")
        assert r["answer"]["kind"] == "count" and r["answer"]["subject"] == "door" and r["route"] == "choice"
        dec = next(n for n in r["trace"] if n.get("stage") == "decision")
        assert dec["detail"]["decider"] == "gemini-decision-1" and dec["detail"]["chosen"] == "exact"
        assert dec["detail"]["runner_up"] == "agent" and dec["confidence"] == pytest.approx(0.91)
        assert dec["detail"]["asked"]["subject"]["choice"] == "door"
        assert sc.runs == []


def test_decision_trace_never_shows_other_peoples_cached_questions(tmp_path):
    def gate(path, body):
        q = body.get("questions") or {}
        if "cache_match" not in q:
            return None
        opts = [k for k in q["cache_match"]["criteria"] if k != "none"]
        ans = {"on_drawing": {"noul": 0.97}, "exact_sufficient": {"noul": 0.05},
               "intent": {"choice": "other", "confidence": 0.9, "probabilities": {"other": 0.9}},
               "subject": {"choice": "other", "confidence": 0.9, "probabilities": {"other": 0.9}},
               "scope": {"choice": "floor", "confidence": 0.9, "probabilities": {"floor": 0.9}},
               "cache_match": {"choice": "none", "confidence": 0.8,
                               "probabilities": {"none": 0.8, **{o: 0.2 for o in opts}}}}
        return 200, {"answers": ans, "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1}}

    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState(),
                                    gemini=MockGeminiDecisionsTransport(gate))) as c:
        secret = "what is the total floor area zebra"  # someone else's earlier question
        _chat(c, None, secret)
        _, r = _chat(c, None, "tell me something unusual about the doors please")
        dec = next(n for n in r["trace"] if n.get("stage") == "decision")
        assert "zebra" not in str(dec)
        assert "question" not in dec["detail"]["asked"]["cache_match"]


def test_agent_down_is_a_neutral_failure_without_raw_errors(tmp_path):
    sc = MockSidecarState()
    sc.health_ok = False

    def down(request):
        raise httpx.ConnectError("connection refused to http://127.0.0.1:8090/runs")

    sc.handler = None
    with TestClient(make_hybrid_app(tmp_path, sidecar=sc)) as c:
        c.app.state.svc.agent._http._transport = httpx.MockTransport(down)
        _, r = _chat(c, None, "how many double doors are there?")
        assert r["agent_failure"] == "unavailable" and r["answer"]["kind"] == "abstain"
        look = next(n for n in r["trace"] if n["id"] == "look")
        assert "127.0.0.1" not in look["body"] and "refused" not in look["body"]


def test_internal_ids_never_reach_the_answer_text():
    """A store id ("e143313") names nothing a person can see: prose shows the room's name instead."""
    from aura import answer as A
    drawing = {"rooms": [{"room_id": "e143304", "name": "Enclosed Walkway"}, {"room_id": "1.03", "name": "Bad"}]}
    assert A.plain_ids("Multi-Purpose Hall (e143313) is the largest room: 248.23 m².", drawing) == \
        "Multi-Purpose Hall is the largest room: 248.23 m²."
    assert A.plain_ids("Stair (Ground) with ID e143299 connects to room e143304.", drawing) == \
        "Stair (Ground) connects to room Enclosed Walkway."
    assert A.plain_ids("Bad (1.03) is the largest room; see e99.", drawing) == "Bad (1.03) is the largest room; see e99."
    assert A.INTERNAL_ID.fullmatch("e143313") and not A.INTERNAL_ID.fullmatch("1.03") and not A.INTERNAL_ID.fullmatch("E12")
