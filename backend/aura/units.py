"""Natural-language unit parsing and conversion (default: metric SI).

Supports:
1. Standalone conversions ("convert 12 m² to sq ft" -> 129.17 sq ft; "convert 250 sq ft" -> 23.23 m²;
   compound feet/inches "14' 6\" in m" -> 4.42 m; "4.42 m to feet and inches" -> 14' 6").
2. Blueprint query target-unit extraction and exact post-conversion ("What is the area of apartment A
   in sq ft?", "How many square feet is room 3.04?", whole-building "All x Floors" sums in sq ft),
   preserving 100% of room/box/group evidence while keeping metric (m, m², m³) as the default.
3. Short conversational follow-ups ("in sq ft?", "convert to inches", "and in metric?") over the
   previous turn's answer and evidence.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from aura import answer as A
from aura.contract import CONTRACT_VERSION, ToolResult
from aura.router import contract_lang

# Exact SI definitions (international yard and pound agreement: 1 in = 0.0254 m, 1 ft = 0.3048 m).
_IN_M = 0.0254
_FT_M = 0.3048
_YD_M = 0.9144


@dataclass(frozen=True)
class UnitSpec:
    canonical: str      # Display unit on AnswerNote.unit (e.g. "sq ft", "in", "m²")
    dim: str            # "length" | "area" | "volume"
    to_si: float        # Multiply by `to_si` to obtain SI base ("m", "m²", "m³")
    is_si_base: bool = False
    compound_ft_in: bool = False


SI_BASE_BY_DIM: dict[str, str] = {
    "length": "m",
    "area": "m²",
    "volume": "m³",
}

UNITS: dict[str, UnitSpec] = {
    # Length (SI base: m)
    "mm": UnitSpec("mm", "length", 0.001),
    "cm": UnitSpec("cm", "length", 0.01),
    "m": UnitSpec("m", "length", 1.0, is_si_base=True),
    "km": UnitSpec("km", "length", 1000.0),
    "in": UnitSpec("in", "length", _IN_M),
    "ft": UnitSpec("ft", "length", _FT_M),
    "ft_in": UnitSpec("ft", "length", _FT_M, compound_ft_in=True),
    "yd": UnitSpec("yd", "length", _YD_M),
    # Area (SI base: m²)
    "mm²": UnitSpec("mm²", "area", 1e-6),
    "cm²": UnitSpec("cm²", "area", 1e-4),
    "m²": UnitSpec("m²", "area", 1.0, is_si_base=True),
    "sq in": UnitSpec("sq in", "area", _IN_M ** 2),
    "sq ft": UnitSpec("sq ft", "area", _FT_M ** 2),
    "sq yd": UnitSpec("sq yd", "area", _YD_M ** 2),
    "ha": UnitSpec("ha", "area", 10_000.0),
    "acre": UnitSpec("acres", "area", 4046.8564224),
    # Volume (SI base: m³)
    "cm³": UnitSpec("cm³", "volume", 1e-6),
    "L": UnitSpec("L", "volume", 0.001),
    "m³": UnitSpec("m³", "volume", 1.0, is_si_base=True),
    "cu in": UnitSpec("cu in", "volume", _IN_M ** 3),
    "cu ft": UnitSpec("cu ft", "volume", _FT_M ** 3),
    "cu yd": UnitSpec("cu yd", "volume", _YD_M ** 3),
    "gal": UnitSpec("gal", "volume", 0.003785411784),
}

# Ordered longest/most-specific first so "sq ft" / "m²" match before "ft" / "m".
_UNIT_PATTERNS: list[tuple[str, str, bool]] = [
    # (canonical_key, regex_body, ambiguous_short_token)
    # Compound feet & inches target
    ("ft_in", r"(?:feet|foot|ft)\s*(?:and|&|/|\+)\s*(?:inches|inch|in\.?)", False),
    # Area
    ("mm²", r"(?:mm[²2]|mm\^2|sq\.?\s*mm|square\s+millimet(?:er|re)s?|quadratmillimeter\w*|neliömillimetri\w*)", False),
    ("cm²", r"(?:cm[²2]|cm\^2|sq\.?\s*cm|square\s+centimet(?:er|re)s?|quadratzentimeter\w*|neliösenttimetri\w*)", False),
    ("m²", r"(?:m[²2]|m\^2|sqm|sq\.?\s*m|qm|square\s+met(?:er|re)s?|m[èe]tres?\s+carr[ée]s?|metri\s+quadrati|quadratmeter\w*|neliömetri\w*|neliöiksi|neliöinä)", False),
    ("sq in", r"(?:sq\.?\s*in(?:ch(?:es)?)?|sqin|in[²2]|in\^2|square\s+inch(?:es)?|pouces?\s+carr[ée]s?|quadratzoll|neliötuum\w*)", False),
    ("sq ft", r"(?:sq\.?\s*ft|sqft|ft[²2]|ft\^2|square\s+f(?:ee|oo)t|pieds?\s+carr[ée]s?|quadratfu(?:ß|ss)|neliöjalk\w*|neliöjaloiksi)", False),
    ("sq yd", r"(?:sq\.?\s*yd|sqyd|yd[²2]|yd\^2|square\s+yards?|neliöjaardi\w*)", False),
    ("acre", r"(?:acres?|eekkeri\w*)", False),
    ("ha", r"(?:hectares?|hektar\w*|hehtaari\w*)", False),
    ("ha", r"ha", True),
    # Volume
    ("cm³", r"(?:cm[³3]|cm\^3|cc|millilit(?:er|re)s?|ml)", False),
    ("m³", r"(?:m[³3]|m\^3|cbm|cu\.?\s*m|cubic\s+met(?:er|re)s?|m[èe]tres?\s+cubes?|kubikmeter\w*|kuutiometri\w*)", False),
    ("cu in", r"(?:cu\.?\s*in(?:ch(?:es)?)?|in[³3]|in\^3|cubic\s+inch(?:es)?|kubikzoll|kuutiotuum\w*)", False),
    ("cu ft", r"(?:cu\.?\s*ft|cft|ft[³3]|ft\^3|cubic\s+f(?:ee|oo)t|pieds?\s+cubes?|kubikfu(?:ß|ss)|kuutiojalk\w*)", False),
    ("cu yd", r"(?:cu\.?\s*yd|yd[³3]|yd\^3|cubic\s+yards?)", False),
    ("gal", r"(?:gallons?|gal)", False),
    ("L", r"(?:lit(?:er|re)s?|litra\w*)", False),
    ("L", r"[lL]", True),
    # Length
    ("mm", r"(?:millimet(?:er|re)s?|millimetri\w*|mm)", False),
    ("cm", r"(?:centimet(?:er|re)s?|zentimeter\w*|senttimetri\w*|cm)", False),
    ("km", r"(?:kilomet(?:er|re)s?|kilom[èe]tres?|kilometri\w*|km)", False),
    ("in", r"(?:inch(?:es)?|pouces?|zoll|tuumaa?|tuumaksi|tuumina)", False),
    ("in", r"in\.?", True),
    ("ft", r"(?:f(?:ee|oo)t|pieds?|fu(?:ß|ss)|jalkaa?|jaloiksi|jalkoina|ft\.?)", False),
    ("yd", r"(?:yards?|yds?|jaardi\w*)", False),
    ("m", r"(?:met(?:er|re)s?|m[èe]tres?|metri\w*)", False),
    ("m", r"m", True),
]

_BOUND_L = r"(?<![A-Za-z0-9_²³])"
_BOUND_R = r"(?![A-Za-z0-9_²³])"

_COMPILED_UNITS: list[tuple[str, re.Pattern[str], bool]] = [
    (key, re.compile(_BOUND_L + pat + _BOUND_R, re.IGNORECASE), amb)
    for key, pat, amb in _UNIT_PATTERNS
]

_ALL_UNIT_UNION = "|".join(f"(?:{pat})" for _, pat, _ in _UNIT_PATTERNS)
_UNAMBIGUOUS_UNIT_UNION = "|".join(f"(?:{pat})" for _, pat, amb in _UNIT_PATTERNS if not amb)

_METRIC_WORD_RE = re.compile(
    _BOUND_L + r"(?:metric(?:\s+units?)?|si(?:\s+units?)?|metrisch\w*|m[ée]trique\w*|metri(?:nen|järjestelmä\w*))" + _BOUND_R,
    re.IGNORECASE,
)

# Blueprint domain terms that mean the question is about a drawing, NOT a pure calculator prompt.
_DRAWING_DOMAIN_RE = re.compile(
    r"\b(?:"
    r"rooms?|bedrooms?|kitchens?|bath(?:room)?s?|living|dining|lounge|corridors?|hallways?|halls?|"
    r"balcon(?:y|ies)|terraces?|loggias?|closets?|storage|stairs?|staircases?|stairwells?|"
    r"apartments?|flats?|dwellings?|units?|floors?|storeys?|stories|levels?|buildings?|houses?|"
    r"sheets?|plans?|drawings?|blueprints?|pages?|walls?|slabs?|doors?|windows?|wcs?|toilets?|"
    r"sinks?|basins?|washbasins?|bathtubs?|tubs?|showers?|saunas?|"
    r"pi[èe]ces?|chambres?|cuisines?|bains?|s[ée]jours?|couloirs?|appartements?|logements?|[ée]tages?|b[âa]timents?|portes?|fen[êe]tres?|"
    r"zimmer|r[äa]ume|raum|k[üu]chen?|badezimmer|bad|wohnzimmer|flur|korridor|balkon|wohnung\w*|geschoss\w*|etagen?|geb[äa]ude|t[üu]ren?|fenster|wand\w*|"
    r"huone\w*|makuuhuone\w*|keitti\w*|kylpyhuone\w*|olohuone\w*|parvek\w*|asunto\w*|kerros\w*|rakennus\w*|ov(?:i|et|ia|ea)|ikkun\w*|"
    r"total|net|gross|overall|largest|biggest|smallest|widest|narrowest|longest|shortest|"
    r"width|length|height|depth|perimeter|area\s+of|size\s+of|dimensions?\b|"
    r"larger\s+than|smaller\s+than|more\s+than|less\s+than|greater\s+than|over|under|above|below|at\s+least|at\s+most|"
    r"price\w*|cost\w*|budget"
    r")\b|[$€£]|[<>]=?|[≥≤]",
    re.IGNORECASE,
)

_CONVERT_VERB_RE = re.compile(
    r"\b(?:convert\w*|conversion|umrechn\w*|konvertier\w*|converti\w*|muunna\w*|muuta\w*)\b",
    re.IGNORECASE,
)

# Compound feet + inches value: e.g. 14' 6", 14'6", 14'-6", 14 ft 6 in, 14 feet 6.5 inches
_COMPOUND_FT_IN_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<sign>-?)(?P<ft>\d+(?:[.,]\d+)?)\s*(?:['′]|(?:\s*(?:ft\.?|f(?:ee|oo)t)\b))"
    r"(?:\s*[-–]?\s*(?P<in>\d+(?:[.,]\d+)?)\s*(?:[\"″]|(?:\s*(?:in\.?|inch(?:es)?)\b))?)?",
    re.IGNORECASE,
)

# Single number + unit: e.g. 12 m², 12.5 sq ft, 36", 4,20 m
_NUM_WITH_QUOTES_IN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<q>[\"″])(?![A-Za-z0-9_])"
)
_NUM_WITH_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<unit>{_ALL_UNIT_UNION}){_BOUND_R}",
    re.IGNORECASE,
)

_TO_PREP_RE = re.compile(
    r"^\s*(?:to|in|into|as|=|->|→|en|nach|auf|in\s+das|iksi)\s+(?P<rest>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_HOW_MANY_TARGET_FIRST_RE = re.compile(
    rf"^\s*(?:how\s+many|what\s+is\s+in|wie\s+viele?|combien\s+de|montako|kuinka\s+monta)\s+"
    rf"(?P<dst>{_ALL_UNIT_UNION})\s+"
    rf"(?:are\s+there\s+in|are\s+in|is\s+in|in|is|are|equals?|sind(?:\s+in)?|dans|font|on)\s+"
    rf"(?P<src>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def _parse_float(raw: str) -> float:
    return float(raw.strip().replace(",", "."))


def round_converted(v: float) -> float:
    if v == 0:
        return 0.0
    av = abs(v)
    if av >= 0.1:
        return round(v, 2)
    if av >= 0.001:
        return round(v, 4)
    return round(v, 6)


def _fmt_num(v: float) -> str:
    r = round_converted(v)
    if float(r).is_integer() and abs(r) >= 1:
        # Keep 2 decimals for non-integers, clean integer for exact whole inputs
        return f"{int(r)}"
    if abs(r) >= 0.1:
        return f"{r:.2f}".rstrip("0").rstrip(".") if False else f"{r:.2f}"
    return f"{r:g}"


def _fmt_input_num(v: float) -> str:
    if float(v).is_integer():
        return str(int(v))
    return f"{v:g}"


def format_feet_inches(meters: float) -> str:
    sign = "-" if meters < 0 else ""
    total_inches = abs(meters) / _IN_M
    whole_ft = int(total_inches // 12)
    rem_in = round(total_inches - whole_ft * 12, 1)
    if rem_in >= 12.0:
        whole_ft += 1
        rem_in = 0.0
    in_txt = str(int(rem_in)) if float(rem_in).is_integer() else f"{rem_in:.1f}"
    return f"{sign}{whole_ft}' {in_txt}\""


def resolve_unit_token(token: str, *, allow_ambiguous: bool = True) -> UnitSpec | None:
    s = unicodedata.normalize("NFC", token or "").strip().rstrip("?.!,;:")
    if not s:
        return None
    if s in ('"', "″"):
        return UNITS["in"]
    if s in ("'", "′"):
        return UNITS["ft"]
    for key, rx, amb in _COMPILED_UNITS:
        if not allow_ambiguous and amb:
            continue
        if rx.fullmatch(s):
            return UNITS[key]
    return None


def _parse_source_quantity(text: str) -> tuple[float, UnitSpec, str, str] | None:
    """Parse `<value> <unit>` (including `14' 6"` or `36"`) at the start of `text`.
    Returns `(si_value, src_spec, formatted_src, trailing_text)`."""
    s = text.strip()
    # 1. Compound feet/inches (e.g. `14' 6"`, `14 ft 6 in`, `14'`)
    m_ft = _COMPOUND_FT_IN_RE.match(s)
    if m_ft:
        rest = s[m_ft.end():].strip()
        # Make sure `14 ft²` wasn't partially matched as `14 ft` + `²`!
        if not rest.startswith(("²", "2", "³", "3", "^")):
            sign = -1.0 if m_ft.group("sign") == "-" else 1.0
            ft_val = _parse_float(m_ft.group("ft"))
            in_raw = m_ft.group("in")
            in_val = _parse_float(in_raw) if in_raw is not None else 0.0
            si_m = sign * (ft_val * _FT_M + in_val * _IN_M)
            if in_raw is not None:
                label = f"{'-' if sign < 0 else ''}{_fmt_input_num(ft_val)}' {_fmt_input_num(in_val)}\""
            else:
                label = f"{'-' if sign < 0 else ''}{_fmt_input_num(ft_val)} ft"
            return si_m, UNITS["ft"], label, rest

    # 2. Quote inches (`36"`)
    m_qin = _NUM_WITH_QUOTES_IN_RE.match(s)
    if m_qin:
        val = _parse_float(m_qin.group("num"))
        rest = s[m_qin.end():].strip()
        return val * _IN_M, UNITS["in"], f'{_fmt_input_num(val)}"', rest

    # 3. Standard `<num> <unit>`
    m_u = _NUM_WITH_UNIT_RE.match(s)
    if m_u:
        val = _parse_float(m_u.group("num"))
        spec = resolve_unit_token(m_u.group("unit"), allow_ambiguous=True)
        if spec is not None:
            rest = s[m_u.end():].strip()
            return val * spec.to_si, spec, f"{_fmt_input_num(val)} {spec.canonical}", rest
    return None


def convert_si(si_value: float, dst: UnitSpec) -> tuple[float, str, str]:
    """Convert `si_value` into `dst`. Returns `(rounded_number, unit_label, formatted_rhs)`."""
    if dst.compound_ft_in:
        dec_ft = round_converted(si_value / _FT_M)
        ft_in_str = format_feet_inches(si_value)
        return dec_ft, "ft", f"{ft_in_str} ({_fmt_num(dec_ft)} ft)"
    raw = si_value / dst.to_si
    num = round_converted(raw)
    return num, dst.canonical, f"{_fmt_num(num)} {dst.canonical}"


def _build_conversion_response(
    question: str,
    lang: str,
    *,
    number: float,
    unit: str,
    value_text: str,
    canonical_en: str,
    intent_body: str,
    scope: dict | None = None,
    evidence: dict[str, Any] | None = None,
    method: str = "cad_geometry",
) -> A.Built:
    eff_scope = scope or {"kind": "floor"}
    ev = evidence if evidence is not None else {"boxes": [], "rooms": []}
    body: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "detected_lang": contract_lang(lang),
        "scope": eff_scope,
        "calls": 0,
        "cost_usd": 0.0,
        "source": "api",
        "answer": {"kind": "note", "value": value_text, "number": number, "unit": unit},
        "evidence": ev,
        "canonical_en": canonical_en,
        "method": method,
        "trace": [
            A.question_node(question, lang),
            A.intent_node(intent_body, "router · units"),
            {
                "id": "convert",
                "parent": "q",
                "title": "Convert units",
                "tag": "exact · SI definition",
                "body": f"Exact unit conversion: {value_text} (0 model calls).",
                "kind": "exact",
            },
            {
                "id": "answer",
                "parent": "q",
                "title": "Answer",
                "tag": unit,
                "body": value_text,
                "kind": "exact",
                "stage": "answer",
                "detail": {"source": "unit_conversion", "calls": 0, "cost_usd": 0.0},
            },
        ],
    }
    return A.Built(body, [], [], None)


# ---- 1. Standalone conversion ---------------------------------------------------------------


def try_standalone_conversion(question: str, lang: str = "en") -> A.Built | None:
    """Handle pure unit-conversion prompts ("convert 12 m² to sq ft", "250 sq ft in metric",
    "14' 6\" to m", "convert 36 inches"). Returns None when the prompt references drawing objects."""
    q = unicodedata.normalize("NFC", question or "").strip()
    if not q or _DRAWING_DOMAIN_RE.search(q):
        return None

    # Pattern A: "how many <dst_unit> are in <value> <src_unit>?"
    m_hm = _HOW_MANY_TARGET_FIRST_RE.match(q)
    if m_hm:
        dst_spec = resolve_unit_token(m_hm.group("dst"), allow_ambiguous=True)
        parsed = _parse_source_quantity(m_hm.group("src"))
        if dst_spec is not None and parsed is not None and not parsed[3]:
            si_val, src_spec, src_txt, _ = parsed
            if src_spec.dim != dst_spec.dim:
                return A.abstain(
                    q, lang, {"kind": "floor"}, "unclear_question", q,
                    f"Cannot convert {src_spec.dim} ({src_spec.canonical}) to {dst_spec.dim} ({dst_spec.canonical}).",
                    intent_tag="router · units",
                )
            num, unit_lbl, rhs = convert_si(si_val, dst_spec)
            txt = f"{src_txt} = {rhs}"
            return _build_conversion_response(
                q, lang,
                number=num,
                unit=unit_lbl,
                value_text=txt,
                canonical_en=f"Convert {src_txt} to {unit_lbl}",
                intent_body=f"unit conversion · {src_spec.canonical} → {unit_lbl}",
            )

    # Pattern B: "[convert / what is / how much is] <value> <src_unit> [to/in <dst_unit> | to metric]"
    has_convert_cue = bool(_CONVERT_VERB_RE.search(q))
    cleaned = re.sub(
        r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:convert|umrechnen|konvertiere\w*|convertir|muunna|muuta|"
        r"what\s+is|what['’]s|how\s+much\s+is|was\s+sind|was\s+ist|combien\s+font|paljonko\s+on)\s+",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()

    parsed = _parse_source_quantity(cleaned)
    if parsed is None:
        return None
    si_val, src_spec, src_txt, rest = parsed
    rest_clean = rest.rstrip("?.! ").strip()

    dst_spec: UnitSpec | None = None
    if not rest_clean:
        # No target unit named -> default to metric SI when an explicit convert cue was given
        # (e.g. "convert 250 sq ft" -> 23.23 m²; "convert 14' 6\"" -> 4.42 m).
        if not has_convert_cue:
            return None
        dst_spec = UNITS[SI_BASE_BY_DIM[src_spec.dim]]
    else:
        m_to = _TO_PREP_RE.match(rest_clean)
        target_str = m_to.group("rest").strip() if m_to else None
        if target_str is None:
            return None
        if _METRIC_WORD_RE.fullmatch(target_str):
            dst_spec = UNITS[SI_BASE_BY_DIM[src_spec.dim]]
        else:
            dst_spec = resolve_unit_token(target_str, allow_ambiguous=True)
            if dst_spec is None:
                return None

    if src_spec.dim != dst_spec.dim:
        return A.abstain(
            q, lang, {"kind": "floor"}, "unclear_question", q,
            f"Cannot convert {src_spec.dim} ({src_spec.canonical}) to {dst_spec.dim} ({dst_spec.canonical}).",
            intent_tag="router · units",
        )

    num, unit_lbl, rhs = convert_si(si_val, dst_spec)
    txt = f"{src_txt} = {rhs}"
    return _build_conversion_response(
        q, lang,
        number=num,
        unit=unit_lbl,
        value_text=txt,
        canonical_en=f"Convert {src_txt} to {unit_lbl}",
        intent_body=f"unit conversion · {src_spec.canonical} → {unit_lbl}",
    )


def is_standalone_conversion(question: str) -> bool:
    return try_standalone_conversion(question, "en") is not None


# ---- 2. Follow-up conversion over previous chat turn ----------------------------------------

_FOLLOWUP_UNIT_RE = re.compile(
    r"^\s*(?:and\s+|what\s+about\s+|how\s+about\s+|now\s+|please\s+|et\s+|und\s+|entä\s+)?"
    r"(?:convert(?:\s+(?:it|that|this|them))?\s+(?:to|into|in)|(?:in|into|to|as|en|nach|auf))\s+"
    r"(?P<dst>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def _prev_measurement(resp: dict[str, Any]) -> tuple[float, UnitSpec] | None:
    ans = resp.get("answer") or {}
    kind = ans.get("kind")
    if kind == "area" and ans.get("value") is not None:
        return float(ans["value"]), UNITS["m²"]
    if kind == "quantity" and ans.get("value") is not None:
        u_map = {"m": "m", "m2": "m²", "m3": "m³"}
        u_key = u_map.get(str(ans.get("unit") or "m"))
        if u_key:
            return float(ans["value"]), UNITS[u_key]
    if kind == "note" and ans.get("number") is not None and ans.get("unit"):
        u_raw = str(ans["unit"])
        if u_raw == "m2":
            u_raw = "m²"
        elif u_raw == "m3":
            u_raw = "m³"
        spec = resolve_unit_token(u_raw, allow_ambiguous=True)
        if spec is not None:
            return float(ans["number"]) * spec.to_si, spec
    return None


def try_followup_conversion(
    question: str,
    lang: str,
    history: list[dict[str, Any]] | None,
) -> A.Built | None:
    """Convert the previous turn's measurement when the user asks a short unit follow-up
    like 'in sq ft?', 'convert to inches', or 'and in metric?'."""
    if not history:
        return None
    q = unicodedata.normalize("NFC", question or "").strip()
    if len(q.split()) > 8 or _DRAWING_DOMAIN_RE.search(q):
        return None
    m = _FOLLOWUP_UNIT_RE.match(q)
    if not m:
        return None
    target_raw = m.group("dst").strip()

    prev_resp = next(
        (t["response"] for t in reversed(history) if isinstance(t.get("response"), dict)),
        None,
    )
    if prev_resp is None:
        return None
    meas = _prev_measurement(prev_resp)
    if meas is None:
        return None
    si_val, src_spec = meas

    if _METRIC_WORD_RE.fullmatch(target_raw):
        dst_spec = UNITS[SI_BASE_BY_DIM[src_spec.dim]]
    else:
        dst_spec = resolve_unit_token(target_raw, allow_ambiguous=True)
    if dst_spec is None or dst_spec.dim != src_spec.dim:
        return None

    num, unit_lbl, rhs = convert_si(si_val, dst_spec)
    base_spec = UNITS[SI_BASE_BY_DIM[src_spec.dim]]
    _, _, base_txt = convert_si(si_val, base_spec)
    value_text = rhs if dst_spec.is_si_base else f"{rhs} ({base_txt})"
    prev_ev = copy.deepcopy(prev_resp.get("evidence") or {"boxes": [], "rooms": []})
    prev_scope = copy.deepcopy(prev_resp.get("scope") or {"kind": "floor"})
    prev_method = str(prev_resp.get("method") or "cad_geometry")
    if prev_method == "none":
        prev_method = "cad_geometry"
    return _build_conversion_response(
        q, lang,
        number=num,
        unit=unit_lbl,
        value_text=value_text,
        canonical_en=f"Convert previous {src_spec.dim} result to {unit_lbl}",
        intent_body=f"follow-up unit conversion · → {unit_lbl}",
        scope=prev_scope,
        evidence=prev_ev,
        method=prev_method,
    )


# ---- 3. Target-unit extraction & post-conversion on blueprint queries -----------------------

# Trailing unit clause: "... in sq ft?", "... to inches", "... (sq ft)"
_TRAILING_TARGET_UNIT_RE = re.compile(
    rf"(?P<clause>"
    rf"(?:\s*,?\s*(?:\bin|\binto|\bto|\bas|\ben|\bnach|\bauf)\s+(?P<u1>{_ALL_UNIT_UNION}|metric(?:\s+units?)?))"
    rf"|(?:\s*\(\s*(?:in\s+)?(?P<u2>{_UNAMBIGUOUS_UNIT_UNION})\s*\))"
    rf")\s*(?P<punct>[?.!]*)\s*$",
    re.IGNORECASE,
)

# Leading "How many square feet is/are <rest>" or "How many inches wide is <rest>"
_LEADING_HOW_MANY_UNIT_RE = re.compile(
    rf"^\s*(?:how\s+many|wie\s+viele?|combien\s+de|montako|kuinka\s+monta)\s+"
    rf"(?P<u3>{_UNAMBIGUOUS_UNIT_UNION})\s+"
    rf"(?P<verb>(?:is|are|does|measure\w*|wide\b|long\b|deep\b|tall\b|high\b|hat|haben|sind|ist|fait|font|mesure\w*|on)\b.*)$",
    re.IGNORECASE,
)

_SQUARE_FOOTAGE_RE = re.compile(r"\bsquare\s+footage\b", re.IGNORECASE)


def extract_target_unit(question: str) -> tuple[UnitSpec | None, str]:
    """Detect a requested output unit on a blueprint question.

    Returns `(target_unit_spec_or_None, metric_normalized_question)`.
    - Default is metric (`None`), leaving standard `AnswerArea` (`m2`) and `AnswerQuantity` (`m`, `m2`, `m3`)
      untouched when no non-SI unit is requested.
    - Never confuses numeric thresholds (`larger than 200 sq ft`) with output units.
    """
    q = unicodedata.normalize("NFC", question or "").strip()
    if not q or is_standalone_conversion(q):
        return None, q

    # 1. "square footage of X" -> ("sq ft", "area of X")
    if _SQUARE_FOOTAGE_RE.search(q):
        norm_q = _SQUARE_FOOTAGE_RE.sub("area", q)
        # Strip any redundant trailing "in sq ft" if also present
        _, norm_q2 = extract_target_unit(norm_q)
        return UNITS["sq ft"], norm_q2

    # 2. Leading "How many square feet is room 3.04?" / "How many inches wide is the kitchen?"
    m_lead = _LEADING_HOW_MANY_UNIT_RE.match(q)
    if m_lead:
        spec = resolve_unit_token(m_lead.group("u3"), allow_ambiguous=False)
        if spec is not None:
            rest = m_lead.group("verb").strip()
            if spec.dim == "area":
                # Rewrite to canonical area question while preserving the subject/scope clause
                rest_stripped = re.sub(r"^(?:is|are|hat|sind|ist|fait|font|on)\s+", "", rest, flags=re.IGNORECASE)
                norm_q = f"What is the area of {rest_stripped}"
            else:
                norm_q = f"What is the measurement {rest}"
            return (None if spec.is_si_base else spec), norm_q

    # 3. Trailing "... in sq ft?" / "... to inches" / "... (sq ft)"
    m_trail = _TRAILING_TARGET_UNIT_RE.search(q)
    if m_trail:
        raw_u = (m_trail.group("u1") or m_trail.group("u2") or "").strip()
        prefix = q[:m_trail.start("clause")].rstrip()
        punct = m_trail.group("punct") or ""
        # Guard against thresholds like "rooms over 20" (not possible here since preposition/paren is required,
        # and ensure prefix isn't empty).
        if prefix:
            if _METRIC_WORD_RE.fullmatch(raw_u):
                return None, prefix + punct
            spec = resolve_unit_token(raw_u, allow_ambiguous=True)
            if spec is not None:
                if spec.is_si_base:
                    # Standard metric requested ("in m²", "in m"): keep original question untouched!
                    return None, q
                return spec, prefix + punct

    return None, q


def apply_target_unit_to_response_dict(
    body: dict[str, Any],
    dst: UnitSpec | None,
    original_question: str | None = None,
) -> dict[str, Any]:
    """Convert `body['answer']` (and `body['breakdown']` if present) into `dst` in-place and return `body`."""
    if dst is None or dst.is_si_base:
        return body

    ans = body.get("answer") or {}
    kind = ans.get("kind")
    if kind == "abstain" or kind == "count" or kind == "text":
        return body

    si_val: float | None = None
    src_dim: str | None = None
    metric_txt: str | None = None

    if kind == "area" and ans.get("value") is not None:
        src_dim = "area"
        si_val = float(ans["value"])
        metric_txt = f"{si_val:.2f} m²"
    elif kind == "quantity" and ans.get("value") is not None:
        u_map = {"m": ("length", "m"), "m2": ("area", "m²"), "m3": ("volume", "m³")}
        dim_u = u_map.get(str(ans.get("unit") or "m"))
        if dim_u:
            src_dim, u_disp = dim_u
            si_val = float(ans["value"])
            metric_txt = f"{si_val:.2f} {u_disp}"
    elif kind == "note":
        if ans.get("number") is not None and ans.get("unit"):
            u_raw = str(ans["unit"])
            u_norm = {"m2": "m²", "m3": "m³"}.get(u_raw, u_raw)
            src_spec = resolve_unit_token(u_norm, allow_ambiguous=True)
            if src_spec is not None and src_spec.dim == dst.dim:
                src_dim = src_spec.dim
                si_val = float(ans["number"]) * src_spec.to_si
                base_u = SI_BASE_BY_DIM[src_dim]
                metric_txt = f"{si_val:.2f} {base_u}"

    if si_val is None or src_dim != dst.dim:
        return body

    num, unit_lbl, rhs = convert_si(si_val, dst)
    if kind == "note" and ans.get("value") and "largest" in str(ans.get("value")).lower() or (
        kind == "note" and ans.get("value") and "smallest" in str(ans.get("value")).lower()
    ):
        # Preserve room_rank descriptive sentence while appending converted unit
        note_val = f"{rhs} — {ans['value']}"
    else:
        note_val = f"{rhs} ({metric_txt})" if metric_txt else rhs

    body["answer"] = {
        "kind": "note",
        "value": note_val,
        "number": num,
        "unit": unit_lbl,
    }
    # Gold dataset comparisons apply only to exact metric dataset answers
    body.pop("gold", None)

    # Convert per-sheet breakdown items when present (e.g. All x Floors)
    if isinstance(body.get("breakdown"), list):
        for item in body["breakdown"]:
            if item.get("status") == "ok" and item.get("value") is not None:
                b_unit = str(item.get("unit") or "")
                b_norm = {"m2": "m²", "m3": "m³"}.get(b_unit, b_unit)
                b_spec = resolve_unit_token(b_norm, allow_ambiguous=True)
                if b_spec is not None and b_spec.dim == dst.dim:
                    b_si = float(item["value"]) * b_spec.to_si
                    b_num, b_lbl, _ = convert_si(b_si, dst)
                    item["value"] = b_num
                    item["unit"] = b_lbl

    if original_question and isinstance(body.get("trace"), list):
        if body["trace"] and body["trace"][0].get("id") == "q":
            body["trace"][0]["body"] = f"“{original_question}”"
        body["trace"].append({
            "id": "unit-convert",
            "parent": "q",
            "title": "Convert units",
            "tag": f"SI → {unit_lbl}",
            "body": f"Converted {metric_txt} → {rhs} using exact SI conversion.",
            "kind": "exact",
        })
    return body


def apply_target_unit_to_built(
    built: A.Built,
    dst: UnitSpec | None,
    original_question: str | None = None,
) -> A.Built:
    if dst is None or dst.is_si_base:
        return built
    apply_target_unit_to_response_dict(built.body, dst, original_question=original_question)
    return built
