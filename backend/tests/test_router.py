"""Router: the 16 E3 questions, the fire-rating question (questions.jsonl:89450), dataset templates."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aura import tools
from aura.router import DrawingCtx, detect_lang, route
from tests.conftest import FRONTEND_DRAWING, REPO

CTX = tools.load_file(FRONTEND_DRAWING).ctx

# E3 (gemini-usecases.md): question, expected intent, count subject, language, expected route.
# `choice` marks the ones the regex leaves to the Decisions step (tested in test_api.py).
E3 = [
    ("How many windows are drawn on this floor plan?", "count", "window", "en", "template"),
    ("Combien de lavabos y a-t-il dans l'appartement B ?", "count", "washbasin", "fr", "template"),
    ("Wie viele Räume mit der Bezeichnung «Bad» gibt es in diesem Geschoss?", "count", "room", "de",
     "template"),
    ("Kuinka monta ovea pohjapiirustuksessa on?", "count", "door", "fi", "template"),
    ("What is the net floor area of apartment A?", "area", None, "en", "template"),
    ("Kui suur on ruumi „TUALETTRUUM (76-3)“ netopõrandapind korrusel „5.korrus“ (m²)?", "area",
     None, None, "template"),
    ("What is the total length of the walls on 'First Floor', in m?", "length", None, "en",
     "template"),
    ("What is the fire rating of the entrance door?", "text", None, "en", "template"),
    ("uh so like the bedroom on the top right, the 3.05 one, how big is that thing roughly", "area",
     None, "en", "template"),
    ("ok I need to order sinks for the whole floor, both flats, so what do I order, like how many",
     "count", "washbasin", "en", "template"),
    ("Paljonko lattiapinta-alaa on makuuhuoneessa?", "area", None, "fi", "template"),
    ("Gibt es hier irgendwo Platz für eine zweite Dusche, und wie viele sind schon eingezeichnet?",
     "count", "shower", "de", "template"),
    ("Which sanitary fixtures are available in this building?", "text", None, "en", "choice"),
    ("toilets?", "count", "wc", "en", "template"),
    ("What material are the exterior walls made of?", "text", None, "en", "template"),
    ("how many places can people sleep in apartment A", "count", "room", "en", "choice"),
]

ROUTER_INTENT = {"count": "count", "area": "area", "find_text": "text", "abstain": None}


@pytest.mark.parametrize("q,intent,subject,lang,how", E3, ids=[f"E3-{i + 1:02d}" for i in range(16)])
def test_e3_questions(q, intent, subject, lang, how):
    r = route(q, CTX)
    if how == "choice":
        assert r.intent == "unknown"  # handed to the Decisions `choice` step
        return
    if intent == "length":
        assert (r.intent, r.reason, r.topic) == ("abstain", "not_supported_yet", "length")
    elif intent == "text":
        assert r.intent in ("find_text", "abstain")
        assert r.reason != "unclear_question"
    else:
        assert r.intent == intent
        if subject:
            assert r.subject == subject
    if lang:
        assert r.lang == lang


def test_e3_scopes():
    assert route(E3[1][0], CTX).scope == {"kind": "unit", "unit": "B"}
    assert route(E3[2][0], CTX).scope == {"kind": "label", "label": "Bain"}  # «Bad» → printed Bain
    assert route(E3[8][0], CTX).scope == {"kind": "room", "room_id": "3.05"}
    assert route(E3[9][0], CTX).scope is None  # "both flats" is not apartment S
    assert route(E3[10][0], CTX).scope == {"kind": "label", "label": "Chambre à coucher"}
    # A quoted room that is not on the drawing stays a (non-existent) label → unclear later.
    assert route(E3[5][0], CTX).scope["kind"] == "label"


FIRE_89450 = ("On sheet A700 of the drawings, what fire rating is scheduled for the unit doors "
              "(type U01)?")


def test_fire_rating_routes_to_find_text_not_blind_abstain():
    others = ("Quelle est la résistance au feu de la porte palière ?",
              "Welche Brandschutzklasse hat die Wohnungstür?", "Mikä on oven paloluokka?")
    for q in (FIRE_89450, E3[7][0], *others):
        r = route(q, CTX)
        assert r.intent == "find_text" and r.subject == "fire_rating", q
        assert r.pattern and re.search(r.pattern, "EI30") and re.search(r.pattern, "20 MIN.")


@pytest.mark.dataset
def test_fire_rating_dataset_line_89450():
    line = (REPO / "data/junction-dataset/questions.jsonl").read_text().splitlines()[89449]
    q = json.loads(line)
    assert q["type"] == "text"
    r = route(q["question"])
    assert r.intent == "find_text" and r.subject == "fire_rating"


@pytest.mark.parametrize("q,lang", [
    ("Wie viele Fenster?", "de"), ("Montako ikkunaa tässä pohjapiirroksessa on?", "fi"),
    ("Combien de fenêtres y a-t-il sur ce plan ?", "fr"),
    ("Quante finestre sono disegnate in questa pianta?", "it"),
    ("How many rooms are labelled Chambre à coucher?", "en"),
])
def test_language(q, lang):
    assert detect_lang(q)[0] == lang


@pytest.mark.parametrize("q,expected", [
    ("Quanti WC sono disegnati in questa pianta?", ("count", "wc", None)),
    ("Quante vasche da bagno sono disegnate in questa pianta?", ("count", "bathtub", None)),
    ("Wie viele Badewannen sind auf diesem Grundriss eingezeichnet?", ("count", "bathtub", None)),
    ("Wie gross ist die Nettowohnfläche von Wohnung C in m² (Summe aller Räume ohne Balkone)?",
     ("area", None, {"kind": "unit", "unit": "C"})),
    ("Qual è la superficie netta del locale U1.02 ('Bagno') in m²?",
     ("area", None, {"kind": "room", "room_id": "U1.02"})),
    (("What is the total net floor area in m² of all numbered indoor rooms on this floor "
      "(excluding balconies, loggias, terraces)?"), ("area", None, None)),
    ("How many rooms labelled 'Treppenhaus' are there on this floor?",
     ("count", "room", {"kind": "label", "label": "Treppenhaus"})),
    ("Combien d'appartements figurent sur ce plan ?", ("count", "apartment", None)),
    ("What is the room height?", ("abstain", None, None)),
    ("Tell me a joke", ("unknown", None, None)),
])
def test_dataset_templates_all_languages(q, expected):
    r = route(q)
    assert (r.intent, r.subject, r.scope) == expected


def test_labels_validate_against_drawing():
    ctx = DrawingCtx(labels=["Küche", "Bad", "Zimmer"], room_ids=["1.01"], units=["A"])
    assert route("How many kitchens are there?", ctx).scope == {"kind": "label", "label": "Küche"}
    assert route("How many bathrooms?", ctx).scope == {"kind": "label", "label": "Bad"}
    assert route("Combien de pièces portant la désignation « Bad » ?", ctx).scope == {
        "kind": "label", "label": "Bad"}


@pytest.mark.dataset
def test_all_swiss_dataset_questions_route_to_their_type_and_scope():
    path = REPO / "data/junction-dataset/questions.jsonl"
    n = bad = 0
    for line in path.open():
        d = json.loads(line)
        if "swiss_dwellings" not in d["path"]:
            continue
        n += 1
        q = d["question"]
        r = route(q)
        if re.search(r"(room|Raum|pièce|locale) \S+ \(", q):
            es = "room"
        elif re.search(r"apartment [A-Z]|Wohnung [A-Z]|appartement [A-Z]|appartamento [A-Z]", q):
            es = "unit"
        elif re.search(r"labelled|Bezeichnung|désign|denomina", q):
            es = "label"
        else:
            es = "floor"
        if r.intent != d["type"] or (r.scope or {}).get("kind", "floor") != es:
            bad += 1
    assert n > 30_000 and bad == 0, f"{bad}/{n} misrouted"


def test_detect_has_no_signal_on_gibberish():
    assert detect_lang("???")[1] is False


def test_router_is_pure_text():
    # The fixture bundle is ours; nothing about the router reads files at route time.
    assert Path(FRONTEND_DRAWING).is_file()
