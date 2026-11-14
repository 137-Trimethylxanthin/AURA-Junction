"""Regression battery: simple questions are answered exactly, instantly, with 0 model calls.

Bare subjects ("all doors", "doors"), any display verb (show, highlight, list, give me, which
are there ...) in EN/FR/DE/FI/IT must resolve to the exact template in the hybrid answerer; an
exact-answerable question never waits on gemini-decision-1, the image check or the agent.
"""
from __future__ import annotations

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
