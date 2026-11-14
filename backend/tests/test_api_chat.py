"""POST /chat (SSE) on the shared pipeline, and GET /conversations/:id."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CONFORMANCE, make_app, no_nulls

SWISS = "swiss-46639"
CASES = json.loads(CONFORMANCE.read_text())["cases"][:20]
SAME = ("answer", "evidence", "scope", "gold", "method", "canonical_en", "detected_lang", "check")


def sse(c: TestClient, body: dict) -> list[tuple[str, dict]]:
    with c.stream("POST", "/chat", json=body) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_event_order_and_answer(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        ev = sse(c, {"drawing_id": SWISS, "message": "How many windows are drawn in apartment A?"})
        names = [e for e, _ in ev]
        assert names[0] == "turn" and names[1] == "understood" and names[-1] == "done"
        assert names[-2] == "answer"
        mid = names[2:-2]
        assert mid and set(mid) <= {"tool_call", "tool_result", "check"}
        assert mid[0] == "tool_call" and "tool_result" in mid
        call = ev[2][1]["node"]
        assert call["tool"]["name"] == "count_objects" and call["tool"]["args"]["subject"] == "window"
        res = ev[3][1]
        assert res["node_id"] == call["id"] and len(res["evidence"]["boxes"]) == 7
        resp = ev[-2][1]["response"]
        no_nulls(resp)
        turn = ev[0][1]
        assert resp["conversation_id"] == turn["conversation_id"]
        assert resp["turn_id"] == turn["turn_id"]
        assert resp["answer"] == {"kind": "count", "value": 7, "subject": "window"}
        assert ev[1][1] == {"detected_lang": "en", "canonical_en": resp["canonical_en"],
                            "route": "template"}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_ask_and_chat_give_identical_answers(tmp_path, case):
    req = {k: case[k] for k in ("scope", "overrides") if k in case}
    with TestClient(make_app(tmp_path / "a")) as c1:
        a = c1.post("/ask", json={"drawing_id": SWISS, "question": case["question"], **req}).json()
    with TestClient(make_app(tmp_path / "b")) as c2:
        ev = sse(c2, {"drawing_id": SWISS, "message": case["question"], **req})
    b = dict(ev)["answer"]["response"]
    for k in SAME:
        assert a.get(k) == b.get(k), k


def test_conversation_restore_and_follow_up(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        first = sse(c, {"drawing_id": SWISS, "message": "How many doors are in apartment A?"})
        conv = first[0][1]["conversation_id"]
        a1 = dict(first)["answer"]["response"]
        follow = sse(c, {"drawing_id": SWISS, "conversation_id": conv,
                         "message": "and in apartment B?"})
        a2 = dict(follow)["answer"]["response"]
        assert a1["answer"]["subject"] == a2["answer"]["subject"] == "door"
        assert a2["scope"] == {"kind": "unit", "unit": "B"}
        assert a1["answer"]["value"] + a2["answer"]["value"] <= 22
        assert "follow-up" in next(n for n in a2["trace"] if n["id"] == "intent")["tag"]
        windows = sse(c, {"drawing_id": SWISS, "conversation_id": conv, "message": "and windows?"})
        a3 = dict(windows)["answer"]["response"]
        assert a3["answer"]["subject"] == "window" and a3["scope"] == {"kind": "unit", "unit": "B"}
        c.portal.call(c.app.state.svc.writer.flush)
        r = c.get(f"/conversations/{conv}").json()
        assert r["contract_version"] == "1.1" and r["drawing_id"] == SWISS
        assert [t["message"] for t in r["turns"]] == [
            "How many doors are in apartment A?", "and in apartment B?", "and windows?"]
        assert r["turns"][1]["response"]["answer"] == a2["answer"]
        assert c.get("/conversations/c-nope").status_code == 404
        other = c.post("/chat", json={"drawing_id": "swiss-404", "message": "hi"})
        assert other.status_code == 404


class _Slow:
    async def post(self, path, body):
        import asyncio

        await asyncio.sleep(5)
        raise AssertionError("unreachable")


def test_turn_deadline_ends_with_timeout_then_done(tmp_path):
    app = make_app(tmp_path, transport=_Slow(), AURA_TURN_DEADLINE_S=0.3)
    with TestClient(app) as c:
        ev = sse(c, {"drawing_id": SWISS, "message": "tally the lavatory fittings"})
        names = [e for e, _ in ev]
        assert names[0] == "turn" and names[-2:] == ["error", "done"]
        assert ev[-2][1]["code"] == "timeout"
        c.portal.call(c.app.state.svc.writer.flush)
        turns = c.get(f"/conversations/{ev[0][1]['conversation_id']}").json()["turns"]
        assert turns[0]["error"]["code"] == "timeout" and "response" not in turns[0]


def test_unknown_drawing_errors_before_streaming(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        r = c.post("/chat", json={"drawing_id": "nope", "message": "How many doors?"})
        assert r.status_code == 404 and r.json()["code"] == "unknown_drawing"
