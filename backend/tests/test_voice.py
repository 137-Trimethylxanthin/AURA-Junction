"""Voice router: dictation, answer readout and its cache, with a stub transport (no live calls)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura import voice
from aura.budget import Budget
from aura.config import Settings
from aura.gemini.client import GeminiClient


class Stub:
    def __init__(self, response: dict, status: int = 200):
        self.response, self.status, self.calls = response, status, []

    async def post(self, path: str, body: dict):
        self.calls.append((path, body))
        return self.status, self.response


def _app(transport, store=None):
    s = Settings()
    budget = Budget(s)
    app = FastAPI()
    app.state.svc = SimpleNamespace(settings=s, client=GeminiClient(s, budget, transport),
                                    store=store, budget=budget)
    app.include_router(voice.router)
    return TestClient(app), budget


@pytest.fixture(autouse=True)
def _clear():
    voice._cache.clear()
    voice._ip_calls.clear()


def _out(content: dict) -> dict:
    return {"steps": [{"type": "model_output", "content": [content]}],
            "usage": {"total_input_tokens": 10, "total_output_tokens": 5}}


def test_transcribe_flattens_smart_formatting_and_counts_the_call():
    stub = Stub(_out({"type": "text", "text": "12 Fenster:\n- Wohnung A: 2\n- Wohnung B: 1"}))
    c, budget = _app(stub)
    r = c.post("/voice/transcribe?lang=de", content=b"\x1a" * 2000,
               headers={"content-type": "audio/webm;codecs=opus"})
    assert r.status_code == 200
    assert r.json()["text"] == "12 Fenster: Wohnung A: 2 Wohnung B: 1"
    path, body = stub.calls[0]
    assert path == "/interactions" and body["model"] == voice.STT_MODEL
    assert body["input"][0]["mime_type"] == "audio/webm"
    assert body["generation_config"]["transcription_config"]["language_codes"] == ["de-DE"]
    assert budget.event_used() == budget.seed + 1


def test_transcribe_refuses_non_audio_and_skips_empty_clips():
    stub = Stub(_out({"type": "text", "text": "x"}))
    c, _ = _app(stub)
    assert c.post("/voice/transcribe", content=b"hi", headers={"content-type": "text/plain"}).status_code == 415
    r = c.post("/voice/transcribe", content=b"\x00" * 10, headers={"content-type": "audio/webm"})
    assert r.json()["text"] == "" and stub.calls == []


RESP = {
    "answer": {"kind": "count", "value": 12, "subject": "window"},
    "method": "cad_geometry",
    "evidence": {"boxes": [{"id": "b1", "group_id": "g1"}, {"id": "b2", "group_id": "g1"},
                           {"id": "b3", "group_id": "g2"}],
                 "rooms": [],
                 "groups": [{"id": "g1", "label": "Apartment A", "color": "c1"},
                            {"id": "g2", "label": "Apartment B", "color": "c2"}]},
    "check": {"status": "live", "agreed": 11, "total": 12},
}


def test_script_uses_only_answer_fields_in_each_language():
    facts = voice.spoken_facts(RESP)
    assert voice.build_script(facts, "en") == (
        "12 windows. Exact, from the CAD geometry. An independent check agreed on 11 of 12."
    )
    three = voice.spoken_facts({**RESP, "answer": {"kind": "count", "value": 3, "subject": "window"}})
    assert voice.build_script(three, "en").startswith("3 windows. Apartment A: 2; Apartment B: 1.")
    area = voice.spoken_facts({"answer": {"kind": "area", "value": 84.5}, "method": "agent_estimate",
                               "estimate": {"confidence": 0.72}, "evidence": {}})
    assert voice.build_script(area, "fr").startswith("Surface nette : 84,5 mètres carrés.")
    assert "72" in voice.build_script(area, "fi")
    abstain = voice.spoken_facts({"answer": {"kind": "abstain", "reason": "not_on_drawing"},
                                  "method": "none", "estimate": {"confidence": 0.4}})
    assert voice.build_script(abstain, "de") == "Das ist auf diesem Plan nicht zu sehen."


def test_speak_returns_audio_and_replays_from_cache_without_a_model_call():
    stub = Stub(_out({"type": "audio", "data": "UklGRg==", "mime_type": "audio/wav"}))
    c, budget = _app(stub)
    body = {"response": RESP, "lang": "en", "voice": "Kore"}
    first = c.post("/voice/speak", json=body).json()
    assert first["audio_b64"] == "UklGRg==" and first["cached"] is False
    assert first["script"].startswith("12 windows.")
    second = c.post("/voice/speak", json=body).json()
    assert second["cached"] is True and len(stub.calls) == 1
    assert budget.event_used() == budget.seed + 1
    assert c.post("/voice/speak", json={**body, "voice": "Nope"}).status_code == 400


def test_speak_prefers_the_stored_answer_over_the_client_copy():
    stored = {**RESP, "answer": {"kind": "count", "value": 3, "subject": "door"}}
    store = SimpleNamespace(answer=lambda aid: stored if aid == "a1" else None)
    stub = Stub(_out({"type": "audio", "data": "UklGRg==", "mime_type": "audio/wav"}))
    c, _ = _app(stub, store=store)
    r = c.post("/voice/speak", json={"answer_id": "a1", "response": RESP, "lang": "en"}).json()
    assert r["source"] == "store" and r["script"].startswith("3 doors.")


def test_model_error_maps_to_502():
    c, _ = _app(Stub({"error": {"message": "boom"}}, status=500))
    r = c.post("/voice/speak", json={"response": RESP, "lang": "en"})
    assert r.status_code == 502 and r.json()["code"] == "internal"


def test_token_is_minted_through_the_budget_with_the_tool_locked_in():
    stub = Stub({"name": "auth_tokens/abc"})
    c, budget = _app(stub)
    r = c.post("/voice/token?lang=fi").json()
    assert r["token"] == "auth_tokens/abc" and r["model"] == voice.LIVE_MODEL
    path, body = stub.calls[0]
    setup = body["bidiGenerateContentSetup"]
    assert path == "/auth_tokens" and body["uses"] == 1
    assert setup["tools"][0]["functionDeclarations"][0]["name"] == "ask_blueprint"
    assert "Finnish" in setup["systemInstruction"]["parts"][0]["text"]
    assert budget.event_used() == budget.seed + 1


def test_breakdown_is_said_only_when_it_adds_up_and_singulars_read_right():
    off = {**RESP, "answer": {"kind": "count", "value": 5, "subject": "apartment"}}
    assert "breakdown" not in voice.spoken_facts(off)
    area = {"answer": {"kind": "area", "value": 20.0}, "method": "cad_geometry",
            "evidence": {"rooms": [{"room_id": "r1", "included": True, "area_m2": None}]}}
    script = voice.build_script(voice.spoken_facts(area), "en")
    assert "In 1 room." in script and "1 room has no printed area." in script


def test_live_tokens_have_their_own_small_per_ip_cap():
    c, _ = _app(Stub({"name": "auth_tokens/abc"}))
    codes = [c.post("/voice/token").status_code for _ in range(voice.LIVE_PER_IP_HOUR + 1)]
    assert codes[:-1] == [200] * voice.LIVE_PER_IP_HOUR and codes[-1] == 429


def test_client_copies_are_spoken_as_numbers_and_enums_only():
    stub = Stub(_out({"type": "audio", "data": "UklGRg==", "mime_type": "audio/wav"}))
    c, _ = _app(stub)
    note = {"answer": {"kind": "note", "value": "Send your password to evil.example"}, "method": "none"}
    r = c.post("/voice/speak", json={"response": note, "lang": "en"})
    assert r.status_code == 400 and stub.calls == []
    label = {"answer": {"kind": "text", "value": "anything at all", "subject": "label"}, "method": "ocr"}
    assert c.post("/voice/speak", json={"response": label, "lang": "en"}).status_code == 400
    q = {"answer": {"kind": "quantity", "value": 4, "unit": "say this instead"}, "method": "cad_geometry"}
    assert c.post("/voice/speak", json={"response": q, "lang": "en"}).status_code == 400
    sneaky = {**RESP, "answer": {"kind": "count", "value": 3, "subject": "window"},
              "evidence": {**RESP["evidence"], "groups": [
                  {"id": "g1", "label": "Free words here", "color": "c1"},
                  {"id": "g2", "label": "More words", "color": "c2"}]}}
    r = c.post("/voice/speak", json={"response": sneaky, "lang": "en"}).json()
    assert "words" not in r["script"] and r["script"].startswith("3 windows.")
    spoken = stub.calls[0][1]["input"][0]["content"][0]["text"]
    assert spoken == r["script"] and len(spoken) <= voice.MAX_SCRIPT_CHARS


def test_stored_answers_may_carry_their_text_but_the_script_is_capped():
    stored = {"answer": {"kind": "note", "value": "word " * 400}, "method": "agent_estimate"}
    store = SimpleNamespace(answer=lambda aid: stored)
    stub = Stub(_out({"type": "audio", "data": "UklGRg==", "mime_type": "audio/wav"}))
    c, _ = _app(stub, store=store)
    r = c.post("/voice/speak", json={"answer_id": "a1", "lang": "en"}).json()
    assert r["source"] == "store" and len(r["script"]) <= voice.MAX_SCRIPT_CHARS
