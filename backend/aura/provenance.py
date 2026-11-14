"""Provenance filter: model prose may only contain numbers that a tool result produced.

Numbers are found as digits (with "," or "." as the decimal mark, thousands separators
tolerated, also glued to letters), as number words 0–100 in en/fr/fi/de/it/es, "dozen" and
Roman numerals. Any sentence carrying a number that is
not in the allowed set is dropped; the caller adds a `warn` trace node saying so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from aura.contract import ToolResult

_EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
             "eighteen", "nineteen"]
_EN_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
            80: "eighty", 90: "ninety"}
_FR = ["zéro", "un", "une", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
       "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf"]
_FR_VALUES = [0, 1, 1, *range(2, 20)]
_FR_TENS = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante",
            80: "quatre-vingts", 100: "cent"}
_DE = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn",
       "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn",
       "neunzehn"]
_DE_TENS = {20: "zwanzig", 30: "dreißig", 40: "vierzig", 50: "fünfzig", 60: "sechzig",
            70: "siebzig", 80: "achtzig", 90: "neunzig", 100: "hundert"}
_FI = ["nolla", "yksi", "kaksi", "kolme", "neljä", "viisi", "kuusi", "seitsemän", "kahdeksan",
       "yhdeksän", "kymmenen"]
_FI_TENS = {20: "kaksikymmentä", 30: "kolmekymmentä", 40: "neljäkymmentä", 50: "viisikymmentä",
            60: "kuusikymmentä", 70: "seitsemänkymmentä", 80: "kahdeksankymmentä",
            90: "yhdeksänkymmentä", 100: "sata"}
_IT = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci",
       "undici", "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette",
       "diciotto", "diciannove"]
_IT_TENS = {20: "venti", 30: "trenta", 40: "quaranta", 50: "cinquanta", 60: "sessanta",
            70: "settanta", 80: "ottanta", 90: "novanta", 100: "cento"}
_ES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
       "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho",
       "diecinueve"]
_ES_TENS = {20: "veinte", 30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta",
            70: "setenta", 80: "ochenta", 90: "noventa", 100: "cien"}
_DOZEN = ["dozen", "douzaine", "dutzend", "dozzina", "docena", "tusina"]
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _words() -> dict[str, int]:
    w: dict[str, int] = {}
    for i, s in enumerate(_EN_UNITS):
        w[s] = i
    for t, s in _EN_TENS.items():
        w[s] = t
        for u in range(1, 10):
            w[f"{s}-{_EN_UNITS[u]}"] = t + u
    w["hundred"] = w["one hundred"] = 100
    # Articles and pronouns that look like "1" are not numbers in prose.
    for article in ("one", "un", "une", "eins"):
        w.pop(article, None)
    for s, v in zip(_FR, _FR_VALUES, strict=True):
        w[s] = v
    for article in ("un", "une"):
        w.pop(article, None)
    w.update({s: t for t, s in _FR_TENS.items()})
    for i, s in enumerate(_DE):
        w[s] = i
    for t, s in _DE_TENS.items():
        w[s] = t
        for u in range(1, 10):
            unit = "ein" if u == 1 else _DE[u]
            w[f"{unit}und{s}"] = t + u
    for i, s in enumerate(_FI):
        w[s] = i
    for u in range(11, 20):
        w[_FI[u - 10] + "toista"] = u
    for t, s in _FI_TENS.items():
        w[s] = t
        if t < 100:
            for u in range(1, 10):
                w[s + _FI[u]] = t + u
    for i, s in enumerate(_IT):
        w[s] = i
    for t, s in _IT_TENS.items():
        w[s] = t
        if t < 100:
            for u in range(1, 10):
                w[(s[:-1] if u in (1, 8) else s) + _IT[u]] = t + u
    for i, s in enumerate(_ES):
        w[s] = i
    for t, s in _ES_TENS.items():
        w[s] = t
    for u in range(1, 10):
        w["veinti" + _ES[u]] = 20 + u
    w.update({"veintidós": 22, "veintitrés": 23, "veintiséis": 26})
    for s in _DOZEN:
        w[s] = 12
    for article in ("uno", "una"):  # articles in it/es
        w.pop(article, None)
    return w


WORDS = _words()
_WORD_RE = re.compile(r"(?<![^\W\d_])(" + "|".join(sorted(map(re.escape, WORDS), key=len,
                                                           reverse=True)) + r")(?![^\W\d_])",
                      re.IGNORECASE)
# Digits anywhere, also glued to letters or underscores ("x57", "_57_").
_NUM_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[ ,’']\d{3})+(?:[.,]\d+)?(?![\d])|(?<![\d.,])\d+(?:[.,]\d+)?")
_UNIT_RE = re.compile(r"(?<=[\d\s])(?:m|cm|mm)[23²³](?![\w])")  # "m2"/"m3" are units, not numbers
_ROMAN_RE = re.compile(r"(?<![^\W\d_])[IVXLC]{2,}(?![^\W\d_])")
_ADJACENT_RE: re.Pattern[str]  # set below: two number words in a row ("twenty one")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _to_float(s: str) -> float | None:
    t = s.replace(" ", "").replace("’", "").replace("'", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", t):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _roman(s: str) -> int | None:
    vals = [_ROMAN[c] for c in s]
    total = sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))
    return total if total > 1 else None


def _tokens(text: str) -> list[tuple[float, bool]]:
    """(value, written with decimals) for every number in the text."""
    out: list[tuple[float, bool]] = []
    text = _UNIT_RE.sub(" ", text or "")
    for m in _NUM_RE.finditer(text):
        v = _to_float(m.group(0))
        if v is not None:
            out.append((v, bool(re.search(r"[.,]\d{1,2}$", m.group(0)))))
    for m in _WORD_RE.finditer(text):
        out.append((float(WORDS[m.group(1).lower()]), False))
    for m in _ROMAN_RE.finditer(text):
        if (v := _roman(m.group(0))) is not None:
            out.append((float(v), False))
    if any(m.group(0).lower() not in WORDS for m in _ADJACENT_RE.finditer(text)):
        out.append((float("nan"), False))  # a spelled-out compound we cannot parse: never allowed
    return out


_ALL_WORDS = "|".join(sorted(map(re.escape, [*WORDS, "one", "un", "une", "eins", "uno", "una"]),
                             key=len, reverse=True))
_ADJACENT_RE = re.compile(rf"(?<![^\W\d_])(?:{_ALL_WORDS})[\s-]+(?:{_ALL_WORDS})(?![^\W\d_])",
                          re.IGNORECASE)


def numbers_in(text: str) -> list[float]:
    return [v for v, _ in _tokens(text)]


def allowed_numbers(results: Iterable[ToolResult], extra_text: str = "") -> set[float]:
    """Every number a tool result OUTPUTS: values, parts, room areas and ids, hit texts.
    Never its args or summary: those can echo the model's own input (find_text('57 doors')).
    `extra_text` is only identifiers the caller vetted (room numbers named in the question)."""
    allowed: set[float] = set()
    for r in results:
        allowed.update(float(x) for x in r.numbers)
        if isinstance(r.value, int | float):
            allowed.add(float(r.value))
        for room in r.rooms:
            if room.area_m2 is not None:
                allowed.add(float(room.area_m2))
            allowed.update(numbers_in(room.room_id))
        for row in r.meta.get("rooms") or []:
            allowed.update(numbers_in(str(row.get("room_id", ""))))
        if r.tool != "find_text":
            allowed.add(float(len(r.boxes)))
        for t in r.meta.get("texts") or []:  # what was found, not what was searched for
            allowed.update(numbers_in(t))
    allowed.update(numbers_in(extra_text))
    return allowed


def _ok(v: float, decimals: bool, allowed: set[float]) -> bool:
    """Exact, or (only when the prose writes decimals) the tool value rounded to 1 decimal."""
    return any(abs(v - a) <= 0.005 or (decimals and round(a, 1) == v) for a in allowed)


def filter_prose(text: str, allowed: set[float]) -> tuple[str, list[str]]:
    """(kept text, dropped sentences)."""
    kept, dropped = [], []
    for sent in _SENT_RE.split((text or "").strip()):
        if not sent:
            continue
        bad = [v for v, dec in _tokens(sent) if not _ok(v, dec, allowed)]
        (dropped if bad else kept).append(sent)
    return " ".join(kept), dropped
