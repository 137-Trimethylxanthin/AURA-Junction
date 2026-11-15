"""A room is found by what the store reads its label as, not only by how the label is written."""
import pytest

from aura import steps, tools
from aura.router import STORE_ROOM_CONCEPT, DrawingCtx, label_of

CTX = DrawingCtx(labels=["OH", "MH", "MH", "K", "KHH", "PARVEKE"], room_ids=["e1", "e2", "e3", "e4", "e5", "e6"],
                 kind="scan", label_concepts={"OH": "living", "MH": "bedroom", "K": "kitchen", "KHH": "storage",
                                              "PARVEKE": "outdoor"})


@pytest.mark.parametrize("question,label", [
    ("What is the area of the living room?", "OH"),
    ("Wie gross ist das Wohnzimmer?", "OH"),
    ("Quelle est la surface du séjour ?", "OH"),
    ("Quanto è grande il soggiorno?", "OH"),
    ("Kuinka suuri on olohuone?", "OH"),
    ("How big is the bedroom?", "MH"),
    ("What is the area of the kitchen?", "K"),
])
def test_a_room_word_resolves_to_the_abbreviated_label_the_store_typed(question, label):
    assert label_of(question, CTX)[0] == label


def test_a_printed_label_still_wins_over_the_store_type():
    ctx = DrawingCtx(labels=["Wohnen", "OH"], label_concepts={"OH": "living"})
    assert label_of("What is the area of the living room?", ctx)[0] == "Wohnen"


def test_without_a_store_type_nothing_is_guessed():
    assert label_of("What is the area of the living room?", DrawingCtx(labels=["OH", "MH"]))[0] is None


def test_room_type_of_a_step_follows_the_store_type():
    assert steps.room_type({"name": "MH", "room_type": "bedroom"}) == "bedroom"
    assert steps.room_type({"name": "OH", "room_type": "living_room"}) == "living"
    assert steps.room_type({"name": "MH"}) == "other"
    assert steps.room_type({"name": "Schlafzimmer", "room_type": "office"}) == "bedroom"   # the printed word first
    assert set(STORE_ROOM_CONCEPT.values()) <= set(steps.ROOM_TYPES)
    assert tools._room_category("KHH", False, "storage")[0] == "cat-storage"
