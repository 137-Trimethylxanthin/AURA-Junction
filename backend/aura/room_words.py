"""Room words of a question matched against the labels printed on a sheet, abbreviations written out.

The store types a room when it interprets the drawing (`rooms.room_type`); a label it could not type then ("MEZZ.",
"ELEV.", "BR 2") stays untyped until the drawing is interpreted again. This module types such a label when a question
is asked, with the same abbreviation table as the store (store/jstore/interpret/vocab.py), so no re-interpretation
is needed.
"""

from __future__ import annotations

import re
import unicodedata

#: abbreviations printed on (mostly US) sheets -> the room word they stand for (whole tokens only)
ABBREVIATIONS = {
    "mezz": "mezzanine", "br": "bedroom", "bdrm": "bedroom", "bedrm": "bedroom", "bath": "bathroom",
    "ba": "bathroom", "kit": "kitchen", "lr": "living room", "liv": "living room", "dr": "dining room",
    "din": "dining room", "mech": "mechanical", "stor": "storage", "cl": "closet", "clo": "closet",
    "corr": "corridor", "elev": "elevator", "util": "utility", "off": "office",
}

#: room type -> (words of a written-out label, words of a question in en/de/fr/fi/it); first match wins
_TYPES: list[tuple[str, str, str]] = [
    ("staircase", r"stair|treppe|escalier|scala|porras",
     r"stairs?|staircases?|stairwells?|stairways?|treppe\w*|escaliers?|scal[ae]|portaa\w*|porras\w*|portai\w*"),
    ("mezzanine", r"mezzanin|zwischengeschoss|soppalco", r"mezzanines?|mezz|zwischengeschoss\w*|soppalc\w+|parv\w+"),
    ("elevator", r"elevator|\blift\b|ascenseur|ascensore|aufzug|hissi",
     r"elevators?|lifts?|ascenseurs?|ascensor[ei]|aufzug\w*|aufzüge\w*|hissi\w*"),
    ("bedroom", r"bedroom", r"bed ?rooms?"),
    ("living_room", r"living", r"living ?rooms?|lounge"),
    ("dining_room", r"dining", r"dining ?rooms?"),
    ("kitchen", r"kitchen", r"kitchens?"),
    ("bathroom", r"bath", r"bath ?rooms?|baths?"),
    ("technical", r"mechanical|utility|technik|technique|tecnico",
     r"mechanical(?: rooms?)?|utility(?: rooms?)?|plant ?rooms?|technikr\w+|local technique|locale tecnico"),
    ("storage", r"storage|closet", r"storage(?: rooms?| units?)?|store ?rooms?|closets?"),
    ("corridor", r"corridor", r"corridors?|hallways?"),
    ("office", r"office", r"offices?"),
]
_LABEL = [(k, re.compile(p)) for k, p, _q in _TYPES]
_QUESTION = [(k, re.compile(rf"(?<![^\W\d_])(?:{q})(?![^\W\d_])", re.IGNORECASE)) for k, _p, q in _TYPES]
_TOKEN = re.compile(r"[a-z]+")
STAIR_WORDS = _QUESTION[0][1]
_BESIDE = re.compile(r"\b(?:under|below|above|over|behind|unter|sous|sotto)\b")


def _plain(s: str) -> str:
    s = (s or "").replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def expand(label: str | None) -> str:
    """The label in lower case without accents, abbreviations written out: "MEZZ." -> "mezzanine"."""
    return _TOKEN.sub(lambda m: ABBREVIATIONS.get(m.group(0), m.group(0)), _plain(label or ""))


def label_type(label: str | None) -> str | None:
    """The room type a printed label names once its abbreviations are written out, or None."""
    s = expand(label)
    if _BESIDE.search(s):               # "ROOM UNDER MEZZ." is not the mezzanine
        return None
    return next((k for k, rx in _LABEL if rx.search(s)), None) if s else None


def room_kind(room: dict) -> str | None:
    """A room's type: the store's, or (where the store left it open) what its printed label says."""
    t = room.get("room_type")
    return t if t and t != "room" else (label_type(room.get("name")) or t)


def asked_types(question: str) -> set[str]:
    """The room types a question names with a room word ("the stair", "the mezzanine", "the elevator")."""
    return {k for k, rx in _QUESTION if rx.search(question or "")}
