"""The recorded function-calling verification on gemini-3.1-flash-lite (2 live requests, P1),
replayed through GeminiClient with FakeTransport."""

from __future__ import annotations

import json
import re

import pytest

from aura import tools
from aura.budget import Budget
from aura.config import Settings
from aura.gemini.client import FakeTransport, GeminiClient
from tests.conftest import FIXTURES, FRONTEND_DRAWING

SYSTEM = ("You answer questions about one floor plan by calling tools. Never write a number "
          "yourself; every number must come from a tool result. Finish with final_answer.")


@pytest.fixture
def client():
    s = Settings()
    return GeminiClient(s, Budget(s), FakeTransport(FIXTURES / "gemini"))


async def test_flash_lite_function_calling_round_trip(client):
    d = tools.load_file(FRONTEND_DRAWING)
    # The declarations as they were sent when this exchange was recorded.
    decl = json.loads((FIXTURES / "gemini" / "fc_turn1.json").read_text())["request"]["tools"]
    contents = [{"role": "user", "parts": [{"text": "how many doors does apartment B have?"}]}]
    g1 = await client.generate(contents, tools=decl, purpose="agent", bucket="dev", system=SYSTEM)
    assert [(f.name, f.args) for f in g1.function_calls] == [
        ("count_objects", {"subject": "door", "scope": "unit:B"})]
    assert g1.model == "gemini-3.1-flash-lite" and g1.input_tokens > 0

    fc = g1.function_calls[0]
    tr = tools.count_objects(d, fc.args["subject"], tools.parse_scope_arg(fc.args["scope"]))
    assert tr.value == 9
    # Replay the function response exactly as it was sent when recorded (summary wording has
    # changed since); its result_id and value must still equal today's tool result.
    sent = json.loads((FIXTURES / "gemini" / "fc_turn2.json").read_text())["request"]["contents"][2]
    reply = sent["parts"][0]["functionResponse"]["response"]
    assert (reply["result_id"], reply["value"]) == (tr.result_id, tr.value)
    contents += [g1.content, sent]
    g2 = await client.generate(contents, tools=decl, purpose="agent", bucket="dev", system=SYSTEM)
    (final,) = g2.function_calls
    assert final.name == "final_answer" and final.args["result_id"] == tr.result_id
    # Every number in the model's explanation is in the tool result.
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", final.args["explanation"])]
    assert nums and all(n in [*tr.numbers, tr.value] for n in nums)
    assert client.budget.event_used() == 94 + 2


def test_fixtures_hold_no_image_bytes_or_key():
    for f in (FIXTURES / "gemini").glob("*.json"):
        text = f.read_text()
        fx = json.loads(text)
        assert fx["source"] in ("recorded", "derived", "synthetic"), f.name
        assert "x-goog-api-key" not in text.lower() and "AIza" not in text
        for part in fx["request"].get("input", []):
            if part.get("type") == "image":
                assert part["data"].startswith("sha256:")


# ---- agent inside the pipeline (synthetic fixtures, see their "note") ---------------------

def _app(tmp_path):
    from fastapi.testclient import TestClient

    from tests.conftest import make_app

    return TestClient(make_app(tmp_path, transport=FakeTransport(FIXTURES / "gemini"),
                               with_image=True))


def test_unsure_choice_hands_over_to_the_agent(tmp_path):
    with _app(tmp_path) as c:
        b = c.post("/ask", json={"drawing_id": "swiss-46639", "question":
                                 "how many ways in and out are there for flat B"}).json()
        assert b["route"] == "agent"
        assert b["answer"] == {"kind": "count", "value": 9, "subject": "door"}
        assert b["scope"] == {"kind": "unit", "unit": "B"} and len(b["evidence"]["boxes"]) == 9
        assert b["calls"] == 1 + 2  # one choice + two function-calling iterations
        # Provenance: "40 percent" is in no tool result, so that sentence is gone.
        assert b["explanation"] == "Apartment B has 9 doors."
        assert any(n["id"] == "provenance" and n["kind"] == "warn" for n in b["trace"])
        assert next(n for n in b["trace"] if n["id"] == "intent")["tag"].startswith("agent")


def test_agent_names_a_room_list_with_a_label_and_gets_that_count(tmp_path):
    """RECORDED live (choice replayed + 2 flash-lite calls, Sat 14 Nov afternoon, with the
    final_answer `label` and the room labels in a quoted user-turn data block): the agent called list_rooms(unit:A), then named that list
    with label 'Chambre à coucher'. The value 4 is the tool's per-label count (rooms 3.04, 3.05,
    3.07, 3.10 as evidence), never the model's number."""
    with _app(tmp_path) as c:
        b = c.post("/ask", json={"drawing_id": "swiss-46639",
                                 "question": "how many places can people sleep in apartment A"}).json()
        assert b["route"] == "agent" and b["calls"] == 3
        assert b["answer"] == {"kind": "count", "value": 4, "subject": "room"}
        assert [r["room_id"] for r in b["evidence"]["rooms"]] == ["3.04", "3.05", "3.07", "3.10"]
        assert "Chambre à coucher" in b["canonical_en"] and "4 rooms" in b["explanation"]
        assert not any(n["id"] == "provenance" for n in b["trace"])


def test_chat_streams_agent_tool_calls(tmp_path):
    import json as _json

    with _app(tmp_path) as c, c.stream("POST", "/chat", json={
            "drawing_id": "swiss-46639",
            "message": "how many ways in and out are there for flat B"}) as r:
        text = "".join(r.iter_text())
    events = [blk.split("\n")[0][7:] for blk in text.strip().split("\n\n")]
    assert events[:2] == ["turn", "understood"] and events[-2:] == ["answer", "done"]
    und = _json.loads(text.split("\n\n")[1].split("\n")[1][6:])
    assert und["route"] == "agent"
    assert "tool_call" in events


def test_provenance_numbers_and_words():
    from aura.provenance import filter_prose, numbers_in

    assert numbers_in("22 doors, 139,34 m², zweiundzwanzig, twenty-two, douze") == [
        22.0, 139.34, 22.0, 22.0, 12.0]
    kept, dropped = filter_prose("There are twelve windows. About 13 are big.", {12.0})
    assert kept == "There are twelve windows." and dropped == ["About 13 are big."]
    # rounding of a tool value is fine; the user's room number is allowed via the question
    kept, _ = filter_prose("Room 3.04 has 17.0 m².", {17.04, 3.04})
    assert kept == "Room 3.04 has 17.0 m²."


def test_rewrite_blocks_recorded(tmp_path):
    with _app(tmp_path) as c:
        r = c.post("/rewrite", json={"drawing_id": "swiss-46639", "text": (
            "uh so I'm renovating, how many sinks are there in both flats together, and the "
            "bedroom 3.05 how big is it, also is the ceiling high enough for a loft bed")})
        assert r.status_code == 200
        b = r.json()
        assert b["calls"] == 1 and b["cost_usd"] > 0 and b["model"] == "gemini-3.1-flash-lite"
        assert [x["scope"] for x in b["blocks"]] == [{"kind": "floor"},
                                                     {"kind": "room", "room_id": "3.05"}]
        assert all(x["routable"] for x in b["blocks"])
        assert b["not_answerable"] == ["is the ceiling high enough for a loft bed"]
        # Confirming a block: /ask with the block's question AND scope (scope wins).
        a = c.post("/ask", json={"drawing_id": "swiss-46639", "question": b["blocks"][0]["question"],
                                 "scope": b["blocks"][0]["scope"]}).json()
        assert a["answer"] == {"kind": "count", "value": 5, "subject": "washbasin"}
        de = c.post("/rewrite", json={"drawing_id": "swiss-46639", "text":
                                      "Wie viele Fenster hat die Wohnung B und wie gross ist die Küche?"}).json()
        assert de["detected_lang"] == "de"
        assert de["blocks"][1]["scope"] == {"kind": "label", "label": "Cuisine"}


def test_rewrite_refused_when_models_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_MODELS", "off")
    with _app(tmp_path) as c:
        r = c.post("/rewrite", json={"drawing_id": "swiss-46639", "text": "how many doors"})
        assert r.status_code == 429 and r.json()["code"] == "budget_exhausted"


def test_find_text_is_literal_no_redos():
    import time

    from aura.router import FIRE_PATTERN

    data = json.loads(FRONTEND_DRAWING.read_text())
    data["texts"] = [{"id": "text-01", "text": "a" * 5000 + "!", "x": 1, "y": 1, "w": 1, "h": 1},
                     {"id": "text-02", "text": "EI30", "x": 2, "y": 2, "w": 1, "h": 1},
                     {"id": "text-03", "text": "20 MIN.", "x": 3, "y": 3, "w": 1, "h": 1}]
    d = tools.LoadedDrawing(id="t", dir=None, drawing=data, engine=tools.BundleEngine(data))
    t0 = time.perf_counter()
    r = tools.find_text_literal(d, "(a+)+$")
    assert time.perf_counter() - t0 < 0.5 and r.text is None
    from aura.agent.loop import execute

    assert execute(d, "find_text", {"pattern": "(a+)+$"}).text is None
    assert execute(d, "find_text", {"pattern": "ei30"}).text == "EI30"
    fire = tools.find_text(d, FIRE_PATTERN, "fire_rating")
    assert fire.meta["texts"] == ["EI30", "20 MIN."]


def test_room_count_by_label_and_scope_is_tool_produced():
    from aura.agent.loop import execute

    d = tools.load_file(FRONTEND_DRAWING)
    r = execute(d, "count_objects", {"subject": "room", "label": "chambre à coucher",
                                     "scope": "unit:A"})
    assert r.value == 4 and [x.room_id for x in r.rooms] == ["3.04", "3.05", "3.07", "3.10"]
    lst = execute(d, "list_rooms", {"scope": "unit:A"})
    assert lst.meta["by_label"]["Chambre à coucher"] == 4 and 4.0 in lst.numbers


class _Scripted:
    """A stand-in client that replays a fixed list of function calls (no transport at all)."""

    def __init__(self, turns):
        self.turns = list(turns)

    async def generate(self, contents, **kw):
        from aura.gemini.client import FunctionCall, GenerateResult

        calls = [FunctionCall(n, a) for n, a in self.turns.pop(0)]
        return GenerateResult(text="", function_calls=calls, input_tokens=1, output_tokens=1,
                              cost_usd=0.0, latency_ms=0, model="stub",
                              content={"role": "model", "parts": []})


async def test_final_answer_on_a_room_list_with_a_label_counts_that_label():
    """Offline path: list_rooms(unit:A), then final_answer naming that list with a label.
    The value is the tool's per-label count, with those rooms as evidence."""
    from aura.agent.loop import run_agent

    d = tools.load_file(FRONTEND_DRAWING)
    rid = tools.list_rooms(d, {"kind": "unit", "unit": "A"}).result_id
    stub = _Scripted([[("list_rooms", {"scope": "unit:A"})],
                      [("final_answer", {"result_id": rid, "label": "Chambre à coucher",
                                         "explanation": "4 rooms are bedrooms."})]])
    ar = await run_agent(stub, d, "how many places can people sleep in apartment A")
    assert ar.final.kind == "count" and ar.final.value == 4
    assert [r.room_id for r in ar.final.rooms] == ["3.04", "3.05", "3.07", "3.10"]
    # Without a label (or with one that is not on the drawing) the list is not an answer.
    stub = _Scripted([[("list_rooms", {"scope": "unit:A"})],
                      [("final_answer", {"result_id": rid, "label": "Sauna",
                                         "explanation": "none"})]])
    assert (await run_agent(stub, d, "saunas in A?")).final.kind == "rooms"


async def test_agent_rejects_an_unknown_scope_instead_of_widening():
    from aura.agent.loop import execute

    d = tools.load_file(FRONTEND_DRAWING)
    assert execute(d, "count_objects", {"subject": "door", "scope": "bedroom"}) is None
    assert execute(d, "count_objects", {"subject": "door", "scope": "apartment:B"}).args[
        "scope"] == {"kind": "unit", "unit": "B"}
    # 'model' exists only for IFC models: on a single plan it is refused, never the whole floor.
    for name, args in (("list_rooms", {}), ("count_objects", {"subject": "door"}),
                       ("count_objects", {"subject": "room", "label": "Bain"}), ("room_areas", {})):
        assert execute(d, name, {**args, "scope": "model"}) is None, name
    assert tools.list_rooms(d, {"kind": "model"}).supported is False


def test_provenance_never_whitelists_model_input_or_user_numbers():
    """M1 + F5: numbers the model typed into tool args/labels, or the user typed into the
    question, are not tool results; only room numbers on the drawing may be quoted back."""
    from aura.pipeline import identifiers_in
    from aura.provenance import allowed_numbers, filter_prose

    d = tools.load_file(FRONTEND_DRAWING)
    miss = tools.find_text_literal(d, "57 doors")  # zero hits; the pattern echoes 57
    lab = tools.count_rooms_labelled(d, "Bain 99", {"kind": "unit", "unit": "A"})
    allowed = allowed_numbers([miss, lab], identifiers_in("Is room 3.04 20 m² like I said?", d))
    assert 57.0 not in allowed and 99.0 not in allowed and 20.0 not in allowed
    assert 3.04 in allowed
    for bad in ("There are 57 doors.", "The room is 20 m².", "x57 doors.", "_57_ doors.",
                "cinquantasette porte.", "neljäkymmentäkaksi ovea.", "a dozen doors.",
                "LVII doors.", "twenty one doors.", "doce puertas."):
        assert filter_prose(bad, allowed)[0] == "", bad
    rooms = tools.room_areas(d, {"kind": "room", "room_id": "3.05"})
    ok = allowed_numbers([rooms])
    assert filter_prose("Room 3.05 has 13.81 m2.", ok)[0]


def test_unsure_subject_goes_to_the_agent_not_to_geometry(tmp_path, monkeypatch):
    """F7: intent 'count' at 0.9 but subject 'washbasin' at 0.35 must not answer the
    washbasin count as exact; the agent decides (here stubbed to record that it ran)."""
    from aura import answer as A
    from aura import pipeline
    from aura.gemini.understand import Understood

    async def fake_understand(*a, **k):
        return Understood(intent="count", intent_conf=0.9, subject="washbasin", subject_conf=0.35,
                          scope=None, scope_conf=0.0, on_drawing=0.9)

    ran = []

    async def fake_agent(svc, d, question, lang, caller, stats, history, tag):
        ran.append(tag)
        return A.unclear(question, lang, d, intent_tag="agent", look="stub")

    monkeypatch.setattr(pipeline, "understand", fake_understand)
    monkeypatch.setattr(pipeline, "_agent", fake_agent)
    with _app(tmp_path) as c:
        b = c.post("/ask", json={"drawing_id": "swiss-46639",
                                 "question": "how many lavatories are there?"}).json()
    assert ran and b["answer"]["kind"] == "abstain"
