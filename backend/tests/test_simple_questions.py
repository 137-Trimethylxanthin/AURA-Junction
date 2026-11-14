"""Regression battery: simple questions are answered exactly, instantly, with 0 model calls.

Bare subjects ("all doors", "doors"), any display verb (show, highlight, list, give me, which
are there ...) in EN/FR/DE/FI/IT must resolve to the exact template in the hybrid answerer; an
exact-answerable question never waits on gemini-decision-1, the image check or the agent.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from aura.router import DrawingCtx, route
from tests.test_agent_client import (
    SWISS,
    MockGeminiDecisionsTransport,
    MockSidecarState,
    make_hybrid_app,
)

BATTERY: list[tuple[str, str, str | None]] = [  # (question, answer kind, count subject)
    # doors
    ("all doors", "count", "door"), ("doors", "count", "door"), ("doors?", "count", "door"),
    ("highlight all doors", "count", "door"), ("show all doors", "count", "door"),
    ("how many doors are there", "count", "door"), ("mark the doors", "count", "door"),
    ("give me the doors", "count", "door"), ("point out the doors", "count", "door"),
    ("which doors are there", "count", "door"), ("list all doors", "count", "door"),
    ("montre toutes les portes", "count", "door"), ("toutes les portes", "count", "door"),
    ("zeige alle Türen", "count", "door"), ("alle Türen", "count", "door"),
    ("näytä kaikki ovet", "count", "door"), ("kaikki ovet", "count", "door"),
    ("tutte le porte", "count", "door"),
    # windows
    ("windows?", "count", "window"), ("all windows", "count", "window"),
    ("how many windows", "count", "window"), ("show me all windows", "count", "window"),
    ("display the windows", "count", "window"), ("every window", "count", "window"),
    ("combien de fenêtres", "count", "window"), ("wie viele Fenster gibt es", "count", "window"),
    ("montako ikkunaa", "count", "window"), ("alle Fenster", "count", "window"),
    # rooms and areas
    ("list all rooms", "count", "room"), ("all rooms", "count", "room"), ("rooms", "count", "room"),
    ("which rooms are there", "count", "room"), ("how many rooms", "count", "room"),
    ("display all rooms", "count", "room"), ("liste toutes les pièces", "count", "room"),
    ("total floor area", "area", None), ("what is the total area", "area", None),
    ("surface totale", "area", None), ("Gesamtfläche", "area", None),
    ("what is the biggest room", "note", None), ("smallest room", "note", None),
    # kitchens / bathrooms
    ("where is the kitchen", "count", "room"), ("show the kitchen", "count", "room"),
    ("show all bathrooms", "count", "room"), ("list the bathrooms", "count", "room"),
    ("où est la cuisine", "count", "room"), ("wo ist die Küche", "count", "room"),
    # apartments
    ("how many apartments are there", "count", "apartment"), ("show the apartments", "count", "apartment"),
    ("combien d'appartements", "count", "apartment"), ("wie viele Wohnungen", "count", "apartment"),
    # fixtures
    ("where are the toilets", "count", "wc"), ("all washbasins", "count", "washbasin"),
]


@pytest.fixture(scope="module")
def hybrid(tmp_path_factory):
    sc, gem = MockSidecarState(), MockGeminiDecisionsTransport()
    with TestClient(make_hybrid_app(tmp_path_factory.mktemp("battery"), sidecar=sc, gemini=gem)) as c:
        yield c, sc, gem


@pytest.mark.parametrize(("q", "kind", "subject"), BATTERY)
def test_simple_question_is_exact_instant_and_model_free(hybrid, q, kind, subject):
    c, sc, gem = hybrid
    runs, gem_calls = len(sc.runs), len(gem.calls)
    r = c.post("/ask", json={"drawing_id": SWISS, "question": q})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["route"] == "template", (q, b["route"], b["answer"])
    assert b["answer"]["kind"] == kind, (q, b["answer"])
    if subject:
        assert b["answer"]["subject"] == subject, (q, b["answer"])
    assert b["calls"] == 0 and b["method"] == "cad_geometry"
    assert b["latency_ms"] < 200, (q, b["latency_ms"])
    # No agent run and no routing/understanding model call. (A first image check of a layer may
    # run in the background after the answer; it never blocks it.)
    routing = [b for _, b in gem.calls[gem_calls:]
               if {"on_drawing", "exact_sufficient", "intent", "cache_match"} & set(b.get("questions") or {})]
    assert len(sc.runs) == runs and routing == []


def test_battery_is_big_enough():
    assert len(BATTERY) >= 40


@pytest.mark.parametrize("q", ["show the staircase", "where is the stairwell", "wo ist das Treppenhaus",
                               "où est la cage d'escalier"])
def test_stair_room_questions_are_exact_when_the_plan_labels_one(q):
    ctx = DrawingCtx(labels=["Cage d'escalier", "Cuisine"], room_ids=["2.01"], units=["A"])
    r = route(q, ctx)
    assert r.intent == "count" and r.scope == {"kind": "label", "label": "Cage d'escalier"}


def test_stair_connectivity_still_needs_the_agent():
    ctx = DrawingCtx(labels=["Cage d'escalier"], room_ids=["2.01"], units=["A"])
    assert route("where do the stairs lead", ctx).intent == "unknown"
    assert route("show all stairs and where they lead to", ctx).intent == "unknown"


def test_cached_agent_answer_never_outranks_an_exact_route(tmp_path):
    """A model estimate cached for a question that the router now answers exactly is ignored."""
    sc = MockSidecarState()
    with TestClient(make_hybrid_app(tmp_path, sidecar=sc)) as c:
        svc = c.app.state.svc
        first = c.post("/ask", json={"drawing_id": SWISS, "question": "what is the biggest room"}).json()
        # Make every cached entry look like an old agent estimate for the same question.
        assert svc.answers._d
        for k in list(svc.answers._d):
            svc.answers._d[k] = svc.answers._d[k] | {"method": "agent_estimate"}
        again = c.post("/ask", json={"drawing_id": SWISS, "question": "what is the biggest room"}).json()
        assert first["method"] == again["method"] == "cad_geometry"
        assert again["route"] == "template"


def test_cached_sidecar_answer_never_outranks_the_tool_tier(tmp_path, monkeypatch):
    """A cached agent_estimate (sidecar) is replayed only after the tool-step answerer declined."""
    import aura.pipeline as P
    from aura import answer as A
    from aura import tools

    q = "show only doors that are in rooms that have toilets"
    sc = MockSidecarState()

    async def declines(*a, **k):
        return None

    async def answers(svc, d, question, lang, caller, stats, history=None, scope=None, **k):
        stats["route"] = "choice"
        tr = tools.count_objects(d, "door", {"kind": "floor"})
        return A.count(question, lang, d, tr, {"kind": "floor"}, None, intent_tag="tool steps")

    with TestClient(make_hybrid_app(tmp_path, sidecar=sc)) as c:
        monkeypatch.setattr(P, "answer_with_tools", declines)
        first = c.post("/ask", json={"drawing_id": SWISS, "question": q}).json()
        assert first["method"] == "agent_estimate" and len(sc.runs) == 1

        monkeypatch.setattr(P, "answer_with_tools", answers)
        tools_r = c.post("/ask", json={"drawing_id": SWISS, "question": q}).json()
        assert tools_r["method"] == "cad_geometry" and tools_r["route"] == "choice"

        monkeypatch.setattr(P, "answer_with_tools", declines)
        replay = c.post("/ask", json={"drawing_id": SWISS, "question": q}).json()
        assert replay["route"] == "cache" and replay["method"] == "agent_estimate"
        assert len(sc.runs) == 1  # the cached sidecar answer replaced a new sidecar run


def test_without_sidecar_tools_answer_and_the_rest_abstains_honestly(tmp_path, monkeypatch):
    """The plan's path with the sidecar retired: tool steps answer; else an honest abstain."""
    import aura.pipeline as P
    from aura import answer as A
    from aura import tools

    sc = MockSidecarState()

    async def answers(svc, d, question, lang, caller, stats, history=None, scope=None, **k):
        stats["route"] = "choice"
        tr = tools.count_objects(d, "door", {"kind": "floor"})
        return A.count(question, lang, d, tr, {"kind": "floor"}, None, intent_tag="tool steps")

    async def declines(*a, **k):
        return None

    with TestClient(make_hybrid_app(tmp_path, sidecar=sc)) as c:
        c.app.state.svc.agent = None  # AURA_SIDECAR_URL unset
        c.app.state.drawing_store.ok = True  # full mode (the store's own calls are not reached here)
        monkeypatch.setattr(P, "answer_with_tools", answers)
        r = c.post("/ask", json={"drawing_id": SWISS, "question": "doors next to something odd"}).json()
        assert r["route"] == "choice" and r["method"] == "cad_geometry"
        monkeypatch.setattr(P, "answer_with_tools", declines)
        r = c.post("/ask", json={"drawing_id": SWISS, "question": "what colour is the front door"}).json()
        assert r["answer"]["kind"] == "abstain" and "agent_failure" not in r
        assert r["answer"]["reason"] in ("not_answerable", "not_on_drawing")
        r = c.post("/ask", json={"drawing_id": SWISS, "question": "how many lifts lead to the roof garden"}).json()
        assert r["answer"] == {"kind": "abstain", "reason": "not_answerable"} or r["answer"]["kind"] == "abstain"
        assert sc.runs == []  # no agent run at all
        for q, kind, _ in BATTERY:  # exact questions are unchanged without the sidecar
            b = c.post("/ask", json={"drawing_id": SWISS, "question": q}).json()
            assert b["route"] in ("template", "cache") and b["answer"]["kind"] == kind, (q, b["answer"])


@pytest.mark.parametrize("q", ["which rooms have a door wider than 0.92 m",
                               "which of them are wider than 0.95 m",
                               "which rooms are larger than 15.5 m²",
                               "which bedrooms are bigger than 11 m2 on this plan",
                               "show only doors that are in rooms that have toilets"])
def test_qualified_filters_are_for_the_tool_tier_not_a_template_abstain(q):
    r = route(q)
    assert r.intent == "unknown", (q, r.intent, r.matched)


def test_gate_off_drawing_verdict_does_not_stop_the_tool_tier_on_an_indexed_drawing(tmp_path, monkeypatch):
    """gemini-decision-1 said 'not shown on the drawing' (p = 0.24) on an answerable filter: the
    tool steps still try first, because the drawing has rooms indexed."""
    import aura.pipeline as P
    from aura import answer as A
    from aura import tools

    def off(path, body):
        q = body.get("questions") or {}
        if "on_drawing" not in q:
            return None
        ans = {k: {"choice": "other", "confidence": 0.9, "probabilities": {"other": 0.9}}
               for k in ("intent", "subject", "scope", "cache_match") if k in q}
        ans |= {"on_drawing": {"noul": 0.24}, "exact_sufficient": {"noul": 0.05}}
        return 200, {"answers": ans, "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1}}

    async def answers(svc, d, question, lang, caller, stats, history=None, scope=None, **k):
        stats["route"] = "choice"
        tr = tools.room_areas(d, {"kind": "floor"})
        return A.area(question, lang, d, tr, {"kind": "floor"}, None, intent_tag="tool steps")

    sc = MockSidecarState()
    with TestClient(make_hybrid_app(tmp_path, sidecar=sc, gemini=MockGeminiDecisionsTransport(off))) as c:
        monkeypatch.setattr(P, "answer_with_tools", answers)
        r = c.post("/ask", json={"drawing_id": SWISS, "question": "which rooms are larger than 15.5 m²"}).json()
        assert r["route"] == "choice" and r["answer"]["kind"] == "area"
        assert sc.runs == []


def test_cached_abstains_are_never_offered_as_the_same_question(tmp_path):
    """The gate's semantic cache sees only real answers of the current router version."""
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        svc = c.app.state.svc
        from aura.router import ROUTER_VERSION
        old = f'["{SWISS}", "q old", null, null, null, true]|router:{ROUTER_VERSION - 1}'
        cur_abs = f'["{SWISS}", "q abs", null, null, null, true]|router:{ROUTER_VERSION}'
        svc.answers.put(old, {"answer": {"kind": "count", "value": 1}}, drawing_id=SWISS, question="q old")
        svc.answers.put(cur_abs, {"answer": {"kind": "abstain", "reason": "x"}}, drawing_id=SWISS, question="q abs")
        gem = c.app.state.svc.client.transport
        before = len(gem.calls)
        c.post("/ask", json={"drawing_id": SWISS, "question": "tell me something unusual about the doors"})
        sent = [b for _, b in gem.calls[before:] if "cache_match" in (b.get("questions") or {})]
        assert all("q old" not in json.dumps(b) and "q abs" not in json.dumps(b) for b in sent)


@pytest.mark.parametrize("q", ["which rooms are larger than 15.5 m²", "which rooms have a door wider than 0.92 m",
                               "which of them are wider than 0.95 m", "rooms over 12.5"])
def test_measurements_are_never_read_as_room_ids(q):
    """'15.5 m²' is a threshold, not room 15.5: a room scope that does not exist would make the tool
    steps decline (measured live)."""
    ctx = DrawingCtx(labels=["Bad"], room_ids=["3.05"], units=["A"])
    assert route(q, ctx).scope is None
    assert route("what is the area of room 3.05", ctx).scope == {"kind": "room", "room_id": "3.05"}


def test_tool_tier_abstain_is_not_answerable_and_router_topics_stay_not_on_drawing(tmp_path, monkeypatch):
    """Rule: tools tried and empty -> not_answerable; a router topic (ceiling height) that never
    reaches the tools -> not_on_drawing."""
    import aura.pipeline as P
    from aura import answer as A

    async def tool_abstain(svc, d, question, lang, caller, stats, history=None, scope=None, **k):
        stats["route"] = "choice"
        return A.abstain(question, lang, {"kind": "floor"}, "not_on_drawing", question,
                         "I cannot answer this from the drawing's data: nothing matched.")

    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        c.app.state.svc.agent = None
        c.app.state.drawing_store.ok = True
        monkeypatch.setattr(P, "answer_with_tools", tool_abstain)
        r = c.post("/ask", json={"drawing_id": SWISS, "question": "how far is it to walk from the kitchen to the bath"}).json()
        assert r["answer"] == {"kind": "abstain", "reason": "not_answerable"}
        h = c.post("/ask", json={"drawing_id": SWISS, "question": "what is the ceiling height in the kitchen"}).json()
        assert h["answer"]["reason"] == "not_on_drawing"
