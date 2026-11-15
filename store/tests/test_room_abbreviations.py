"""Room labels printed as abbreviations are typed like the word they stand for."""
import pytest

from jstore.interpret.vocab import expand, room_type


@pytest.mark.parametrize("label,kind", [
    ("MEZZ.", "mezzanine"), ("MEZZANINE", "mezzanine"), ("STAIR", "staircase"), ("BR", "bedroom"), ("BDRM", "bedroom"),
    ("BR 2", "bedroom"), ("BATH", "bathroom"), ("BA", "bathroom"), ("KIT.", "kitchen"), ("LR", "living_room"),
    ("LIV.", "living_room"), ("DR", "dining_room"), ("DIN.", "dining_room"), ("MECH.", "technical"),
    ("Mech. Room", "technical"), ("STOR.", "storage"), ("CL", "storage"), ("CLO.", "storage"), ("CORR.", "corridor"),
    ("ELEV.", "elevator"), ("UTIL.", "technical"), ("UTILITY", "technical"), ("OFF.", "office"),
])
def test_abbreviations(label, kind):
    assert room_type(label) == kind


@pytest.mark.parametrize("label,kind", [
    ("Stanza", "room"), ("Bad", "bathroom"), ("Chambre", "bedroom"), ("K", "kitchen"), ("MH", "bedroom"),
    ("Zimmer 2", "room"), ("Küche", "kitchen"), ("Séjour", "living_room"), ("ROOM UNDER MEZZ.", "room"),
    ("CLINIC", None), ("WC", "toilet"),
])
def test_existing_language_entries_keep_their_type(label, kind):
    assert room_type(label) == kind


def test_expand_writes_whole_tokens_out():
    assert expand("MEZZ.") == "mezzanine." and expand("BR2") == "bedroom2" and expand("Broom") == "broom"
