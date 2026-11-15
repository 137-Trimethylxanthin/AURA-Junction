"""Closing fixes: 'largest room' over an IFC building reads as one sentence without GUIDs."""

from types import SimpleNamespace

from aura import answer as A
from aura import building, tools


def test_ifc_global_ids_and_store_ids_are_internal_but_printed_room_numbers_are_not():
    assert A.INTERNAL_ID.fullmatch("2$rHI0$Xf1XuycdkEm_J0E")
    assert A.INTERNAL_ID.fullmatch("e143313")
    for printed in ("1.03", "A-204", "R12", "Top 14"):
        assert not A.INTERNAL_ID.fullmatch(printed)


def test_gross_area_spaces_are_not_rooms():
    for name in ("Bruttoareal 1etg", "Bruttoareal kjeller", "BGF EG", "Bruttofläche OG", "Gross floor area L1", "BTA 2"):
        assert tools._GROSS_SPACE.search(name), name
    for name in ("Auditorium", "Vent.rom", "Bad", "Grossraumbüro", "Brutstätte", "Kitchen"):
        assert not tools._GROSS_SPACE.search(name), name


def _floor(label: str, word: str, area: float, said: str):
    return (SimpleNamespace(label=label),
            {"answer": {"kind": "note", "value": said, "number": area, "unit": "m2"},
             "canonical_en": f"Which is the {word} room on this floor"})


def test_building_rank_picks_the_one_winner_of_all_floors():
    floors = [_floor("kjeller", "largest", 144.43, "Vent.rom è il locale più grande: 144,43 m²."),
              _floor("1.etg", "largest", 459.7, "Boksamling è il locale più grande: 459,70 m²."),
              _floor("2.etg", "largest", 210.0, "Auditorium è il locale più grande: 210,00 m².")]
    sh, r = building._rank_winner(floors)
    assert sh.label == "1.etg" and r["answer"]["number"] == 459.7
    small = [_floor("a", "smallest", 3.0, "x"), _floor("b", "smallest", 1.5, "y")]
    assert building._rank_winner(small)[0].label == "b"
    # anything that is not a rank answer on every floor keeps the per-floor listing
    other = (SimpleNamespace(label="c"), {"answer": {"kind": "note", "value": "text"}, "canonical_en": "List the rooms"})
    assert building._rank_winner([*floors, other]) is None
    assert building._rank_winner([floors[0], small[0]]) is None
