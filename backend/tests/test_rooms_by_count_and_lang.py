"""Exact 'rooms with more than N doors' answers, and the question language (local when clear, a model when not)."""
import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aura import language as L
from tests.conftest import make_app

SWISS = "swiss-46639"


def ask(c, q, **kw):
    r = c.post("/ask", json={"drawing_id": SWISS, "question": q, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_rooms_with_more_than_one_door_are_exact_and_consistent(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        listed = ask(c, "mark me all the rooms that have more than 1 door")
        area = ask(c, "give me the cumulative area of all the rooms that have more than 1 door")
        again = ask(c, "what is the total area of the rooms with more than one door")
    for b in (listed, area, again):
        assert b["method"] == "cad_geometry" and b["calls"] == 0
    kept = [r for r in listed["evidence"]["rooms"] if r.get("included")]
    assert kept and listed["answer"]["number"] == len(kept)
    # The area is the sum of exactly the marked rooms, and the same however it is asked.
    assert area["answer"]["number"] == round(sum(r["area_m2"] for r in kept), 2) == again["answer"]["number"]
    assert area["answer"]["unit"] == "m2"
    # Every marked room has at least two doors among the evidence boxes.
    assert len(listed["evidence"]["boxes"]) >= 2


def test_door_filter_answers_in_the_question_language(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        de = ask(c, "Welche Räume haben mehr als eine Tür?")
        fr = ask(c, "Quelles pièces ont plus d'une porte ?")
    assert de["detected_lang"] == "de" and "Tür" in de["answer"]["value"]
    assert fr["detected_lang"] == "fr" and "porte" in fr["answer"]["value"]


def test_language_is_local_when_clear_and_a_model_decides_otherwise():
    assert L.local_verdict("How many doors are there?", "en") == "en"
    assert L.local_verdict("combien de portes", "en") == "fr"
    assert L.local_verdict("alle Fenster", "en") == "de"  # one distinctive word is enough
    assert L.local_verdict("test", "de") == "de"  # nothing to detect: the interface language
    assert L.local_verdict("color in different colors", "en") == "en"  # a tie with the interface language
    assert L.local_verdict("hallo kannst du deutsch", "en") is None  # only a shared 'du': ask the model


def test_the_model_decides_unclear_questions_and_is_cached(monkeypatch):
    calls = []

    class Client:
        def available(self):
            return True

        async def generate(self, prompt, **kw):
            calls.append(prompt)
            return SimpleNamespace(text='{"lang": "de"}')

    monkeypatch.setattr(L, "models_enabled", lambda: True)
    L._cache.clear()
    svc = SimpleNamespace(client=Client(), settings=SimpleNamespace(agent_model="m"))
    caller = SimpleNamespace(ip_hash=None, presenter=False)
    q = "hallo kannst du deutsch"
    assert asyncio.run(L.question_language(svc, q, "en", caller)) == "de"
    assert asyncio.run(L.question_language(svc, q, "en", caller)) == "de"
    assert len(calls) == 1  # the second time from the cache
    assert asyncio.run(L.question_language(svc, "How many doors are there?", "en", caller)) == "en"
    assert len(calls) == 1  # clear questions never call the model


def test_no_model_means_the_interface_language(monkeypatch):
    monkeypatch.setattr(L, "models_enabled", lambda: False)
    L._cache.clear()
    svc = SimpleNamespace(client=SimpleNamespace(available=lambda: True), settings=SimpleNamespace(agent_model="m"))
    assert asyncio.run(L.question_language(svc, "hallo kannst du deutsch", "fi", None)) == "fi"
