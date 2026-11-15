"""Unit conversion in natural language (metric by default), 0 model calls.

Two jobs:
- `parse_conversion("convert 12 m² to sq ft")` answers a pure conversion question.
- `requested_unit("area of the kitchen in square feet")` finds the unit a drawing question wants
  its answer in and returns the question without that phrase, so routing sees the plain question.

Every factor is exact by definition (1 in = 0.0254 m, 1 ft = 0.3048 m, 1 acre = 4046.8564224 m²).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# unit key -> (dimension, factor to the SI base unit: m, m², m³)
UNITS: dict[str, tuple[str, float]] = {
    "mm": ("length", 0.001), "cm": ("length", 0.01), "dm": ("length", 0.1), "m": ("length", 1.0),
    "km": ("length", 1000.0), "in": ("length", 0.0254), "ft": ("length", 0.3048),
    "yd": ("length", 0.9144), "mi": ("length", 1609.344),
    "mm2": ("area", 1e-6), "cm2": ("area", 1e-4), "m2": ("area", 1.0), "km2": ("area", 1e6),
    "ha": ("area", 1e4), "a": ("area", 100.0), "in2": ("area", 0.0254 ** 2),
    "ft2": ("area", 0.3048 ** 2), "yd2": ("area", 0.9144 ** 2), "acre": ("area", 4046.8564224),
    "m3": ("volume", 1.0), "cm3": ("volume", 1e-6), "l": ("volume", 0.001),
    "ft3": ("volume", 0.3048 ** 3), "in3": ("volume", 0.0254 ** 3), "yd3": ("volume", 0.9144 ** 3),
}

SYMBOL = {"mm": "mm", "cm": "cm", "dm": "dm", "m": "m", "km": "km", "in": "in", "ft": "ft",
          "yd": "yd", "mi": "mi", "mm2": "mm²", "cm2": "cm²", "m2": "m²", "km2": "km²", "ha": "ha",
          "a": "a", "in2": "sq in", "ft2": "sq ft", "yd2": "sq yd", "acre": "acres", "m3": "m³",
          "cm3": "cm³", "l": "L", "ft3": "cu ft", "in3": "cu in", "yd3": "cu yd"}

IMPERIAL = {"in", "ft", "yd", "mi", "in2", "ft2", "yd2", "acre", "ft3", "in3", "yd3"}
# Contract units of the drawing answers (`AnswerArea` is m², `AnswerQuantity` m / m² / m³).
CONTRACT_UNIT = {"m": "m", "m2": "m2", "m3": "m3"}

_SQ = r"(?:square|sq\.?|quadrat|carr[ée]s?|neliö)\s*"
_CU = r"(?:cubic|cu\.?|kubik)\s*"
# Longest aliases first within each unit; matched case-insensitively at word boundaries.
_ALIASES: list[tuple[str, str]] = [
    ("ft2", rf"{_SQ}(?:feet|foot|ft|fu(?:ß|ss)|pieds?)|ft\s*[²2]|sq\.?\s*ft|sqft|quadratfu(?:ß|ss)|pieds?\s+carr[ée]s?|sf"),
    ("in2", rf"{_SQ}(?:inch(?:es)?|in|zoll)|in\s*[²2]|sq\.?\s*in"),
    ("yd2", rf"{_SQ}(?:yards?|yd)|yd\s*[²2]|sq\.?\s*yd"),
    ("ft3", rf"{_CU}(?:feet|foot|ft)|ft\s*[³3]|cu\.?\s*ft"),
    ("in3", rf"{_CU}(?:inch(?:es)?|in)|in\s*[³3]"),
    ("yd3", rf"{_CU}(?:yards?|yd)|yd\s*[³3]"),
    ("acre", r"acres?|morgen"),
    ("ha", r"hectares?|hektar(?:e|en)?|ha"),
    ("km2", rf"{_SQ}kilomet(?:er|re)s?|km\s*[²2]|quadratkilometer"),
    ("mm2", rf"{_SQ}millimet(?:er|re)s?|mm\s*[²2]"),
    ("cm2", rf"{_SQ}centimet(?:er|re)s?|cm\s*[²2]|quadratzentimeter"),
    ("m2", rf"{_SQ}met(?:er|re)s?|m\s*[²2]|sq\.?\s*m|sqm|qm|quadratmeter|m[eè]tres?\s+carr[ée]s?|neliömetri\w*|metri\s+quadrati"),
    ("m3", rf"{_CU}met(?:er|re)s?|m\s*[³3]|kubikmeter|m[eè]tres?\s+cubes?"),
    ("cm3", r"cm\s*[³3]|ccm"),
    ("l", r"lit(?:er|re)s?|l"),
    ("mm", r"millimet(?:er|re)s?|millimetri|mm"),
    ("cm", r"centimet(?:er|re)s?|zentimeter|centimetri|cm"),
    ("dm", r"decimet(?:er|re)s?|dezimeter|dm"),
    ("km", r"kilomet(?:er|re)s?|km"),
    ("mi", r"miles?|meilen?|mi"),
    ("yd", r"yards?|yd"),
    ("ft", r"feet|foot|ft|fu(?:ß|ss)|pieds?|jalka\w*|piedi"),
    ("in", r"inch(?:es)?|zoll|pouces?|tuuma\w*|pollici"),
    ("m", r"met(?:er|re)s?|m[eè]tres?|metri|metr\w*|m"),
]
_UNIT_SRC = "|".join(f"(?P<u_{k}_{i}>{src})" for i, (k, src) in enumerate(_ALIASES))
_UNIT_RE = re.compile(rf"(?<![\w])(?:{_UNIT_SRC})(?![\w²³])", re.IGNORECASE)
_NUM = r"(?P<num>\d{1,3}(?:[ ,'’]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
# A number directly followed by a unit ("12 m²", "12m2", "3.5 ft"); feet/inch marks ("12'6\"").
_QTY_RE = re.compile(rf"{_NUM}\s*(?P<unit>(?:{'|'.join(src for _, src in _ALIASES)}))(?![\w²³])",
                     re.IGNORECASE)
_FEET_INCH_RE = re.compile(r"(?P<ft>\d+)\s*(?:'|’|ft)\s*(?P<inch>\d+(?:\.\d+)?)\s*(?:\"|”|in\b)", re.IGNORECASE)

_FILLER = {
    # en
    "convert", "conversion", "what", "whats", "what's", "is", "are", "how", "many", "much", "in", "into",
    "to", "as", "please", "the", "a", "an", "equals", "equal", "equivalent", "of", "me", "tell", "give",
    "do", "does", "make", "makes", "there", "that", "this", "show", "it", "would", "be", "units", "unit",
    "from", "and", "can", "you", "calculate", "number", "amount", "value",
    # de
    "umrechnen", "rechne", "rechnen", "um", "wie", "viel", "viele", "sind", "ist", "nach", "bitte", "was",
    "ergeben", "ergibt", "entspricht", "entsprechen", "von", "zu", "auf", "das", "die", "der", "in",
    # fr
    "convertir", "convertis", "combien", "de", "en", "est", "font", "fait", "font-ils", "quel", "quelle",
    "le", "la", "les", "un", "une", "des", "y", "a-t-il",
    # fi / it
    "muunna", "kuinka", "monta", "paljon", "on", "ovat", "converti", "quanti", "quante", "sono", "è", "in",
}


@dataclass
class Conversion:
    value: float
    unit: str        # source unit key
    target: str      # target unit key
    result: float

    def text(self) -> str:
        return f"{fmt(self.value)} {SYMBOL[self.unit]} = {fmt(self.result)} {SYMBOL[self.target]}"


def _unit_key(m: re.Match[str]) -> str:
    for name, val in m.groupdict().items():
        if val is not None and name.startswith("u_"):
            return name[2:].rsplit("_", 1)[0]
    raise ValueError("no unit group")


def unit_of(text: str) -> str | None:
    m = _UNIT_RE.fullmatch(text.strip())
    return _unit_key(m) if m else None


def _num(s: str) -> float:
    s = s.replace("’", "'").replace(" ", "").replace("'", "")
    if s.count(",") == 1 and "." not in s and len(s.split(",")[1]) != 3:
        s = s.replace(",", ".")  # decimal comma: "3,40"
    return float(s.replace(",", ""))


def convert(value: float, unit: str, target: str) -> float:
    du, fu = UNITS[unit]
    dt, ft = UNITS[target]
    if du != dt:
        raise ValueError(f"cannot convert {du} to {dt}")
    return value * fu / ft


def fmt(v: float) -> str:
    """Up to 4 significant decimals, trailing zeros dropped, thousands separated."""
    if v == 0:
        return "0"
    digits = 2 if abs(v) >= 1 else max(2, 3 - int(f"{abs(v):e}".split("e")[1]))
    s = f"{v:,.{min(digits, 6)}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _quantities(text: str) -> list[tuple[re.Match[str], float, str]]:
    out: list[tuple[re.Match[str], float, str]] = []
    for m in _FEET_INCH_RE.finditer(text):
        out.append((m, int(m["ft"]) * 12 + float(m["inch"]), "in"))
    taken = [m.span() for m, _, _ in out]
    for m in _QTY_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        u = unit_of(m["unit"])
        if u is None:
            continue
        out.append((m, _num(m["num"]), u))
    return sorted(out, key=lambda t: t[0].start())


def parse_conversion(text: str) -> Conversion | None:
    """A question that only asks to convert one quantity to another unit, else None.

    Accepts 'convert 12 m² to sq ft', '12 m2 in square feet?', 'how many feet is 3 m',
    'wie viel sind 5 m in Fuß', '10 ft to m'. Anything else in the sentence (a room, the plan)
    makes it a drawing question, so None.
    """
    s = text.strip()
    qs = _quantities(s)
    if len(qs) != 1:
        return None
    m, value, unit = qs[0]
    rest = (s[:m.start()] + " " + s[m.end():]).strip()
    # The target unit is the remaining unit word (exactly one).
    targets = [t for t in _UNIT_RE.finditer(rest)]
    # 'in' as preposition is also the inch alias: drop bare 'in'/'a'/'l'/'m' preposition-like hits
    # unless nothing else is left.
    strong = [t for t in targets if t.group(0).lower() not in ("in", "a", "l", "m", "mi")]
    cands = strong or [t for t in targets if t.group(0).lower() not in ("in", "a")]
    if len(cands) != 1:
        return None
    t = cands[0]
    target = _unit_key(t)
    if target == unit or UNITS[target][0] != UNITS[unit][0]:
        return None
    leftover = (rest[:t.start()] + " " + rest[t.end():]).lower()
    words = [w for w in re.split(r"[\s?!.,:;=()/]+", leftover) if w]
    if any(w not in _FILLER for w in words):
        return None
    return Conversion(value, unit, target, convert(value, unit, target))


# "... in square feet", "... in sq ft please", "(in imperial)", "auf Quadratfuß"
_REQ_RE = re.compile(
    rf"\s*[,(]?\s*\b(?:in|into|as|en|auf|als|nach)\s+(?P<u>imperial(?:\s+units)?|(?:{'|'.join(src for _, src in _ALIASES)}))"
    rf"(?![\w²³])\s*(?:please|bitte)?\s*\)?\s*(?=[?.!]?\s*$)",
    re.IGNORECASE,
)


_THRESHOLD = re.compile(
    r"(?:than|over|under|above|below|exceed\w*|at\s+(?:least|most)|minimum|maximum|min\.?|max\.?|[<>≥≤]=?|"
    r"als|über|unter|mindestens|höchstens|plus de|moins de|yli|alle|oltre|sotto)\s*\d", re.IGNORECASE)


def requested_unit(text: str) -> tuple[str | None, str]:
    """('ft2', question without the phrase) when a drawing question asks for a specific unit.

    'imperial' maps to 'imperial' (the caller picks ft / ft² / ft³ by the answer's dimension).
    Metric requests ("in m²", "in cm") return the unit and the question unchanged.
    A pure conversion question is not a drawing question: use `parse_conversion` first.
    """
    m = _REQ_RE.search(text)
    if not m:
        return None, text
    raw = m["u"]
    if raw.strip().lower() in ("mi", "a", "l", "in", "m"):  # "doors in MI?": a scope letter, not a unit
        return None, text
    key = "imperial" if raw.lower().startswith("imperial") else unit_of(raw)
    if key is None:
        return None, text
    if key != "imperial" and key not in IMPERIAL:
        return key, text  # metric is what answers are in: the question is routed exactly as asked
    before = text[:m.start()]
    if _THRESHOLD.search(before):
        return None, text  # "rooms larger than 200 in square feet": the unit belongs to the number
    cleaned = (text[:m.start()] + text[m.end():]).rstrip().rstrip(",;")
    if not cleaned.endswith("?") and text.rstrip().endswith("?"):
        cleaned += "?"
    return key, cleaned


def target_for(requested: str, dimension: str) -> str | None:
    """The unit key to show an answer of `dimension` in, for a requested unit (None when it does not fit)."""
    if requested == "imperial":
        return {"length": "ft", "area": "ft2", "volume": "ft3"}[dimension]
    if requested in UNITS and UNITS[requested][0] == dimension:
        return requested
    # "in feet" for an area answer means square feet.
    lift = {("ft", "area"): "ft2", ("in", "area"): "in2", ("yd", "area"): "yd2", ("m", "area"): "m2",
            ("cm", "area"): "cm2", ("ft", "volume"): "ft3", ("m", "volume"): "m3"}
    return lift.get((requested, dimension))
