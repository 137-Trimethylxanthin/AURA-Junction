"""Provenance filter: model prose may only contain numbers that a tool result produced.

Numbers are found as digits (with "," or "." as the decimal mark, thousands separators
tolerated) and as number words 0–100 in en/fr/fi/de. Any sentence carrying a number that is
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
_FI_TENS = {20: "kaksikymmentä", 30: "kolmekymmentä", 100: "sata"}


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
    w.update({s: t for t, s in _FI_TENS.items()})
    return w


WORDS = _words()
_WORD_RE = re.compile(r"(?<![^\W\d_])(" + "|".join(sorted(map(re.escape, WORDS), key=len,
                                                           reverse=True)) + r")(?![^\W\d_])",
                      re.IGNORECASE)
_NUM_RE = re.compile(r"(?<![\w.,])\d{1,3}(?:[ ,’']\d{3})+(?:[.,]\d+)?(?![\d])|(?<![\w])\d+(?:[.,]\d+)?")
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


def _tokens(text: str) -> list[tuple[float, bool]]:
    """(value, written with decimals) for every number in the text."""
    out: list[tuple[float, bool]] = []
    for m in _NUM_RE.finditer(text or ""):
        v = _to_float(m.group(0))
        if v is not None:
            out.append((v, bool(re.search(r"[.,]\d{1,2}$", m.group(0)))))
    for m in _WORD_RE.finditer(text or ""):
        out.append((float(WORDS[m.group(1).lower()]), False))
    return out


def numbers_in(text: str) -> list[float]:
    return [v for v, _ in _tokens(text)]


def allowed_numbers(results: Iterable[ToolResult], extra_text: str = "") -> set[float]:
    """Every number a tool result exposes: values, parts, and numbers in its summary/args."""
    allowed: set[float] = set()
    for r in results:
        allowed.update(float(x) for x in r.numbers)
        if r.value is not None:
            allowed.add(float(r.value))
        allowed.update(numbers_in(r.summary))
        allowed.update(numbers_in(str(r.args)))
        for room in r.rooms:
            allowed.add(float(room.area_m2))
        allowed.add(float(len(r.boxes)))
    allowed.update(numbers_in(extra_text))  # the user's own question (e.g. "room 3.04")
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
