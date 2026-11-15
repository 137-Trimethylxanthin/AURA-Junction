"""Units of measure: what unit a question asks for, conversion, and formatting.

The answer pipeline always computes in metric (m, m², m³). This module is applied at one place
around it (`pipeline.with_units`):

1. `plan(question)` reads the unit the user wants ("in square feet", "en pieds", "in Quadratfuß",
   "neliöjalkoina", "in feet and inches") and rewrites the question to its metric twin, so every
   route and the answer cache see the same question as before. Imperial quantities used to compare
   or filter ("larger than 200 sq ft", "wider than 3 ft") are rewritten to metric too.
2. `apply(response, unit)` adds an optional `display` block to the finished metric response and
   rewrites measures in its prose as "3,675 sq ft (341.42 m²)". The metric answer itself is kept.

Default output is metric: without a requested unit nothing changes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# ---- unit table ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    key: str
    dim: int        # 1 length, 2 area, 3 volume
    si: float       # one unit in m / m² / m³
    label: str
    family: str     # units of one family replace each other across dimensions (ft -> sq ft -> cu ft)
    names: str      # regex alternatives: words in en/de/fr/it/fi (matched case-insensitively)
    symbols: str = ""  # short symbols, only trusted after a number or a preposition


_IN, _FT, _YD = 0.0254, 0.3048, 0.9144
_SQ = r"(?:square|sq\.?)\s*"
_CU = r"(?:cubic|cu\.?)\s*"

UNITS: tuple[Unit, ...] = (
    # volume
    Unit("cuft", 3, _FT ** 3, "cu ft", "ft",
         _CU + r"f(?:ee|oo)t|kubikfu(?:ß|ss)|pieds?\s+cubes?|pied[ei]\s+cub[io]|kuutiojalk\w*",
         r"cu\.?\s*ft\.?|ft³|ft3|ft\^3"),
    Unit("cuyd", 3, _YD ** 3, "cu yd", "yd",
         _CU + r"yards?|kubikyards?|yards?\s+cubes?|verges?\s+cubes?|iard[ae]\s+cub[ae]|kuutiojaard\w*",
         r"cu\.?\s*yd\.?|yd³|yd3"),
    Unit("m3", 3, 1.0, "m³", "m",
         _CU + r"met(?:er|re)s?|kubikmetern?|mètres?\s+cubes?|metr[oi]\s+cub[oi]|kuutiometr\w*|kuutioina",
         r"m³|m3|cbm|m\^3"),
    Unit("l", 3, 0.001, "l", "l", r"lit(?:er|re)s?|litern|litr[oi]|litr(?:a|aa|oina|aa)"),
    # area
    Unit("sqft", 2, _FT ** 2, "sq ft", "ft",
         _SQ + r"f(?:ee|oo)t|quadratfu(?:ß|ss)|pieds?\s+carrés?|pied[ei]\s+quadr(?:at)?[oi]|neliöjalk\w*",
         r"sq\.?\s*ft\.?|sqft|ft²|ft2|ft\^2|sf"),
    Unit("sqin", 2, _IN ** 2, "sq in", "in",
         _SQ + r"inch(?:es)?|quadratzoll|pouces?\s+carrés?|pollic[ei]\s+quadr(?:at)?[oi]|neliötuum\w*",
         r"sq\.?\s*in\.?|in²|in2"),
    Unit("sqyd", 2, _YD ** 2, "sq yd", "yd",
         _SQ + r"yards?|quadratyards?|yards?\s+carrés?|verges?\s+carrées?|iard[ae]\s+quadr(?:at)?[ae]|"
         r"neliöjaard\w*",
         r"sq\.?\s*yd\.?|yd²|yd2"),
    Unit("acre", 2, 4046.8564224, "acres", "acre", r"acres?|acri|eekkeri\w*|eekkerei\w*"),
    Unit("ha", 2, 10000.0, "ha", "ha", r"hectares?|hektar(?:en)?|ettar[oi]|hehtaari\w*|hehtaarei\w*", r"ha"),
    Unit("km2", 2, 1e6, "km²", "km",
         _SQ + r"kilomet(?:er|re)s?|quadratkilometern?|kilomètres?\s+carrés?|neliökilometr\w*", r"km²|km2"),
    Unit("cm2", 2, 1e-4, "cm²", "cm",
         _SQ + r"centimet(?:er|re)s?|quadratzentimetern?|centimètres?\s+carrés?|centimetri\s+quadr(?:at)?i|"
         r"neliösenttimetr\w*", r"cm²|cm2"),
    Unit("mm2", 2, 1e-6, "mm²", "mm",
         _SQ + r"millimet(?:er|re)s?|quadratmillimetern?|millimètres?\s+carrés?|millimetri\s+quadr(?:at)?i|"
         r"neliömillimetr\w*", r"mm²|mm2"),
    Unit("m2", 2, 1.0, "m²", "m",
         _SQ + r"met(?:er|re)s?|quadratmetern?|mètres?\s+carrés?|metr[oi]\s+quadr(?:at)?[oi]|neliömetr\w*|"
         r"neliöinä",
         r"sq\.?\s*m\.?|sqm|m²|m2|qm|m\^2"),
    # length
    Unit("ftin", 1, _FT, "ft-in", "ft",
         r"f(?:ee|oo)t\s*(?:and|&|[-/,])?\s*inch(?:es)?|fu(?:ß|ss)\s+und\s+zoll|pieds?\s+et\s+(?:en\s+)?pouces?|"
         r"pied[ei]\s+e\s+pollic[ei]|jalk\w+\s+ja\s+tuum\w+",
         r"ft\.?\s*(?:and|&|[-/])?\s*in\.?"),
    Unit("ft", 1, _FT, "ft", "ft", r"feet|foot|fu(?:ß|ss)|pieds?|pied[ei]|jalkoina|jalkaa|jalkoja", r"ft\.?"),
    Unit("in", 1, _IN, "in", "in", r"inch(?:es)?|zoll|pouces?|pollic[ei]|tuumina|tuumaa|tuumia", r"in\.?"),
    Unit("yd", 1, _YD, "yd", "yd", r"yards|verges|iarde|jaardeina|jaardia|jaardeja", r"yd\.?|yard"),
    Unit("km", 1, 1000.0, "km", "km", r"kilomet(?:er|re)s?|kilometern|kilomètres?|chilometr[oi]|kilometr\w+", r"km"),
    Unit("cm", 1, 0.01, "cm", "cm",
         r"centimet(?:er|re)s?|zentimetern?|centimètres?|centimetr[oi]|senttimetr\w+|sentteinä|senttiä", r"cm"),
    Unit("mm", 1, 0.001, "mm", "mm", r"millimet(?:er|re)s?|millimetern|millimètres?|millimetr\w+", r"mm"),
    Unit("m", 1, 1.0, "m", "m", r"met(?:er|re)s?|metern|mètres?|metr[oi]|metreinä|metriä|metrejä", r"m"),
)
BY_KEY: dict[str, Unit] = {u.key: u for u in UNITS}
METRIC_DEFAULT = {1: "m", 2: "m2", 3: "m3"}
_METRIC_LABEL = {"m": "m", "m2": "m²", "m3": "m³"}
_IMPERIAL = {"ft", "in", "yd", "acre"}  # families whose quantities in a question are rewritten


def unit_for(key_or_symbol: str | None) -> Unit | None:
    """A unit from its key or a printed symbol/name ('m2', 'm²', 'sq ft', 'mm')."""
    s = (key_or_symbol or "").strip().lower()
    if not s:
        return None
    if s in BY_KEY:
        return BY_KEY[s]
    for u in UNITS:
        if re.fullmatch(f"(?:{u.names}|{u.symbols})" if u.symbols else f"(?:{u.names})", s, re.IGNORECASE):
            return u
    return None


def coerce(unit: Unit, dim: int) -> Unit | None:
    """The unit of `unit`'s family for another dimension: feet for an area is sq ft."""
    if unit.dim == dim:
        return unit
    for u in UNITS:
        if u.family == unit.family and u.dim == dim and u.key != "ftin":
            return u
    return None


def convert(value: float, src: str | Unit, dst: str | Unit) -> float:
    a = src if isinstance(src, Unit) else BY_KEY[src]
    b = dst if isinstance(dst, Unit) else BY_KEY[dst]
    if a.dim != b.dim:
        raise ValueError(f"cannot convert {a.key} to {b.key}")
    return value * a.si / b.si


# ---- formatting ---------------------------------------------------------------------------------

_DEC_COMMA = {"de", "fr", "fi", "it"}


def fmt_number(v: float, lang: str = "en", decimals: int | None = None) -> str:
    """About four significant figures, at most 2 decimals (4 below 1), locale separators."""
    fixed = decimals is not None
    if decimals is None:
        mag = math.floor(math.log10(abs(v))) if v else 0
        decimals = max(0, min(3 - mag, 2 if abs(v) >= 1 else 4))
    s = f"{v:,.{decimals}f}"
    if not fixed and "." in s:
        s = s.rstrip("0").rstrip(".")  # 200 sq ft, not 200.0
    if lang in _DEC_COMMA:
        s = s.replace(",", " ").replace(".", ",")
    return s


def fmt_ft_in(feet: float, frac: int = 1) -> str:
    """12' 6" ; with frac=8 inches are rounded to 1/8 and written 12' 6 1/2"."""
    sign = "-" if feet < 0 else ""
    parts = round(abs(feet) * 12 * frac)
    whole_in, rem = divmod(parts, frac)
    ft, inch = divmod(whole_in, 12)
    tail = ""
    if rem:
        g = math.gcd(rem, frac)
        tail = f" {rem // g}/{frac // g}"
    return f"{sign}{ft}' {inch}{tail}\""


def fmt_value(v: float, unit: Unit, lang: str = "en") -> str:
    """A value already in `unit`, with its label."""
    if unit.key == "ftin":
        return fmt_ft_in(v)
    if unit.key in _METRIC_LABEL:
        return f"{fmt_number(v, lang, 2)} {unit.label}"
    return f"{fmt_number(v, lang)} {unit.label}"


# ---- reading the question -----------------------------------------------------------------------

_NAMES = "|".join(f"(?P<n_{u.key}>{u.names})" for u in UNITS)
_SYMS = "|".join(f"(?P<s_{u.key}>{u.symbols})" for u in UNITS if u.symbols)
_END = r"(?![\w²³])"
_PREP = r"(?:in|into|en|auf|as)"
_CONVERT = re.compile(r"\b(?:convert\w*|chang\w+|switch\w*|express\w*|umrechn\w+|umwandeln|konvertier\w+|"
                      r"converti\w*|muunna\w*|muuta\w*|translate)\b", re.IGNORECASE)
# "in square feet", "en pieds carrés", "in ft" (also "to inches" in a conversion request)
_REQ = re.compile(rf"(?<![\w'])(?P<prep>{_PREP})\s+(?:{_NAMES}|{_SYMS}){_END}", re.IGNORECASE)
# only in a conversion request ("convert that to inches"): "door to the yard", "zu Fuß" are not units
_REQ_TO = re.compile(rf"(?<![\w'])(?P<prep>{_PREP}|to|zu|nach|a|à)\s+(?:{_NAMES}|{_SYMS}){_END}", re.IGNORECASE)
# "how many square feet", "wie viele Quadratfuß", "combien de pieds carrés", "montako neliöjalkaa"
_HOW_MANY = re.compile(rf"(?P<lead>\b(?:how\s+many|wie\s*viele?|combien\s+de|quant[ie]|kuinka\s+monta|"
                       rf"montako)\s+)(?:{_NAMES}){_END}", re.IGNORECASE)
# Finnish essive forms stand alone: "neliöjalkoina", "tuumina"
_FI_ESSIVE = re.compile(r"(?<!\w)(?P<fi>(?:neliö|kuutio)?(?:jalkoina|tuumina|jaardeina|metreinä|"
                        r"senttimetreinä|millimetreinä|sentteinä)|eekkereinä|hehtaareina|litroina)(?!\w)", re.IGNORECASE)
_SQ_FOOTAGE = re.compile(r"\bsquare\s+footage\b|\bsq\.?\s*footage\b", re.IGNORECASE)
_NUM = r"(?P<num>\d+(?:[.,]\d+)?)"
_QTY = re.compile(rf"(?<![\w.,]){_NUM}\s*-?\s*(?:{_NAMES}|{_SYMS}){_END}", re.IGNORECASE)
_FT_IN_QTY = re.compile(
    r"(?<![\w.,])(?P<ft>\d+(?:\.\d+)?)\s*(?:'|′|ft\.?|feet|foot)\s*-?\s*"
    r"(?P<inch>\d+(?:\.\d+)?)\s*(?:\"|″|''|in\.?(?![a-z])|inch(?:es)?)", re.IGNORECASE)
_PRIME_QTY = re.compile(r"(?<![\w.,'])(?P<v>\d+(?:\.\d+)?)(?P<mark>['′\"″])(?=[\s,.;?!)]|$)")
_IN_SYMBOL_OK = re.compile(r"\s*(?:[?.,;!)]|$|wide|long|high|tall|deep|thick|or\b|and\b|by\b|x\b)", re.IGNORECASE)
_FILLER = re.compile(
    r"\b(?:and|also|now|then|please|pls|ok|okay|what|whats|is|it|that|this|the|same|value|answer|result|"
    r"number|about|how|again|instead|can|could|you|me|give|show|tell|would|be|as|well|too|"
    r"und|auch|jetzt|bitte|das|dies|dieses|noch|mal|wie|viel|ist|sind|wäre|es|ganze|"
    r"et|aussi|maintenant|ça|cela|le|la|s'il|vous|plaît|plait|"
    r"e|anche|ora|per|favore|quello|questo|lo|"
    r"ja|myös|nyt|entä|se|sama|kiitos)\b", re.IGNORECASE)


@dataclass
class UnitPlan:
    question: str               # the metric question the pipeline answers
    unit: Unit | None = None    # the unit to show the answer in (None: metric default)
    pure: bool = False          # nothing but a unit request ("and in inches?"): re-show the last answer
    changed: bool = False       # the question text was rewritten


def _unit_of_match(m: re.Match) -> tuple[Unit, bool]:
    for k, v in m.groupdict().items():
        if v is not None and k[:2] in ("n_", "s_"):
            return BY_KEY[k[2:]], k[0] == "s"
    raise AssertionError("no unit group")


def _metric_qty(v_si: float, dim: int) -> str:
    return f"{float(f'{v_si:.4g}'):g} {_METRIC_LABEL[METRIC_DEFAULT[dim]]}"


def _rewrite_quantities(q: str) -> tuple[str, Unit | None]:
    """Imperial quantities ('200 sq ft', 3'6\", '4 ft') -> metric; the first one's unit is returned."""
    first: list[Unit] = []

    def ft_in(m: re.Match) -> str:
        first.append(BY_KEY["ftin"])
        return _metric_qty(float(m["ft"]) * _FT + float(m["inch"]) * _IN, 1)

    def prime(m: re.Match) -> str:
        before = m.string[:m.start()]
        # a closing quotation mark, not a unit: "Room 12", 'WC 1'
        if m["mark"] == '"' and before.count('"') % 2:
            return m[0]
        if m["mark"] == "'" and len(re.findall(r"(?<!\w)'", before)) % 2:
            return m[0]
        u = BY_KEY["ft" if m["mark"] in "'′" else "in"]
        first.append(u)
        return _metric_qty(float(m["v"]) * u.si, 1)

    def qty(m: re.Match) -> str:
        u, is_symbol = _unit_of_match(m)
        if u.family not in _IMPERIAL:
            return m[0]
        if is_symbol and u.key == "in" and not _IN_SYMBOL_OK.match(m.string, m.end()):
            return m[0]  # "room 3 in apartment A"
        first.append(u)
        return _metric_qty(float(m["num"].replace(",", ".")) * u.si, u.dim)

    q = _FT_IN_QTY.sub(ft_in, q)
    q = _QTY.sub(qty, q)
    q = _PRIME_QTY.sub(prime, q)
    return q, (first[0] if first else None)


def plan(question: str) -> UnitPlan:
    """The unit a question asks for and the metric question to answer instead."""
    q0 = question
    q = _SQ_FOOTAGE.sub("area in square feet", question)
    q, qty_unit = _rewrite_quantities(q)
    is_convert = bool(_CONVERT.search(q))
    asked: list[Unit] = []

    def req(m: re.Match) -> str:
        u, _ = _unit_of_match(m)
        asked.append(u)
        return f"{m['prep']} {_METRIC_LABEL[METRIC_DEFAULT[u.dim]]}"

    def how_many(m: re.Match) -> str:
        u, _ = _unit_of_match(m)
        asked.append(u)
        return f"{m['lead']}{_METRIC_LABEL[METRIC_DEFAULT[u.dim]]}"

    def essive(m: re.Match) -> str:
        w = m["fi"].lower()
        dim = 2 if w.startswith("neliö") else 3 if w.startswith("kuutio") else 1
        stem = w.removeprefix("neliö").removeprefix("kuutio")
        base = {"jalkoina": "ft", "tuumina": "in", "jaardeina": "yd", "metreinä": "m", "senttimetreinä": "cm",
                "sentteinä": "cm", "millimetreinä": "mm", "eekkereinä": "acre", "hehtaareina": "ha",
                "litroina": "l"}[stem]
        u = coerce(BY_KEY[base], dim) or BY_KEY[base]
        asked.append(u)
        return {1: "metreinä", 2: "neliömetreinä", 3: "kuutiometreinä"}[u.dim]

    q = _FI_ESSIVE.sub(essive, _HOW_MANY.sub(how_many, (_REQ_TO if is_convert else _REQ).sub(req, q)))
    # "in m and in ft": the unit that is not already the default is the one asked for
    unit = next((u for u in asked if u.key not in _METRIC_LABEL), asked[0]) if asked else None
    if unit is None and qty_unit is not None:
        unit = qty_unit
    if unit is not None and unit.key in _METRIC_LABEL and not asked[1:]:
        # Metric default asked explicitly: nothing to convert; keep the user's own wording.
        return UnitPlan(q0 if qty_unit is None else q, None, False, qty_unit is not None and q != q0)
    pure = False
    if asked and qty_unit is None:
        rest = _CONVERT.sub(" ", _REQ_TO.sub(" ", _FI_ESSIVE.sub(" ", _rewrite_quantities(
            _SQ_FOOTAGE.sub(" ", q0))[0])))
        rest = _FILLER.sub(" ", rest)
        pure = not re.search(r"\w", rest)
    return UnitPlan(q, unit, pure, q != q0)


# ---- showing a metric response in the requested unit ----------------------------------------------

_PROSE_QTY = re.compile(
    r"(?<![\w.,/])(?P<num>\d{1,3}(?:[,   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)[   ]?"
    r"(?P<u>m²|m2|m³|m3|m)(?![\w²³/])(?!\s*\()")


def _parse_number(s: str) -> float:
    s = re.sub(r"[   ]", "", s)
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        return float(s.replace(",", ""))
    return float(s.replace(",", "."))


def convert_prose(text: str, unit: Unit, lang: str = "en") -> str:
    """'341.42 m²' -> '3,675 sq ft (341.42 m²)' for every metric measure the unit's family covers."""
    def sub(m: re.Match) -> str:
        src = unit_for(m["u"])
        dst = coerce(unit, src.dim) if src else None
        if src is None or dst is None or dst.key == src.key:
            return m[0]
        try:
            v = _parse_number(m["num"])
        except ValueError:
            return m[0]
        return f"{fmt_value(convert(v, src, dst), dst, lang)} ({m[0]})"

    return _PROSE_QTY.sub(sub, text)


def _measure(resp: dict) -> tuple[float, Unit] | None:
    """The response's own number and unit, when it is a measure."""
    ans = resp.get("answer") or {}
    kind = ans.get("kind")
    if kind == "area":
        return float(ans["value"]), BY_KEY["m2"]
    if kind == "quantity":
        u = unit_for(ans.get("unit"))
        return (float(ans["value"]), u) if u else None
    if kind == "note" and ans.get("number") is not None:
        u = unit_for(ans.get("unit"))
        return (float(ans["number"]), u) if u else None
    return None


def has_measure(resp: dict | None) -> bool:
    return bool(resp) and _measure(resp) is not None


def metric_response(resp: dict) -> dict:
    """The response as the pipeline answered it: no `display`, the metric prose restored."""
    old = resp.get("display")
    if not isinstance(old, dict):
        return resp
    out = {k: v for k, v in resp.items() if k != "display"}
    if "metric_explanation" in old:
        out["explanation"] = old["metric_explanation"]
    else:
        out.pop("explanation", None)  # it was only our own "x ft (y m)" line
    if "metric_note" in old and (out.get("answer") or {}).get("kind") == "note":
        out["answer"] = out["answer"] | {"value": old["metric_note"]}
    return out


def metric_turns(history: list[dict] | None) -> list[dict] | None:
    """A conversation as the metric pipeline should see it: unit requests and converted prose in
    earlier turns must not pull a model into answering in feet by itself."""
    if not history:
        return history
    out = []
    for t in history:
        t = dict(t)
        if isinstance(t.get("message"), str):
            t["message"] = plan(t["message"]).question
        if isinstance(t.get("response"), dict):
            t["response"] = metric_response(t["response"])
        out.append(t)
    return out


def apply(resp: dict, unit: Unit | None, lang: str | None = None) -> dict:
    """A copy of a metric response with `display` (the requested unit) and converted prose.

    The metric `answer` is kept as computed, so evidence toggles, CSV and the cache stay metric.
    """
    if unit is None:
        return resp
    lang = lang or resp.get("detected_lang") or "en"
    out = resp = metric_response(resp)  # shown in another unit before: start again from metric
    out = dict(out)
    factors: dict[str, float] = {}
    labels: dict[str, str] = {}
    for dim, mkey in METRIC_DEFAULT.items():
        u = coerce(unit, dim)
        if u is not None and u.key != mkey:
            factors[mkey] = 1.0 / u.si
            labels[mkey] = u.label
    if not factors:
        return out
    display: dict = {"unit": unit.key, "label": unit.label, "factors": factors, "labels": labels,
                     "style": "ft_in" if unit.key == "ftin" else "decimal"}
    measure = _measure(resp)
    if measure is not None:
        v, src = measure
        dst = coerce(unit, src.dim)
        if dst is not None and dst.key != src.key:
            dv = convert(v, src, dst)
            display |= {"unit": dst.key, "label": dst.label, "value": round(dv, 6),
                        "text": fmt_value(dv, dst, lang), "metric_text": fmt_value(v, src, lang),
                        "style": "ft_in" if dst.key == "ftin" else "decimal"}
    if isinstance(out.get("explanation"), str):
        display["metric_explanation"] = out["explanation"]
        out["explanation"] = convert_prose(out["explanation"], unit, lang)
    ans = out.get("answer") or {}
    if ans.get("kind") == "note" and isinstance(ans.get("value"), str):
        display["metric_note"] = ans["value"]
        out["answer"] = ans | {"value": convert_prose(ans["value"], unit, lang)}
    if "text" in display and isinstance(out.get("explanation"), str) and display["text"] not in out["explanation"]:
        out["explanation"] = f"{display['text']} ({display['metric_text']}). {out['explanation']}"
    elif "text" in display and not out.get("explanation"):
        out["explanation"] = f"{display['text']} ({display['metric_text']})."
    if "text" in display and measure is not None and unit.dim != measure[1].dim and unit.dim == 1:
        # asked for a length unit, the answer is an area / a volume: say what was done instead of converting silently
        what = {2: "An area", 3: "A volume"}[measure[1].dim]
        note = f"{what} cannot be given in {unit.label} (a length), so it is shown in {display['label']}."
        out["explanation"] = f"{out['explanation'].rstrip()} {note}"
    out["display"] = display
    return out
