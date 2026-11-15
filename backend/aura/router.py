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

# Bumped whenever routing changes what a question resolves to: cached answers written under an
# older router are ignored (pipeline.answer cache key).
ROUTER_VERSION = 11  # 8: tried-and-empty = not_answerable; 9: metadata to tools; 10: prices to tools, money not rooms, topics via tools; 11: rooms by door/window count
EXACT_INTENTS = frozenset({"count", "area", "quantity", "find_text", "room_rank", "adjacent", "rooms_by_count"})

Intent = Literal["count", "area", "quantity", "find_text", "abstain", "unknown",
                 "room_rank", "adjacent", "rooms_by_count"]
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
        _ws("fenster|tür|wohnung|fläche|gesamt|wohnfläche|nettofläche|nettowohn|nettogeschoss|zimmer|"
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
            "delle|in|questo|questa|piano|pianta|tutti|tutte"),
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


def stem_scores(text: str) -> dict[str, int]:
    """Hits of each language's distinctive domain stems ('fenster', 'ovet', 'surface'), not its function words."""
    s = _LABEL_NAMES.sub(" ", (text or "").lower())
    return {k: len(_MARKERS[k][1].findall(s)) for k in _TIE}


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
           r"volume|volumen|tilavuus|volum|width|widths|wide|narrow|largeur|étroit|leveys|"
           r"kapea|breite|breit|schmal|larghezza")

LOCATE = S(
    r"show|highlight|mark|find|locate|point out|where (?:is|are)|list|display|give me|"
    r"what are (?:the|all)|which are|enumerate|"
    r"montre\w*|affiche\w*|surlign\w*|où (?:est|sont)|liste\w*|indique\w*|donne-moi|"
    r"näytä|korosta|missä (?:on|ovat)|etsi|listaa|luettele|"
    r"zeig\w*|markier\w*|wo (?:ist|sind)|liste\w*|auflist\w*|nenn\w*|"
    r"mostra\w*|dove (?:è|sono)|elenca\w*|evidenzia\w*"
)
# "all doors", "toutes les fenêtres", "alle Türen", "kaikki ovet", "tutte le porte": a bare
# quantifier + subject asks to show every item, exactly like "show all doors".
ALL_OF = S(r"all|every|each|toutes?|tous|alle[ns]?|sämtliche|kaikki|tutt[ie]|ogni")

STAIR_ESCALATE = re.compile(
    rf"(?<!{_LD})(?:stairs?|staircases?|stairwells?|escaliers?|portaat|porras|porraskäytäv\w*|"
    rf"treppen?|treppenhäuser|treppenhaus|vano scala)(?!{_LD})|"
    r"\bleads?\s+to\b|\bwhere\s+do(?:es)?\b.*\blead\b|\boù\s+mène|\bmihin\b.*\bjohta|\bwohin\s+führ",
    re.IGNORECASE,
)

STAIR_LEADS = re.compile(
    r"\blead|\bconnect|\bgo(?:es)?\s+(?:up|down|to)\b|\bwhere\s+do(?:es)?\b|\bmène|\bführ|\bjohta|\bporta(?:no)?\s+a\b",
    re.IGNORECASE)

DEICTIC_SIMILARITY = re.compile(
    r"\b(?:like this|like that|like these|similar to|same as this|spots?\s+like|symbols?\s+like|"
    r"these spots|what is this|what['’]s this|where else|other ones|same symbol|same icon)\b|"
    rf"(?<!{_LD})(?:comme ça|comme ceci|similaire\w*|semblable\w*|"
    r"tällaisi[aä]|tämän kaltaisi\w*|samanlaisi[aä]|mikä tämä|"
    rf"wie diese[rsnm]?|solche\w*|ähnlich\w*|was ist das)(?!{_LD})",
    re.IGNORECASE,
)

_INCLUSIVE_PAREN = re.compile(
    r"\([^)]*\b(?:together|zusammen|confondues?|insieme|yhteensä|both|alle)\b[^)]*\)",
    re.IGNORECASE,
)

COLOR_CUE = re.compile(
    rf"(?<!{_LD})(?:colors?|colours?|color[- ]?cod\w*|colour[- ]?cod\w*|"
    r"different\s+colou?rs?|colou?r\s+by|en\s+couleurs?|couleurs?\s+différent\w*|"
    r"eri\s+väreillä|väritä|värikood\w*|farbig\w*|verschiedene[rn]?\s+farben|färb\w*)(?!{_LD})",
    re.IGNORECASE,
)

FOLLOWUP_CUE = re.compile(
    r"\b(?:only|show only|just the|filter|exclude|without|instead|now show|"
    r"by apartment|by type|by room|group them|group by|these|those|them|"
    r"seulement|uniquement|sans|ceux|celles|vain|ilman|näistä|näitä|nur|ohne|diese[rn]?)\b",
    re.IGNORECASE,
)

_FOLLOWUP_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:and|what about|how about|same(?: for)?|et|und|ja|entä|e|ed|plus|also|then)\b",
    re.IGNORECASE,
)

_QUOTED_SPAN = re.compile(r"['\"«„][^'\"»“]{1,60}['\"»“]")

QUALIFIER_GUARD = re.compile(
    r"(?:(?<![\w-])|(?<=_))(?:"
    # Door/window subtypes & accessibility
    r"double|single|sliding|pocket|folding|revolving|hinged|french\s+doors?|"
    r"exterior|external|outer|interior|internal|inner|entrance|entry|front\s+doors?|back\s+doors?|"
    r"accessible|wheelchair|handicap\w*|barrier[- ]free|wider|narrower|swing\w*|inward|outward|"
    r"porte\s+double|portes?\s+doubles?|portes?\s+couliss\w*|portes?\s+ext\w*|portes?\s+int\w*|"
    r"pariov\w*|liukuov\w*|ulko-?ov\w*|sisäov\w*|esteet\w*|"
    r"doppeltür\w*|schiebetür\w*|außentür\w*|aussentür\w*|innentür\w*|haustür\w*|barrierefrei\w*|"
    # Filter, restriction & threshold/comparison cues (EN/FR/FI/DE)
    r"only|just|except|without|exclude|excluding|filter\w*|matching|whose|having|with\s+an?|"
    r"where(?!\s+(?:is|are)\b)|"
    r"higher|more\s+than|less\s+than|fewer\s+than|greater\s+than|smaller\s+than|larger\s+than|bigger\s+than|"
    r"at\s+least|at\s+most|above|below|over|under|minimum|maximum|between|"
    r"seulement|uniquement|sauf|supérieur\w*|inférieur\w*|au\s+moins|au\s+plus|plus\s+de|moins\s+de|"
    r"vain|ilman|suurempi\w*|pienempi\w*|vähintään|enintään|yli\s+\d|alle\s+\d|"
    r"nur|außer|ausser|größer\w*|grösser\w*|kleiner\w*|höher\w*|niedriger\w*|mindestens|höchstens|"
    r"mehr\s+als|weniger\s+als|über\s+\d|unter\s+\d|zwischen\s+\d|"
    # Compass, facade & positional qualifiers (EN/FR/FI/DE)
    r"north(?:ern)?|south(?:ern)?|east(?:ern)?|west(?:ern)?|"
    r"north[- ]facing|south[- ]facing|east[- ]facing|west[- ]facing|"
    r"nord|sud|ouest|"
    r"pohjoinen|pohjois\w*|etelä\w*|itä(?:ikkun\w*|ov\w*|puol\w*|siv\w*|julki\w*|inen|än)?|länsi\w*|läntinen|"
    r"norden|süden|osten|westen|nord(?:seite|fassade|fenster|tür\w*)|süd(?:seite|fassade|fenster|tür\w*)|"
    r"ost(?:seite|fassade|fenster|tür\w*)|west(?:seite|fassade|fenster|tür\w*)|"
    r"facades?|façades?|julki\w+|fassaden?|"
    r"left|right|top|bottom|upper|lower|corner|gauche|droite|haut|bas|coin|"
    r"vasen\w*|oikea\w*|ylä(?:puol\w*|osa\w*|reun\w*)|ala(?:puol\w*|osa\w*|reun\w*)|kulma\w*|"
    r"links|rechts|oben|unten|ecke"
    rf")(?!{_LD})|"
    # French 'est' only in directional contexts (never matching verb 'est' = 'is')
    r"(?:\borienté\w*\s+(?:à\s+l['’])?est\b|\bcôté\s+est\b|\bfa[çc]ade\s+est\b|\bà\s+l['’]est\b|\bvers\s+l['’]est\b)|"
    # Explicit dimensional & area thresholds (note: Python 3 treats '²'/'³' as \w, so never rely on \b after ²!)
    r"(?<!\d)\d+(?:[.,]\d+)?\s*(?:m[²³23]|cm[²³23]?|mm[²³23]?|sqm\b|sq\.?\s*m\b|ft[²³23]?|m(?![a-zA-Z0-9_²³]))|"
    r"(?:[<>]=?|[≥≤])\s*\d+",
    re.IGNORECASE,
)

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
    r"square\s+f(?:ee|oo)t|sq\.?\s*ft\b|sqft\b|ft[²2]|square\s+inch\w*|sq\.?\s*in\b|in[²2]|"
    r"quadratfu(?:ß|ss)|pieds?\s+carr[ée]s?|neliöjalk\w*|square\s+footage|"
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
# A room id like 3.05; never a measurement ("0.92 m", "15.5 m²", "2.5 metres", "12.5 sq ft").
_ROOM_ID_RE = re.compile(r"(?<![\w.$€£])([A-Z]{0,2}\d{1,3}\.\d{1,3})(?![\d])"
                         r"(?!\s*(?:m\b|m²|m2\b|m\^2|m³|m3\b|cm[²³23]?|mm[²³23]?|km\b|sqm|qm|%|met(?:er|re)s?\b|neliö|quadrat"
                         r"|in\b|in[²³23]|inch\w*|ft\b|ft[²³23]|f(?:ee|oo)t\b|sqft\b|sq\.?\s*(?:ft|in|yd|m|cm|mm)\b"
                         r"|cu\.?\s*(?:ft|in|yd|m)\b|yd\b|yards?\b|acres?\b|ha\b|hectares?\b|lit(?:er|re)s?\b|gal\w*"
                         r"|[\"″'′]|\$|€|£|chf|eur\w*|usd|dollars?|fr\b|francs?|franken))", re.IGNORECASE)
# ... nor a threshold ("larger than 15.5", "over 0.9", ">= 12.5").
_COMPARE_BEFORE = re.compile(r"(?:than|over|under|above|below|least|most|[<>]=?|≥|≤|als|que|kuin|di)\s*$",
                             re.IGNORECASE)
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
    ids = [m2 for m2 in _ROOM_ID_RE.finditer(text)
           if m2.group(1) in known or not _COMPARE_BEFORE.search(text[:m2.start()])]
    for m2 in ids:
        rid = m2.group(1)
        if not known or rid in known:
            return rid
    # A bare number the drawing has no room for ("for 2.5", "costs 3.50") is not a room; an unknown
    # id counts only after a room word ("room 9.99" above), so it can abstain as unclear.
    return None if known else (ids[0].group(1) if ids else None)


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


SELECTOR = S(r"which|most|fewest|least|largest|smallest|biggest|widest|narrowest|longest|shortest|"
             r"more than|less than|fewer than|"
             r"at least|at most|quel(?:les?|s)?\b(?!\s+(?:est|sont))|lequel|laquelle|"
             r"le plus|la plus|les plus|plus large|plus étroit|welche[rsn]?|"
             r"meisten|wenigsten|grösste[rn]?|größte[rn]?|kleinste[rn]?|breiteste[rn]?|schmalste[rn]?|"
             r"mehr als|mindestens|mikä(?!\s+on\b)|"
             r"mitkä(?!\s+ovat\b)|missä(?!\s+(?:on|ovat)\b)|eniten|vähiten|suurin|pienin|levein|kapein|"
             r"enemmän kuin|vähintään|quale|quali|"
             r"il più|la più|di più|almeno")
ROOMS_WITH = S(r"(?:rooms?|bedrooms?|pièces?|chambres?|zimmer|räume|huonee?t?)\s+(?:that\s+|which\s+)?"
               r"(?:with|have|has|having|contain\w*|avec|ayant|mit|joissa|jossa)")


# Superlative over room areas ("biggest room", "smallest bedroom", "welches Zimmer ist am grössten"):
# answered from the indexed room areas (grab all rooms, filter to the max/min), never by a model.
RANK_MAX = S(r"largest|biggest|most spacious|grösste[rnms]?|größte[rnms]?|am grössten|am größten|"
             r"plus grand(?:e|es|s)?|più grand[ei]|suurin|suurimm\w*")
RANK_MIN = S(r"smallest|tiniest|kleinste[rnms]?|am kleinsten|plus petit(?:e|es|s)?|più piccol[oaie]|"
             r"pienin|pienimm\w*")

# A reference back to the previous answer ("that room", "in it"). Resolved from the previous turn's
# evidence in pipeline.py; never answered as an unqualified whole-floor template.
ANAPHORA = re.compile(
    r"\b(?:that|this|the same|said|these|those)\s+(?:rooms?|ones?|spaces?|apartments?|units?|flats?)\b|"
    r"\b(?:in|into|to|of|from|inside|does|has|for)\s+(?:it|them)\b|\bits\b|"
    r"\b(?:ce(?:tte)?|cet)\s+(?:pièce|chambre|appartement)\b|\b(?:diese[mnrs]?|dem)\s+(?:raum|zimmer)\b",
    re.IGNORECASE,
)
# Doors/windows that open into / connect to a room (adjacency, both sides of a shared wall).
ADJACENT = re.compile(
    r"\b(?:lead(?:s|ing)?|open(?:s|ing)?|go(?:es|ing)?|connect(?:s|ed|ing)?|adjacent|next|access(?:es|ing)?)"
    r"\s+(?:in)?to\b|\binto\b|\bconnected\s+with\b|\b(?:of|for|in|on)\s+(?:that|this|the same)\s+room\b",
    re.IGNORECASE,
)


WHICH_ARE_THERE = re.compile(
    r"^\s*(?:which|what)\s+(?=\w+\s+(?:are\s+there|exist|are\s+(?:on|in)\s+(?:this|the)\s+(?:floor|plan|drawing))\b)",
    re.IGNORECASE)


PRICE = re.compile(
    r"\b(?:price|prices|cost|costs|costing|pay|budget|quote|per (?:m²|m2|square)|"
    r"prix|coût\w*|coute\w*|payer|preis\w*|kosten?|kostet|bezahl\w*|hinta\w*|maksa\w*|"
    r"prezz\w*|cost[oai]|pagar\w*)\b|[$€£]|\b(?:chf|eur|usd)\b",
    re.IGNORECASE)
# Metadata of drawn objects (EN/FR/DE/FI/IT). Not "named"/"called" (that is a room label).
METADATA = re.compile(
    r"\b(?:property|properties|property ?sets?|psets?|attributes?|ifc ?class(?:es)?|classes|"
    r"layers?|type names?|types?|names?|distinct|guids?|tags?|"
    r"propriétés?|attributs?|calques?|noms?|types?|"
    r"eigenschaft\w*|merkmal\w*|attribut\w*|ebenen?|layern?|namen?|klassen?|typen?|"
    r"ominaisuu\w*|attribuut\w*|tasot?|tasoj\w*|nim(?:i|et|iä)|luok\w*|tyyp\w*|"
    r"propriet\w*|attribut[oi]|livell[oi]|nom[ei]|class[ei]|tip[oi])\b",
    re.IGNORECASE)
LAYER_WORD = re.compile(r"\b(?:layers?|calques?|ebenen?|tasot?|livell[oi]|ifc)\b", re.IGNORECASE)


def room_concept(text: str) -> tuple[str | None, list[str]]:
    """('bedroom', its printed label names) when the question names a room type."""
    for concept, rx, printed in LABEL_CONCEPTS:
        if concept != "outdoor" and rx.search(text):
            return concept, printed
    return None, []


def _counts_apartments(s: str) -> bool:
    """Apartments are what is COUNTED/SHOWN ("how many flats…", "show apartments"), not just the
    scope ("…in this flat?"): the apartment word must follow the count/locate cue within 3 words."""
    for cue_rx in (COUNT, LOCATE):
        c = cue_rx.search(s)
        a = APARTMENT.search(s, c.end()) if c else None
        if a and len(s[c.end():a.start()].split()) <= 3:
            return True
    return False


def is_followup_message(text: str, routed: Routed | None = None) -> bool:
    """True when `text` looks like an elliptical refinement/color/filter follow-up to a prior turn."""
    s = unicodedata.normalize("NFC", text or "")
    if COLOR_CUE.search(s) or FOLLOWUP_CUE.search(s):
        return True
    if routed is not None and "anaphora" in routed.matched:
        return True
    if routed is not None and "qualifier_guard" in routed.matched:
        has_explicit_noun = any(rx.search(s) for _, rx in SUBJECTS) or bool(
            ROOM_WORD.search(s) or APARTMENT.search(s) or AREA.search(s)
        )
        if not has_explicit_noun:
            return True
    return False


_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "ein": 1, "eine": 1, "einer": 1, "einem": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six ": 6,
    "yksi": 1, "yhden": 1, "yhtä": 1, "kaksi": 2, "kahden": 2, "kahta": 2, "kolme": 3, "kolmen": 3, "neljä": 4,
    "viisi": 5,
}
_NUM = r"(\d+|" + "|".join(sorted((re.escape(k.strip()) for k in _NUM_WORDS), key=len, reverse=True)) + r")"
_CMP = [
    ("gt", r"more than|over|greater than|mehr als|über|plus d['’]|plus de|enemmän kuin|yli"),
    ("ge", r"at least|minimum of|mindestens|wenigstens|au moins|vähintään"),
    ("lt", r"less than|fewer than|under|weniger als|moins d['’]|moins de|vähemmän kuin|alle"),
    ("le", r"at most|no more than|maximum of|höchstens|maximal|au plus|enintään"),
    ("eq", r"exactly|genau|exactement|tasan|precisely"),
]
_CMP_RX = [(op, re.compile(rf"(?:{w})\s*{_NUM}\b", re.IGNORECASE)) for op, w in _CMP]
_OR_MORE = re.compile(rf"\b{_NUM}\s*(?:\+|or more|oder mehr|ou plus|tai enemmän)", re.IGNORECASE)
_ROOMS_WITH_N = re.compile(
    rf"(?:rooms?|pièces?|chambres?|zimmer|räume|huonee?t?)\s+(?:that\s+|which\s+)?"
    rf"(?:with|have|has|having|avec|ayant|mit|joissa|jossa)\s+(?:on\s+)?{_NUM}\s", re.IGNORECASE)


def _num(tok: str) -> int | None:
    tok = tok.strip().lower()
    return int(tok) if tok.isdigit() else _NUM_WORDS.get(tok)


def rooms_by_count_of(s: str) -> tuple[str, int] | None:
    """(op, n) when the question filters rooms by how many of something they have: 'rooms with more than
    1 door' -> ('gt', 1); '2 or more windows' -> ('ge', 2); 'rooms with two doors' -> ('eq', 2)."""
    if not ROOMS_WITH.search(s) and not ROOM_WORD.search(s):
        return None
    for op, rx in _CMP_RX:
        if (m := rx.search(s)) and (n := _num(m.group(1))) is not None:
            return op, n
    if (m := _OR_MORE.search(s)) and (n := _num(m.group(1))) is not None:
        return "ge", n
    if (m := _ROOMS_WITH_N.search(s)) and (n := _num(m.group(1))) is not None:
        return "eq", n
    return None


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
        # "which rooms have a door wider than 0.92 m": a filter over measured items is for the
        # tool steps; only a plain length question ("how long is the corridor") is not supported.
        if SELECTOR.search(s) or QUALIFIER_GUARD.search(s) or ROOMS_WITH.search(s):
            return Routed("unknown", lang, signal, scope=_scope(s, ctx), matched=["length_filter"])
        return Routed("abstain", lang, signal, reason="not_supported_yet", topic="length",
                      matched=["length"])

    # A template answers only a question it accounts for whole: a selector or superlative
    # ("which bedroom has the most windows?") or a "rooms with/that have X" clause is NOT
    # "count X"; it goes to the model step (or abstains when models are off).
    if DEICTIC_SIMILARITY.search(s):
        return Routed("unknown", lang, signal, scope=_scope(s, ctx), matched=["deictic_similarity"])

    box_subject = next((subj for subj, rx in SUBJECTS if rx.search(s)), None)
    # A calculation on top of the drawing ("price of wood floor for the kitchens at 2.5 $/m²"):
    # the area alone is not the answer; tool steps and their reasoning step compute it.
    if PRICE.search(s):
        return Routed("unknown", lang, signal, scope=_scope(s, ctx), subject=box_subject,
                      matched=["calculation"])
    # "how many distinct property sets are recorded for the doors", "which layers contain the
    # windows": the thing counted is metadata OF the objects, not the objects. Tool steps (sql).
    if METADATA.search(s) and (box_subject or ROOM_WORD.search(s) or LAYER_WORD.search(s)):
        return Routed("unknown", lang, signal, scope=_scope(s, ctx), subject=box_subject,
                      matched=["metadata", *([box_subject] if box_subject else [])])
    # "rooms that have more than 1 door", "Räume mit mindestens zwei Fenstern": an exact filter over the rooms
    # by how many doors/windows touch each one, then marked, counted or summed by area.
    if box_subject in ("door", "window") and (bc := rooms_by_count_of(s)) is not None:
        op, n = bc
        measure = "area" if AREA.search(s) else "count" if COUNT.search(s) else "list"
        return Routed("rooms_by_count", lang, signal, subject=box_subject, scope={"kind": "floor"},
                      pattern=f"{op}|{n}|{measure}", matched=["rooms_by_count", box_subject, op, str(n), measure])
    rank = "max" if RANK_MAX.search(s) else "min" if RANK_MIN.search(s) else None
    if rank and box_subject is None and not APARTMENT.search(s) and not COUNT.search(s):
        concept, printed = room_concept(s)
        if concept or ROOM_WORD.search(s) or AREA.search(s):
            sc = _scope(s, ctx)
            if sc and sc["kind"] in ("label", "room"):
                sc = None  # the room type is a filter over all rooms, not one printed label
            return Routed("room_rank", lang, signal, subject=rank, scope=sc,
                          topic=concept, pattern="|".join(printed) or None,
                          matched=["room_rank", rank, *([concept] if concept else [])])

    if box_subject is not None and (ANAPHORA.search(s) or ADJACENT.search(s)):
        room = room_of(s, ctx)
        if ADJACENT.search(s) and box_subject in ("door", "window"):
            if room:
                return Routed("adjacent", lang, signal, subject=box_subject,
                              scope={"kind": "room", "room_id": room}, matched=["adjacent", box_subject])
            return Routed("unknown", lang, signal, subject=box_subject,
                          matched=["anaphora", "adjacent", box_subject])
        if not room and ANAPHORA.search(s):
            return Routed("unknown", lang, signal, subject=box_subject,
                          matched=["anaphora", box_subject])
    # "which rooms are there?" just asks for the list (no selection criterion).
    s_sel = WHICH_ARE_THERE.sub("show all ", s)
    if SELECTOR.search(s_sel) or (ROOMS_WITH.search(s) and not LABELLED.search(s)):
        return Routed("unknown", lang, signal, scope=_scope(s, ctx), matched=["needs_model"])

    is_area = bool(AREA.search(s))
    is_count = bool(COUNT.search(s))
    is_locate = bool(LOCATE.search(s_sel) or ALL_OF.search(s))
    subject = None
    subject_rx: re.Pattern[str] | None = None
    scope_text = s
    for subj, rx in SUBJECTS:
        m = rx.search(s)
        if m:
            subject = subj
            subject_rx = rx
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

    # Stairs and connectivity ("show all stairs and where they lead") escalate to deep_agent,
    # while preserving explicit room-id area queries ("room 1.01 ('Treppenhaus')") handled below.
    # "where is the staircase" / "show the stairwell" on a plan with a printed stair room is the
    # room with that label (exact); connectivity ("where do they lead") still needs the agent.
    if (STAIR_ESCALATE.search(s) and not STAIR_LEADS.search(s) and (is_count or is_locate)
            and scope and scope["kind"] == "label"
            and room_concept(scope["label"])[0] == "stairs"):
        return Routed("count", lang, signal, subject="room", scope=scope,
                      matched=["count", "label", "stairs"])
    if STAIR_ESCALATE.search(s) and not (scope and scope["kind"] == "room"):
        return Routed("unknown", lang, signal, scope=scope, matched=["stair_escalate"])

    # Subtype, dimensional, area-threshold, filter, compass & positional qualifiers ("double doors",
    # "north windows", "things with a 30m² area or higher") must never collapse into unadorned counts/areas.
    # Exception: naming a single explicit room ID ("the bedroom on the top right, the 3.05 one, how big is it")
    # asks for that one room's area unless a numeric threshold/filter cue is also present.
    s_qual = _QUOTED_SPAN.sub(" ", _INCLUSIVE_PAREN.sub(" ", _EXCLUSION.sub(" ", s)))
    single_room_area = bool(
        is_area
        and scope
        and scope.get("kind") == "room"
        and not re.search(
            r"(?<!\d)\d+(?:[.,]\d+)?\s*(?:m[²³23]|cm[²³23]?|mm[²³23]?|sqm\b|sq\.?\s*m\b|ft[²³23]?|m(?![a-zA-Z0-9_²³]))|"
            r"(?:[<>]=?|[≥≤])\s*\d+|\b(?:only|higher|more\s+than|less\s+than|at\s+least|at\s+most|above|below|over|under)\b",
            s_qual,
            re.IGNORECASE,
        )
    )
    if QUALIFIER_GUARD.search(s_qual) and not single_room_area:
        return Routed("unknown", lang, signal, scope=scope, matched=["qualifier_guard"])

    has_color = bool(COLOR_CUE.search(s))
    extra_match = ["color_cue"] if has_color else []

    is_bare_subject = bool(
        subject_rx is not None
        and re.fullmatch(rf"\s*(?:{subject_rx.pattern}){_L}*\s*\??\s*", s, re.IGNORECASE)
    )
    is_followup_subject = bool(
        subject is not None
        and _FOLLOWUP_SUBJECT_PREFIX.search(s)
        and len(s.split()) <= 6
        and not FOLLOWUP_CUE.search(s)
    )
    if subject and (is_count or is_locate or is_bare_subject or is_followup_subject) and not (is_area and not is_count):
        if has_color:
            return Routed("unknown", lang, signal, scope=scope, matched=["color_cue", subject])
        return Routed("count", lang, signal, subject=subject, scope=scope, matched=["count", subject])
    if is_area:
        return Routed("area", lang, signal, scope=scope, matched=["area", *extra_match])

    if (is_count or is_locate) and _counts_apartments(s) and not (scope and scope["kind"] == "unit"):
        return Routed("count", lang, signal, subject="apartment", scope=None,
                      matched=["count", "apartment", *extra_match])
    if scope and scope["kind"] == "label" and (is_count or is_locate or ROOM_WORD.search(s)):
        return Routed("count", lang, signal, subject="room", scope=scope,
                      matched=["count", "label", *extra_match])
    bare_rooms = bool(re.fullmatch(rf"\s*(?:{ROOM_WORD.pattern}){_L}*\s*\??\s*", s, re.IGNORECASE))
    if (is_count or is_locate or bare_rooms) and ROOM_WORD.search(s):
        return Routed("count", lang, signal, subject="room", scope=scope,
                      matched=["count", "room", *extra_match])

    return Routed("unknown", lang, signal, scope=scope, matched=extra_match)


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
