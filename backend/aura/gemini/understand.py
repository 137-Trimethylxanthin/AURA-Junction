"""Router-miss understanding with one Decisions request (E3/E4 in gemini-usecases.md).

`choice` questions pick intent, count subject and scope among options built from the drawing;
a `noul` gate asks whether the drawing can answer at all. `choice` cannot emit numbers, so this
step can never put a number into an answer. Cost ≈ $0.00005 per question (E3).
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from aura.gemini.client import GeminiClient, Meter, image_part, text_part

MIN_CONFIDENCE = 0.6  # E2/E3: accepted answers ≥ 0.6 were right 22/23
GATE_OFF = 0.4  # E4: off-drawing facts ≤ 0.19, on-drawing facts ≥ 0.63
EXACT_SUFFICIENT_GATE = 0.80
CACHE_MATCH_GATE = 0.85
HYBRID_OFF_DRAWING_GATE = 0.30

INTENTS = {
    "count": "Asks how many items there are (a number of objects, rooms or apartments).",
    "area": "Asks for a surface or floor area, or how big a room or apartment is.",
    "length": "Asks for a length, width, height, distance or volume.",
    "text": "Asks for a name, type, material, rating, list or other non-numeric fact.",
    "other": "Not a question about a building or its drawing.",
}
SUBJECTS = {
    "window": "windows",
    "door": "doors",
    "washbasin": "washbasins or sinks",
    "wc": "toilets or WC seats",
    "bathtub": "bathtubs",
    "shower": "showers",
    "room": "rooms of some kind (bedrooms, bathrooms, kitchens...)",
    "apartment": "apartments or dwelling units",
    "other": "none of these, or not a count",
}


@dataclass
class Understood:
    intent: str | None
    intent_conf: float
    subject: str | None
    subject_conf: float
    scope: dict | None
    scope_conf: float
    on_drawing: float | None
    calls: int = 1
    cost_usd: float = 0.0
    input_tokens: int = 0
    latency_ms: int = 0
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def confident(self) -> bool:
        return self.intent is not None and self.intent_conf >= MIN_CONFIDENCE

    @property
    def off_drawing(self) -> bool:
        return self.on_drawing is not None and self.on_drawing < GATE_OFF


def scope_options(units: list[str], labels: list[str]) -> tuple[dict[str, str], dict[str, dict]]:
    crit: dict[str, str] = {"floor": "the whole floor plan, or no particular part of it"}
    to_scope: dict[str, dict] = {"floor": {"kind": "floor"}}
    for u in units:
        crit[f"unit_{u}"] = f"apartment {u}"
        to_scope[f"unit_{u}"] = {"kind": "unit", "unit": u}
    for i, lab in enumerate(dict.fromkeys(labels)):
        key = f"label_{i:02d}"
        crit[key] = f"the rooms labelled '{lab}'"
        to_scope[key] = {"kind": "label", "label": lab}
    return crit, to_scope


def valid_choices(questions: dict[str, dict]) -> dict[str, dict]:
    """The Decisions API refuses a choice question with fewer than 2 options (HTTP 400, measured on
    uploads without rooms or units: scope had only "floor"). Such a question has nothing to decide,
    so it is left out and its reader falls back (scope None = the whole drawing)."""
    return {k: q for k, q in questions.items()
            if q.get("type") != "choice" or len(q.get("criteria") or {}) >= 2}


def build_request(question: str, units: list[str], labels: list[str],
                  image: bytes | None) -> tuple[list[dict], dict, dict[str, dict]]:
    parts = [text_part(f"A user asked this question about the construction floor plan below:\n"
                       f"{question.strip()}\n")]
    if image is not None:
        parts.append(image_part(image, "low"))
    crit, to_scope = scope_options(units, labels)
    questions = {
        "intent": {"type": "choice", "instructions": "What does the question ask for?",
                   "criteria": INTENTS},
        "subject": {"type": "choice",
                    "instructions": "If the question asks for a count, what is being counted?",
                    "criteria": SUBJECTS},
        "scope": {"type": "choice",
                  "instructions": "Which part of the floor plan is the question about?",
                  "criteria": crit},
        "on_drawing": {"type": "noul",
                       "instructions": "Is the information needed to answer the question shown "
                                       "on this drawing?"},
    }
    return parts, valid_choices(questions), to_scope


async def understand(client: GeminiClient, question: str, *, units: list[str],
                     labels: list[str], image_path: Path | None, ip_hash: str | None,
                     presenter: bool, meter: Meter | None = None) -> Understood:
    image = (await asyncio.to_thread(image_path.read_bytes)
             if image_path is not None and image_path.is_file() else None)
    parts, questions, to_scope = build_request(question, units, labels, image)
    res = await client.decisions(parts, questions, "choice", ip_hash=ip_hash, presenter=presenter,
                                 meter=meter)
    intent, ic, ip = res.choice("intent")
    subject, sc, sp = res.choice("subject")
    scope_key, scc, scp = res.choice("scope")
    return Understood(
        intent=intent, intent_conf=ic,
        subject=None if subject in (None, "other") else subject, subject_conf=sc,
        scope=to_scope.get(scope_key or ""), scope_conf=scc,
        on_drawing=res.noul("on_drawing"),
        cost_usd=res.cost_usd, input_tokens=res.input_tokens, latency_ms=res.latency_ms,
        probabilities={"intent": ip, "subject": sp, "scope": scp},
    )


_LD = r"[^\W_]"
_WB = r"(?:(?<![\w-])|(?<=_))"
_QUALIFIER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("dir:north", re.compile(
        rf"{_WB}(?:north(?:ern)?|north[- ]facing|nord|norden|pohjoinen|pohjois\w*|"
        rf"nord(?:seite|fassade|fenster|tür\w*))(?!{_LD})", re.IGNORECASE)),
    ("dir:south", re.compile(
        rf"{_WB}(?:south(?:ern)?|south[- ]facing|sud|süden|etelä\w*|"
        rf"süd(?:seite|fassade|fenster|tür\w*))(?!{_LD})", re.IGNORECASE)),
    ("dir:east", re.compile(
        rf"{_WB}(?:east(?:ern)?|east[- ]facing|osten|itä(?:ikkun\w*|ov\w*|puol\w*|siv\w*|julki\w*|inen|än)?|"
        rf"ost(?:seite|fassade|fenster|tür\w*))(?!{_LD})|"
        r"(?:\borienté\w*\s+(?:à\s+l['’])?est\b|\bcôté\s+est\b|\bfa[çc]ade\s+est\b|\bà\s+l['’]est\b|\bvers\s+l['’]est\b)",
        re.IGNORECASE)),
    ("dir:west", re.compile(
        rf"{_WB}(?:west(?:ern)?|west[- ]facing|ouest|westen|länsi\w*|läntinen|"
        rf"west(?:seite|fassade|fenster|tür\w*))(?!{_LD})", re.IGNORECASE)),
    ("sub:double", re.compile(
        rf"{_WB}(?:double|portes?\s+doubles?|pariov\w*|doppeltür\w*|french\s+doors?)(?!{_LD})",
        re.IGNORECASE)),
    ("sub:single", re.compile(rf"{_WB}single(?!{_LD})", re.IGNORECASE)),
    ("sub:sliding", re.compile(
        rf"{_WB}(?:sliding|portes?\s+couliss\w*|liukuov\w*|schiebetür\w*)(?!{_LD})",
        re.IGNORECASE)),
    ("sub:pocket", re.compile(rf"{_WB}pocket(?!{_LD})", re.IGNORECASE)),
    ("sub:folding", re.compile(rf"{_WB}folding(?!{_LD})", re.IGNORECASE)),
    ("loc:exterior", re.compile(
        rf"{_WB}(?:exterior|external|outer|portes?\s+ext\w*|ulko-?ov\w*|außentür\w*|aussentür\w*)(?!{_LD})",
        re.IGNORECASE)),
    ("loc:interior", re.compile(
        rf"{_WB}(?:interior|internal|inner|portes?\s+int\w*|sisäov\w*|innentür\w*)(?!{_LD})",
        re.IGNORECASE)),
    ("loc:entrance", re.compile(
        rf"{_WB}(?:entrance|entry|front\s+doors?|haustür\w*)(?!{_LD})", re.IGNORECASE)),
    ("acc:accessible", re.compile(
        rf"{_WB}(?:accessible|wheelchair|handicap\w*|barrier[- ]free|esteet\w*|barrierefrei\w*)(?!{_LD})",
        re.IGNORECASE)),
    ("pos:left", re.compile(rf"{_WB}(?:left|gauche|vasen\w*|links)(?!{_LD})", re.IGNORECASE)),
    ("pos:right", re.compile(rf"{_WB}(?:right|droite|oikea\w*|rechts)(?!{_LD})", re.IGNORECASE)),
    ("pos:top", re.compile(
        rf"{_WB}(?:top|upper|haut|ylä(?:puol\w*|osa\w*|reun\w*)|oben)(?!{_LD})", re.IGNORECASE)),
    ("pos:bottom", re.compile(
        rf"{_WB}(?:bottom|lower|bas|ala(?:puol\w*|osa\w*|reun\w*)|unten)(?!{_LD})", re.IGNORECASE)),
    ("pos:corner", re.compile(rf"{_WB}(?:corner|coin|kulma\w*|ecke)(?!{_LD})", re.IGNORECASE)),
    ("cmp:gt", re.compile(
        rf"{_WB}(?:higher|more\s+than|greater\s+than|larger\s+than|bigger\s+than|at\s+least|above|over|"
        rf"minimum|supérieur\w*|au\s+moins|plus\s+de|suurempi\w*|vähintään|yli\s+\d|"
        rf"größer\w*|grösser\w*|höher\w*|mindestens|mehr\s+als|über\s+\d)(?!{_LD})|(?:>=|≥|>)\s*\d+",
        re.IGNORECASE)),
    ("cmp:lt", re.compile(
        rf"{_WB}(?:lower|less\s+than|fewer\s+than|smaller\s+than|at\s+most|below|under|maximum|"
        rf"inférieur\w*|au\s+plus|moins\s+de|pienempi\w*|enintään|alle\s+\d|"
        rf"kleiner\w*|niedriger\w*|höchstens|weniger\s+als|unter\s+\d)(?!{_LD})|(?:<=|≤|<)\s*\d+",
        re.IGNORECASE)),
    ("cmp:between", re.compile(
        rf"{_WB}(?:between|entre|välillä|zwischen)(?!{_LD})", re.IGNORECASE)),
    ("flt:restrict", re.compile(
        rf"{_WB}(?:only|just|except|without|exclude|excluding|filter\w*|matching|seulement|uniquement|"
        rf"sauf|vain|ilman|nur|außer|ausser)(?!{_LD})", re.IGNORECASE)),
]
_DIM_LINEAR_RE = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(cm|mm|m)(?![a-zA-Z0-9_²³])",
    re.IGNORECASE,
)
_DIM_AREA_RE = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(m[²2]|cm[²2]|mm[²2]|sqm\b|sq\.?\s*m\b)",
    re.IGNORECASE,
)


def canonical_qualifiers(text: str) -> frozenset[str]:
    """Extract normalized qualifier tags across EN/FR/FI/DE to prevent cache collisions."""
    s = unicodedata.normalize("NFC", text or "")
    tags: set[str] = set()
    for tag, rx in _QUALIFIER_PATTERNS:
        if rx.search(s):
            tags.add(tag)
    for m in _DIM_AREA_RE.finditer(s):
        val = float(m.group(1).replace(",", "."))
        u = m.group(2).lower()
        m2 = val / 10_000.0 if u.startswith("cm") else val / 1_000_000.0 if u.startswith("mm") else val
        tags.add(f"area:{round(m2, 4):g}m2")
    for m in _DIM_LINEAR_RE.finditer(s):
        val = float(m.group(1).replace(",", "."))
        u = m.group(2).lower()
        meters = val / 100.0 if u == "cm" else val / 1000.0 if u == "mm" else val
        tags.add(f"dim:{round(meters, 4):g}m")
    return frozenset(tags)


@dataclass
class HybridDecision:
    on_drawing: float | None
    exact_sufficient: float | None
    cache_key: str | None
    cache_question: str | None
    cache_conf: float
    intent: str | None = None
    intent_conf: float = 0.0
    subject: str | None = None
    subject_conf: float = 0.0
    scope: dict | None = None
    scope_conf: float = 0.0
    calls: int = 1
    cost_usd: float = 0.0
    input_tokens: int = 0
    latency_ms: int = 0
    model: str = ""
    # Every question asked of gemini-decision-1 → its calibrated output, for the reasoning trace:
    # {"on_drawing": {"p": 0.97}, "intent": {"choice": "count", "confidence": 0.8,
    #  "probabilities": {...}}, ...}
    asked: dict = field(default_factory=dict)

    @property
    def off_drawing(self) -> bool:
        return self.on_drawing is not None and self.on_drawing < HYBRID_OFF_DRAWING_GATE

    @property
    def can_use_exact(self) -> bool:
        return (
            self.exact_sufficient is not None
            and self.exact_sufficient >= EXACT_SUFFICIENT_GATE
            and self.intent in ("count", "area")
            and self.intent_conf >= MIN_CONFIDENCE
        )


async def decide_hybrid_route(
    client: GeminiClient,
    question: str,
    *,
    drawing_kind: str,
    source_ext: str | None = None,
    units: list[str],
    labels: list[str],
    image_path: Path | None,
    cached_entries: list[tuple[str, str]] | None = None,
    check_exact: bool = True,
    skip_on_drawing: bool = False,
    ip_hash: str | None = None,
    presenter: bool = False,
    meter: Meter | None = None,
) -> HybridDecision:
    """Single calibrated Decisions call gating semantic cache, off-drawing, and exact sufficiency."""
    use_image = (
        not skip_on_drawing
        and drawing_kind != "ifc"
        and source_ext != ".pdf"
        and image_path is not None
        and image_path.is_file()
    )
    image = await asyncio.to_thread(image_path.read_bytes) if use_image and image_path else None
    parts = [
        text_part(
            "A user asked this question about a building floor plan:\n"
            f"{question.strip()}\n"
        )
    ]
    if image is not None:
        parts.append(image_part(image, "low"))

    questions: dict[str, dict] = {}
    if not skip_on_drawing and drawing_kind != "ifc" and source_ext != ".pdf":
        questions["on_drawing"] = {
            "type": "noul",
            "instructions": (
                "Could this question plausibly be about architectural elements, rooms, areas, "
                "dimensions, orientation, stairs, or features on a floor plan drawing (as opposed "
                "to completely unrelated non-drawing topics like ownership, prices, or jokes)?"
            ),
        }

    key_by_opt: dict[str, tuple[str, str]] = {}
    entries = list(cached_entries or [])[:40]
    if entries:
        cache_crit: dict[str, str] = {}
        for idx, (ckey, cq) in enumerate(entries):
            opt = f"c{idx:02d}"
            cache_crit[opt] = f"Cached question: {cq}"
            key_by_opt[opt] = (ckey, cq)
        cache_crit["none"] = "None of the cached questions ask for the exact same thing."
        questions["cache_match"] = {
            "type": "choice",
            "instructions": (
                "Does one of the cached questions ask for the EXACT same set of items/measurement, "
                "preserving ALL subtype, compass/orientation (north/south/east/west), positional, "
                "dimensional, and room/apartment scope qualifiers? If ANY qualifier differs (for "
                "example 'north windows' vs 'all windows', or 'double doors' vs 'doors'), you MUST "
                "pick 'none'."
            ),
            "criteria": cache_crit,
        }

    to_scope: dict[str, dict] = {}
    if check_exact:
        crit, to_scope = scope_options(units, labels)
        questions["exact_sufficient"] = {
            "type": "noul",
            "instructions": (
                "Can this question be answered 100% faithfully by an unadorned whole-layer count "
                "of ALL windows, ALL doors, ALL washbasins, ALL WCs, ALL bathtubs, ALL showers, "
                "ALL rooms/apartments, or a summed net room area — WITHOUT needing any subtype "
                "(double/sliding/exterior), compass orientation (north/south/east/west), width/"
                "clearance measurement, stair connectivity, visual similarity, or spatial comparison?"
            ),
        }
        questions["intent"] = {
            "type": "choice",
            "instructions": "What does the question ask for?",
            "criteria": INTENTS,
        }
        questions["subject"] = {
            "type": "choice",
            "instructions": "If the question asks for a count, what is being counted?",
            "criteria": SUBJECTS,
        }
        questions["scope"] = {
            "type": "choice",
            "instructions": "Which part of the floor plan is the question about?",
            "criteria": crit,
        }

    questions = valid_choices(questions)
    if not questions:
        return HybridDecision(
            on_drawing=None, exact_sufficient=None, cache_key=None, cache_question=None,
            cache_conf=0.0, calls=0,
        )

    res = await client.decisions(
        parts, questions, "abstain_gate", ip_hash=ip_hash, presenter=presenter, meter=meter
    )
    matched_key: str | None = None
    matched_q: str | None = None
    cache_conf = 0.0
    if "cache_match" in questions:
        c_opt, cache_conf, _ = res.choice("cache_match")
        if c_opt and c_opt != "none" and cache_conf >= CACHE_MATCH_GATE and c_opt in key_by_opt:
            cand_key, cand_q = key_by_opt[c_opt]
            # Hard defense-in-depth: never allow a cache match across mismatched qualifiers!
            if canonical_qualifiers(question) == canonical_qualifiers(cand_q):
                matched_key = cand_key
                matched_q = cand_q

    intent, ic, _ = res.choice("intent") if "intent" in questions else (None, 0.0, {})
    subj, sc, _ = res.choice("subject") if "subject" in questions else (None, 0.0, {})
    skey, scc, _ = res.choice("scope") if "scope" in questions else (None, 0.0, {})
    asked: dict[str, dict] = {}
    for qk, qv in questions.items():
        if qv["type"] == "noul":
            asked[qk] = {"p": res.noul(qk)}
        else:
            ch, conf, probs = res.choice(qk)
            asked[qk] = {"choice": ch, "confidence": conf, "probabilities": probs}
            if qk == "cache_match" and matched_q is not None:
                # Cached questions were typed by other people: only option ids leave the server,
                # plus the text of the one entry actually used as this answer.
                asked[qk]["question"] = matched_q

    return HybridDecision(
        on_drawing=res.noul("on_drawing") if "on_drawing" in questions else None,
        exact_sufficient=res.noul("exact_sufficient") if "exact_sufficient" in questions else None,
        cache_key=matched_key,
        cache_question=matched_q,
        cache_conf=cache_conf,
        intent=intent,
        intent_conf=ic,
        subject=None if subj in (None, "other") else subj,
        subject_conf=sc,
        scope=to_scope.get(skey or "") if check_exact else None,
        scope_conf=scc,
        calls=1,
        cost_usd=res.cost_usd,
        input_tokens=res.input_tokens,
        latency_ms=res.latency_ms,
        model=res.model,
        asked=asked,
    )


AGENT_SUBJECT_CRITERIA: dict[str, str] = {
    "window": "windows (including north/south/roof/facade windows)",
    "door": "doors (including double, sliding, exterior, interior, or entrance doors)",
    "washbasin": "washbasins or bathroom/kitchen hand basins",
    "wc": "toilets or WC bowls",
    "bathtub": "bathtubs",
    "shower": "showers",
    "room": "rooms in general",
    "apartment": "apartments, flats, or dwelling units",
    "bedroom": "bedrooms",
    "kitchen": "kitchens",
    "bathroom": "bathrooms",
    "wc_room": "separate WC rooms",
    "sauna": "saunas",
    "sink": "kitchen sinks",
    "storey": "storeys, levels, or floors",
    "balcony": "balconies, loggias, or terraces",
    "other": "stairs, columns, spots, shafts, parking spaces, furniture, or anything else",
}


async def pick_agent_subject(
    client: GeminiClient,
    question: str,
    method_text: str,
    *,
    ip_hash: str | None = None,
    presenter: bool = False,
    meter: Meter | None = None,
) -> str | None:
    """Use calibrated choice (>= 0.80) to map an agent count to a contract Subject or None ('other')."""
    parts = [
        text_part(
            f"Question: {question.strip()}\n"
            f"Agent summary: {(method_text or '')[:300]}\n"
        )
    ]
    questions = {
        "subject": {
            "type": "choice",
            "instructions": (
                "What category of architectural element is being counted by this integer answer? "
                "Pick 'other' if it counts stairs, arbitrary spots/symbols, columns, furniture, "
                "or anything not strictly one of the named categories."
            ),
            "criteria": AGENT_SUBJECT_CRITERIA,
        }
    }
    res = await client.decisions(
        parts, questions, "choice", ip_hash=ip_hash, presenter=presenter, meter=meter
    )
    subj, conf, _ = res.choice("subject")
    if subj and subj != "other" and conf >= EXACT_SUFFICIENT_GATE:
        return subj
    return None
