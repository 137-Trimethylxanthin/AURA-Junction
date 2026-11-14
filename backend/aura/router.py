"""Deterministic question router: templates for every Swiss dataset family, in EN/FR/FI/DE/IT.

A port of frontend/src/lib/{route,detect}.ts, generalised past swiss-46639:
- apartments A–Z, any room id like 3.04 / U1.02 / 12.01, and any printed label (quoted
  'X', «X», „X“, "X" or named verbatim), all validated against the drawing;
- label synonyms resolve to whichever printed label the drawing actually has;
- fire-rating and printed-label questions route to `find_text` (never a blind abstain);
- height / material abstain `not_on_drawing`; lengths and volumes `not_supported_yet`;
- anything else is `unknown`, for the Decisions `choice` step (gemini/understand.py).

Python `re` has no \\p{L}; `[^\\W\\d_]` is a Unicode letter and `[^\\W_]` a letter or digit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

Intent = Literal["count", "area", "quantity", "find_text", "abstain", "unknown"]
InternalLang = Literal["en", "fr", "fi", "de", "it"]

_LD = r"[^\W_]"  # letter or digit
_L = r"[^\W\d_]"  # letter


def S(src: str) -> re.Pattern[str]:
    """Start-anchored (stems may continue)."""
    return re.compile(rf"(?<!{_LD})(?:{src})", re.IGNORECASE)


def W(src: str) -> re.Pattern[str]:
    """Whole word."""
    return re.compile(rf"(?<!{_LD})(?:{src})(?!{_LD})", re.IGNORECASE)


# ---- language ------------------------------------------------------------------------------

_LABEL_NAMES = re.compile(
    r"chambre à coucher|séjour\s*/\s*repas|réduit|terrasse|couloir|cuisine|cage d'escalier|"
    r"küche|korridor|treppenhaus|wohnen/essen|schlafzimmer|zimmer|bad|balkon|reduit|"
    r"cucina|corridoio|bagno|stanza|vano scala|soggiorno/pranzo|camera da letto|"
    r"'[^']{1,40}'|«[^»]{1,40}»|„[^“”]{1,40}[“”]",
    re.IGNORECASE,
)


def _wl(src: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!{_L})(?:{src})(?!{_L})", re.IGNORECASE)


def _ws(src: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!{_L})(?:{src})", re.IGNORECASE)


_MARKERS: dict[str, list[re.Pattern[str]]] = {
    "de": [
        _wl("wie|viele|wieviele|welche|gibt|es|der|die|das|den|dem|ist|sind|hat|auf|im|in der|"
            "diesem|dieser|plan|und|groß|gross|von|mit"),
        _ws("fenster|tür|wohnung|fläche|wohnfläche|nettofläche|nettowohn|nettogeschoss|zimmer|"
            "räume|raum|waschbeck|küche|grundriss|badezimmer|badewanne|dusche|brandschutz|anzahl|"
            "quadratmeter|stockwerk|geschoss|bezeichnung|eingezeichnet|dargestellt"),
    ],
    "fi": [
        _wl("montako|kuinka|monta|paljonko|onko|missä|mikä|mitkä|tässä|on|ja|siinä|tämän|näitä"),
        _ws("ikkun|ovea|ovia|ovet|asunno|asunto|huonee|huone|pinta-ala|pohja|keittiö|kylpy|makuu|"
            "pesual|vessa|suihku|kerro|neliö"),
    ],
    "fr": [
        _wl("combien|quelle|quel|quels|quelles|est|sont|le|la|les|de|des|du|en|dans|sur|ce|cette|"
            "ici|il|y a|a-t-il"),
        _ws("fenêtre|fenetre|porte|lavabo|appartement|pièce|surface|superficie|baignoire|douche|"
            "toilette|étage|plan|dessiné|désignation"),
    ],
    "it": [
        _wl("quanti|quante|quanto|qual|quale|è|sono|ci|il|lo|la|gli|le|di|del|della|dell|dei|"
            "delle|in|questo|questa|piano|pianta"),
        _ws("finestr|porte|lavabi|appartament|locali|locale|superficie netta|vasche|docce|"
            "disegnat|rappresentat|denominazione|unità abitative"),
    ],
    "en": [
        _wl("how|many|much|what|which|is|are|the|of|in|on|this|there|drawn|labelled|labeled|"
            "rooms?|total|area|floor|plan|apartment"),
        _ws("window|door|washbasin|bathroom|bedroom|kitchen|toilet|shower|bathtub"),
    ],
}
_CHARS = {"de": re.compile(r"[üß]"), "fi": re.compile(r"[äö]"), "fr": re.compile(r"[éèêçàùâîôœ]"),
          "it": re.compile(r"[ìò]")}
_TIE: list[str] = ["de", "fi", "fr", "it", "en"]


def lang_scores(text: str) -> dict[str, float]:
    s = _LABEL_NAMES.sub(" ", (text or "").lower())
    out = {k: 0.0 for k in _TIE}
    for lang in _TIE:
        for rx in _MARKERS[lang]:
            out[lang] += len(rx.findall(s))
        ch = _CHARS.get(lang)
        if ch:
            out[lang] += 0.5 * len(ch.findall(s))
    return out


def detect_lang(text: str) -> tuple[InternalLang, bool]:
    """(language, had_signal). 'en' when there is no signal."""
    sc = lang_scores(text)
    best, best_score = "en", 0.0
    for lang in _TIE:
        if sc[lang] > best_score:
            best, best_score = lang, sc[lang]
    return best, best_score > 0  # type: ignore[return-value]


def contract_lang(lang: str) -> str:
    """Contract Lang is en|fr|fi|de; Italian answers in English."""
    return lang if lang in ("en", "fr", "fi", "de") else "en"


# ---- vocab -----------------------------------------------------------------------------------

FIRE = S(r"fire|incendie|coupe-feu|feu\b|palo|brandschutz|feuer|brand|antincendio|fuoco|"
         r"\bEI\s?\d|\bREI\s?\d")
LABEL_TEXT = S(r"what (?:is|does) (?:the )?(?:label|text|tag)|printed (?:label|text)|which label|"
               r"what label|written on")
NOT_ON_DRAWING: list[tuple[str, re.Pattern[str]]] = [
    ("height", S(r"height|ceiling height|hauteur|korkeus|huonekorkeus|höhe|raumhöhe|altezza")),
    ("material", S(r"material|matériau|materiaali|rakenne|baustoff|materiale")),
]
LENGTH = S(r"length|lengths|longueur|pituus|länge|lunghezza|perimeter|périmètre|umfang|"
           r"volume|volumen|tilavuus|volum")

SUBJECTS: list[tuple[str, re.Pattern[str]]] = [
    ("window", S(r"windows?|fenêtres?|fenetres?|ikkun|fenster|finestr")),
    ("washbasin", S(r"wash ?basins?|hand ?basins?|basins?|sinks?|lavabos?|lavabi|lavandin|pesual|"
                    r"käsienpesu|waschbecken|waschtisch")),
    ("bathtub", S(r"bath ?tubs?|tubs?|baignoires?|kylpyam|badewanne|vasche? da bagno|vasca")),
    ("shower", S(r"showers?|douches?|suihku|dusche|docce|doccia")),
    ("wc", W(r"wcs?|wc-istuim[^\W\d_]*|wc-istuin|toilets?|toilettes?|vessa[^\W\d_]*|klosett|"
             r"water|gabinett[io]")),
    ("door", W(r"doors?|portes?|porte|porta|ovi|ovea|ovia|ovet|ovien|ovesta|ovessa|türen|tür|türe")),
]

AREA = re.compile(
    r"area|surface|superficie|pinta-?ala|neliö|fläche|flache|quadratmeter|m²|m2\b|square met|"
    r"how big|how large|wie groß|wie gross|kuinka suuri|nettofläche|nettowohn|nettogeschoss|"
    r"quanto è grand|metri quadr",
    re.IGNORECASE,
)
COUNT = re.compile(
    r"how many|number of|count|combien|nombre d|montako|kuinka monta|wie viele|wieviele|anzahl|"
    r"lukumäärä|quanti|quante|numero di",
    re.IGNORECASE,
)
APARTMENT = S(r"apartments?|flats?|dwellings?|appartements?|logements?|asunto|asunno|asuntoj|"
              r"huoneisto|wohnung|appartament|unità abitativ|residential units?")
ROOM_WORD = S(r"rooms?|pièces?|huone|huonei|tilo|zimmer|räume|raum|locali|locale|stanz")
LABELLED = S(r"labell?ed|labeled|named|called|désignation|désignées?|bezeichnung|denominazion|"
             r"denominat|nimetty|nimellä|portant")

# Concept → (question synonyms, printed labels that mean it, most specific first).
LABEL_CONCEPTS: list[tuple[str, re.Pattern[str], list[str]]] = [
    ("bedroom", S(r"chambres? à coucher|bed ?rooms?|makuuhuone|schlafzimmer|camera da letto|"
                  r"camere da letto"),
     ["Chambre à coucher", "Schlafzimmer", "Camera da letto", "Bedroom", "Chambre", "Zimmer",
      "Pièce", "Stanza"]),
    ("living", S(r"séjour|sejour|living|dining|lounge|olohuone|oleskelu|ruokailu|wohnzimmer|"
                 r"wohnen|esszimmer|wohnraum|soggiorno"),
     ["Séjour/repas", "Séjour", "Wohnen/Essen", "Wohnen", "Soggiorno/pranzo", "Soggiorno",
      "Living", "Living room"]),
    ("kitchen", S(r"cuisines?|kitchens?|keitti|küche|kuche|cucin"),
     ["Cuisine", "Küche", "Küche/Essen", "Cucina", "Kitchen"]),
    ("bathroom", W(r"bains?|salles? de bains?|bathrooms?|baths?|kylpyhuone[^\W\d_]*|bad|"
                   r"badezimmer|bäder|bagno|bagni"),
     ["Bain", "Bad", "Bagno", "Bathroom", "Bath"]),
    ("storage", S(r"réduits?|reduit|storage|store ?rooms?|closets?|varasto|abstellraum|abstellr|"
                  r"ripostigli|keller|cantina"),
     ["Réduit", "Reduit", "Ripostiglio", "Kellerabteil", "Vano cantina", "Storage"]),
    ("corridor", S(r"couloirs?|corridors?|hallways?|halls?|käytäv|eteinen|eteis|flur|korridor|"
                   r"gang\b|corridoi"),
     ["Couloir", "Korridor", "Corridoio", "Corridor", "Hall"]),
    ("outdoor", S(r"terrasses?|terraces?|balcons?|balcony|balconies|terassi|parveke|balkon|"
                  r"balcone|loggia"),
     ["Terrasse", "Balcon", "Balkon", "Balcone", "Loggia", "Balcony", "Terrace"]),
    ("stairs", S(r"stair|escalier|treppe|scala|porrashuone|porras"),
     ["Cage d'escalier", "Treppenhaus", "Vano scala", "Staircase", "Stair"]),
]

_QUOTED = re.compile(r"'([^']{1,60})'|‘([^’]{1,60})’|«\s?([^»]{1,60}?)\s?»|„([^“”]{1,60})[“”]|"
                     r"\"([^\"]{1,60})\"|“([^”]{1,60})”")
_UNIT_RE = re.compile(
    rf"(?<!{_L})(?:apartment|appartement|appartamento|logement|unit|wohnung|asunto|"
    rf"asunno{_L}*|app\.?|flat)(?:\s+|\s*[-:]\s*)([A-Za-z])(?!{_LD})",
    re.IGNORECASE,
)
_UNIT_APOS_RE = re.compile(r"(?:l['’]|dell['’])appartement?o?\s*([A-Z])(?![^\W_])", re.IGNORECASE)
_UNIT_FI_RE = re.compile(rf"(?<!{_L})([A-Z])-(?:asunno|asunto){_L}*")
_ROOM_ID_RE = re.compile(r"(?<![\w.])([A-Z]{0,2}\d{1,3}\.\d{1,3})(?![\d])")
_ROOM_AFTER_WORD = re.compile(
    rf"(?<!{_L})(?:room|pièce|piece|raum|locale|huone|stanza|zimmer)\s+(?:n[o°.]?\s*)?"
    rf"([A-Z]{{0,2}}\d{{1,3}}(?:\.\d{{1,3}})?)(?![\d.])",
    re.IGNORECASE,
)


QUESTION_WORDS = {"floor", "plan", "level", "room", "rooms", "area", "total", "house", "building",
                  "storey", "story", "étage", "etage", "geschoss", "stock", "piano", "kerros",
                  "apartment", "unit", "space"}


# ---- drawing context -------------------------------------------------------------------------


@dataclass
class DrawingCtx:
    """What the router may validate against: printed labels, room ids, apartment letters."""

    labels: list[str] = field(default_factory=list)
    room_ids: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    kind: str = "cad_dxf"


WHOLE_MODEL = S(r"whole building|entire building|in the (?:ifc )?model|whole model|all storeys|"
                r"all floors|ganzen gebäude|gesamten gebäude|tout le bâtiment|koko rakennus")
# IFC quantities (answered as given in the model, never recomputed).
QUANTITY: list[tuple[str, re.Pattern[str]]] = [
    ("wall_length", S(r"(?:total )?length of (?:the |all )?walls|wall length|wandlänge")),
    ("wall_side_area", S(r"(?:net )?(?:side )?area of (?:the |all )?walls|wall (?:side )?area|"
                         r"wandfläche")),
    ("wall_volume", S(r"volume of (?:the |all )?walls|wall volume|wandvolumen")),
    ("slab_volume", S(r"volume of (?:the |all )?slabs?|slab volume|deckenvolumen")),
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold().strip()
    return re.sub(r"\s+", " ", s)


def unit_of(text: str, ctx: DrawingCtx | None) -> tuple[str | None, str | None]:
    """(unit letter, raw mention). The letter is returned even if the drawing lacks it."""
    for rx in (_UNIT_APOS_RE, _UNIT_RE):
        for m in rx.finditer(text):
            letter = m.group(1)
            rest = text[m.end():]
            # A lowercase "a" counts only at the end of a clause (else it is an article).
            if letter == letter.upper() or re.match(r"^\s*([?.!,;]|$)", rest):
                return letter.upper(), m.group(0)
    f = _UNIT_FI_RE.search(text)
    if f:
        return f.group(1), f.group(0)
    return None, None


def room_of(text: str, ctx: DrawingCtx | None) -> str | None:
    known = set(ctx.room_ids) if ctx else set()
    m = _ROOM_AFTER_WORD.search(text)
    if m:
        rid = m.group(1)
        if not known or rid in known:
            return rid
        up = rid.upper()
        if up in known:
            return up
        return rid
    for m2 in _ROOM_ID_RE.finditer(text):
        rid = m2.group(1)
        if not known or rid in known:
            return rid
    m3 = _ROOM_ID_RE.search(text)
    return m3.group(1) if m3 else None


def label_of(text: str, ctx: DrawingCtx | None) -> tuple[str | None, str | None]:
    """(label as printed on the drawing, or the raw quoted label if the drawing lacks it; raw)."""
    labels = list(dict.fromkeys(ctx.labels)) if ctx else []
    by_norm = {_norm(lab): lab for lab in labels}
    quoted = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(text)]
    for q in quoted:
        hit = by_norm.get(_norm(q))
        if hit:
            return hit, q
    # A label named verbatim (longest first), as a whole word. Labels that are also everyday
    # question words ("Floor", "Plan") count only with a "labelled" cue.
    t = _norm(text)
    cue = bool(LABELLED.search(text))
    for lab in sorted(labels, key=len, reverse=True):
        n = _norm(lab)
        if n in QUESTION_WORDS and not cue:
            continue
        if len(n) >= 3 and re.search(rf"(?<!{_LD}){re.escape(n)}(?!{_LD})", t):
            return lab, lab
    # Synonyms → a printed label the drawing has.
    probe = " ".join(quoted) + " " + text if quoted else text
    for _concept, rx, printed in LABEL_CONCEPTS:
        if rx.search(probe):
            for p in printed:
                if _norm(p) in by_norm:
                    return by_norm[_norm(p)], p
            if quoted:
                return quoted[0], quoted[0]
            return (printed[0] if not labels else None), None
    if quoted:
        return quoted[0], quoted[0]
    return None, None


# ---- route -----------------------------------------------------------------------------------


@dataclass
class Routed:
    intent: Intent
    lang: InternalLang
    lang_signal: bool
    subject: str | None = None  # count subject; for find_text: 'fire_rating' | 'label'
    scope: dict | None = None  # text-derived scope (never region)
    reason: str | None = None  # abstain reason
    topic: str | None = None  # not_on_drawing topic: fire | height | material | length
    pattern: str | None = None  # find_text regex
    matched: list[str] = field(default_factory=list)  # which rules fired (for the trace)


FIRE_PATTERN = r"\bR?EI\s?\d{2,3}\b|\b\d{2,3}\s?MIN\b|\bT\s?\d0\b|\bF\s?\d{2,3}\b|\bE\s?\d{2}\b"


# Exclusion clauses name outdoor spaces that are NOT the scope ("excluding balconies, …").
_EXCLUSION = re.compile(
    r"\((?:excluding|without|except|hors|sans|ohne|esclus[ie]|lukuun ottamatta)[^)]*\)|"
    r"(?:summing all its rooms )?(?:excluding|except|hors|ohne|esclus[ie]|without)\b[^?]*",
    re.IGNORECASE,
)


def _scope(text: str, ctx: DrawingCtx | None) -> dict | None:
    text = _EXCLUSION.sub(" ", text)
    if ctx is not None and ctx.kind == "ifc" and WHOLE_MODEL.search(text):
        return {"kind": "model"}
    room = room_of(text, ctx)
    if room:
        return {"kind": "room", "room_id": room}
    unit, _ = unit_of(text, ctx)
    if unit:
        return {"kind": "unit", "unit": unit}
    label, _ = label_of(text, ctx)
    if label:
        return {"kind": "label", "label": label}
    return None


SELECTOR = S(r"which|most|fewest|least|largest|smallest|biggest|more than|less than|fewer than|"
             r"at least|at most|quel(?:les?|s)?\b(?!\s+(?:est|sont))|lequel|laquelle|le plus|la plus|les plus|welche[rsn]?|"
             r"meisten|wenigsten|grösste[rn]?|größte[rn]?|kleinste[rn]?|mehr als|mindestens|mikä(?!\s+on\b)|"
             r"mitkä(?!\s+ovat\b)|missä|eniten|vähiten|suurin|pienin|enemmän kuin|vähintään|quale|quali|"
             r"il più|la più|di più|almeno")
ROOMS_WITH = S(r"(?:rooms?|bedrooms?|pièces?|chambres?|zimmer|räume|huonee?t?)\s+(?:that\s+|which\s+)?"
               r"(?:with|have|has|having|contain\w*|avec|ayant|mit|joissa|jossa)")


def _counts_apartments(s: str) -> bool:
    """Apartments are what is COUNTED ("how many flats…"), not just the scope ("…in this
    flat?"): the apartment word must follow the count cue within two words."""
    c = COUNT.search(s)
    a = APARTMENT.search(s, c.end()) if c else None
    return bool(a) and len(s[c.end():a.start()].split()) <= 2


def route(text: str, ctx: DrawingCtx | None = None) -> Routed:
    s = unicodedata.normalize("NFC", text or "")
    lang, signal = detect_lang(s)

    if FIRE.search(s):
        return Routed("find_text", lang, signal, subject="fire_rating", topic="fire",
                      pattern=FIRE_PATTERN, matched=["fire"])
    if LABEL_TEXT.search(s):
        return Routed("find_text", lang, signal, subject="label", pattern=None,
                      matched=["label_text"])
    if ctx is not None and ctx.kind == "ifc":
        for fam, rx in QUANTITY:
            if rx.search(s):
                return Routed("quantity", lang, signal, subject=fam, scope={"kind": "floor"},
                              matched=["quantity", fam])
    for topic, rx in NOT_ON_DRAWING:
        if rx.search(s):
            return Routed("abstain", lang, signal, reason="not_on_drawing", topic=topic,
                          matched=[topic])
    if LENGTH.search(s):
        return Routed("abstain", lang, signal, reason="not_supported_yet", topic="length",
                      matched=["length"])

    # A template answers only a question it accounts for whole: a selector or superlative
    # ("which bedroom has the most windows?") or a "rooms with/that have X" clause is NOT
    # "count X"; it goes to the model step (or abstains when models are off).
    if SELECTOR.search(s) or (ROOMS_WITH.search(s) and not LABELLED.search(s)):
        return Routed("unknown", lang, signal, scope=_scope(s, ctx), matched=["needs_model"])

    is_area = bool(AREA.search(s))
    is_count = bool(COUNT.search(s))
    subject = None
    scope_text = s
    for subj, rx in SUBJECTS:
        m = rx.search(s)
        if m:
            subject = subj
            # "vasche da bagno" is a bathtub, not the label "Bagno": scope from the rest.
            scope_text = s[: m.start()] + " " + s[m.end():]
            break
    scope = _scope(scope_text, ctx)

    labelled = bool(LABELLED.search(s))
    if labelled and scope and scope["kind"] == "label":
        if is_area and not is_count:
            return Routed("area", lang, signal, scope=scope, matched=["area", "label"])
        return Routed("count", lang, signal, subject="room", scope=scope,
                      matched=["count", "label"])

    if subject and not (is_area and not is_count):
        return Routed("count", lang, signal, subject=subject, scope=scope, matched=["count", subject])
    if is_area:
        return Routed("area", lang, signal, scope=scope, matched=["area"])

    if is_count and _counts_apartments(s) and not (scope and scope["kind"] == "unit"):
        return Routed("count", lang, signal, subject="apartment", scope=None,
                      matched=["count", "apartment"])
    if scope and scope["kind"] == "label" and (is_count or ROOM_WORD.search(s)):
        return Routed("count", lang, signal, subject="room", scope=scope,
                      matched=["count", "label"])
    if is_count and ROOM_WORD.search(s):
        return Routed("count", lang, signal, subject="room", scope=scope, matched=["count", "room"])

    return Routed("unknown", lang, signal, scope=scope, matched=[])


WHERE_TO_FIND = {
    "fire": {
        "en": "A door schedule or the IFC model would have it.",
        "fr": "Un tableau des portes ou la maquette IFC l’indiquerait.",
        "fi": "Tieto löytyy ovikaaviosta tai IFC-mallista.",
        "de": "Das steht in der Türliste oder im IFC-Modell.",
    },
    "height": {
        "en": "A section drawing or the IFC model would have it.",
        "fr": "Une coupe ou la maquette IFC l’indiquerait.",
        "fi": "Tieto löytyy leikkauspiirustuksesta tai IFC-mallista.",
        "de": "Das steht in einem Schnitt oder im IFC-Modell.",
    },
    "material": {
        "en": "The specification or the IFC model would have it.",
        "fr": "Le descriptif ou la maquette IFC l’indiquerait.",
        "fi": "Tieto löytyy rakennustapaselostuksesta tai IFC-mallista.",
        "de": "Das steht in der Baubeschreibung oder im IFC-Modell.",
    },
}

TOPIC_EN = {
    "fire": ("What is the fire rating", "No printed text on this drawing carries a fire rating."),
    "height": ("What is the room height", "A floor plan has no heights; no label on it states one."),
    "material": ("What materials are used", "No layer or label on this drawing names a material."),
    "length": ("What is the length or volume",
               "Lengths and volumes are not extracted from this drawing yet."),
}
