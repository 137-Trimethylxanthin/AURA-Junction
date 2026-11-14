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
    decl = [{"functionDeclarations": tools.TOOL_DECLARATIONS}]
    contents = [{"role": "user", "parts": [{"text": "how many doors does apartment B have?"}]}]
    g1 = await client.generate(contents, tools=decl, purpose="agent", bucket="dev", system=SYSTEM)
    assert [(f.name, f.args) for f in g1.function_calls] == [
        ("count_objects", {"subject": "door", "scope": "unit:B"})]
    assert g1.model == "gemini-3.1-flash-lite" and g1.input_tokens > 0

    fc = g1.function_calls[0]
    tr = tools.count_objects(d, fc.args["subject"], tools.parse_scope_arg(fc.args["scope"]))
    assert tr.value == 9
    contents += [g1.content, {"role": "user", "parts": [{"functionResponse": {
        "name": fc.name,
        "response": {"result_id": tr.result_id, "value": tr.value, "summary": tr.summary}}}]}]
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


def test_agent_without_a_result_abstains_and_filters_prose(tmp_path):
    with _app(tmp_path) as c:
        b = c.post("/ask", json={"drawing_id": "swiss-46639",
                                 "question": "how many places can people sleep in apartment A"}).json()
        assert b["route"] == "agent" and b["answer"]["reason"] == "unclear_question"
        assert b["explanation"] == ("Beds are not drawn, so sleeping places cannot be counted.")
        assert any(n.get("tool", {}).get("name") == "list_rooms" for n in b["trace"])


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
