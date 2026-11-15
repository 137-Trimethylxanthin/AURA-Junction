"""Vocabulary of plans: what a text on a drawing is, what a room name means, what a layer or block name suggests.

Three things live here and nowhere else:
  * classify(text)    room number / printed area / apartment tag / room label
  * room_type(label)  the English kind of room for a label in German, French, Italian, English or Finnish,
                      and whether such a room counts as indoor floor area
  * concepts(name)    what a CAD layer or block name suggests (door, window, sanitary fixture ...), with the word
                      that matched and the naming convention it belongs to (plain word, AIA, ISO 13567)
Nothing here decides alone: readers combine these hints with geometry and store the outcome in `definition`.
"""
from __future__ import annotations

import re
import unicodedata

# ------------------------------------------------------------------ texts on a plan
NUM = re.compile(r"^(?:[A-Z]{1,2}[ -]?)?\d{1,3}(?:\.\d{1,3})+$")          # 2.01, U1.05, A 2.10, 1.2.3
AREA = re.compile(r"^(?:(?:NF|NGF|HNF|F|A|Fl\.?|Surf\.?|Sup\.?)\s*[:=]?\s*)?(\d+(?:[.,']\d+)?)\s*"
                  r"(m2|m²|m\^2|qm|sqm)\s*$", re.I)
TAG = re.compile(r"^(?i:Whg|Wohnung|Wohneinheit|WE|App|Appt|Apt|Appartement|Appartamento|Apartment|Appart|Logement|"
                 r"Log|Unit|Einheit|Unité|Unità|Alloggio|Asunto|Huoneisto)\.?\s*(?i:Nr\.?|No\.?|n°|N\.)?\s*"
                 r"([A-Z]{1,3}|\d{1,3})$")


def classify(s: str) -> tuple[str, object]:
    """-> ('area', m2) | ('tag', apartment id) | ('num', room number) | ('label', text) | ('other', text)."""
    s = s.strip()
    if m := AREA.match(s):
        return "area", float(m.group(1).replace(",", ".").replace("'", "."))
    if m := TAG.match(s):
        return "tag", m.group(1).upper()
    if NUM.match(s):
        return "num", s
    if re.search(r"[A-Za-zÀ-ÿ]", s) and not re.search(r"\d", s) and len(s) <= 40:
        return "label", s
    return "other", s


def plain(s: str) -> str:
    """Lower case without accents (ü -> u, é -> e, ß -> ss)."""
    s = (s or "").replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


# ------------------------------------------------------------------ room names
# Spaces that are not indoor floor area (outdoor spaces, shafts, voids).
OUTDOOR = re.compile(
    r"balkon|balcon|loggia|terrass|terrazz|terrace|veranda|patio|garten|jardin|giardino|garden|aussen|exterieur|"
    r"esterno|outdoor|laube|sitzplatz|schacht|gaine|cavedio|shaft|luftraum|vide\b|void|parveke|terassi|piha|kuilu")

# First match wins, so compounds come before their parts.
ROOM_TYPES = [
    ("living_dining", r"wohnen ?/ ?essen|sejour ?/ ?repas|soggiorno ?/ ?pranzo|living ?/ ?dining|wohn-? ?ess"),
    ("kitchen_dining", r"kuche ?/ ?essen|cuisine ?/ ?repas|cucina ?/ ?pranzo|wohnkuche|essku|tupakeittio"),
    ("winter_garden", r"wintergarten|jardin d'hiver|giardino d'inverno"),
    ("balcony", r"balkon|balcon|parveke"),
    ("loggia", r"loggia|laube"),
    ("terrace", r"terrass|terrazz|terrace|veranda|patio|sitzplatz|terassi"),
    ("garden", r"garten|jardin|giardino|garden|piha"),
    ("shaft", r"schacht|gaine|cavedio|shaft|kuilu"),
    ("void", r"luftraum|\bvide\b|void"),
    ("staircase", r"treppe|escalier|scala|stair|porras"),
    ("mezzanine", r"mezzanin|zwischengeschoss|soppalco|\bparvi\b"),
    ("elevator", r"\blift\b|ascenseur|ascensore|elevator|aufzug|hissi"),
    ("bedroom", r"schlaf|chambre|camera da letto|\bcamera\b|bedroom|makuuhuone|\bmh\b"),
    ("living_room", r"wohnen|wohnzimmer|sejour|soggiorno|living|lounge|salon|olohuone|\boh\b"),
    ("dining_room", r"essen|esszimmer|repas|pranzo|dining|ruokailu"),
    ("kitchen", r"kuche|cuisine|cucina|kitchen|keittio|keittokomero|\bk\b|\bkt\b|\bkk\b|\bavok\b|\bavokeittio"),
    ("toilet", r"\bwc\b|toilet|gabinetto"),
    ("bathroom", r"\bbad\b|badezimmer|\bbain|bagno|bath|dusche|douche|doccia|shower|kylpyhuone|\bkph\b|\bkh\b|pesuh|\bph\b|\bsh\b|suihkuh"),
    ("sauna", r"sauna|loyly|(?:^|[/+] ?)sa?\.?(?:$| ?[/+-])"),     # a lone "S" / "SA", not "WOMEN'S" or "S. Transept"
    ("laundry", r"wasch|buanderie|lavanderia|laundry|kodinhoito|\bkhh\b"),
    ("entrance", r"eingang|entree|ingresso|entrance|entry|foyer|eteinen|tuulikaappi|\bet\b|\btk\b|windfang"),
    ("hall", r"(multi.?purpose|sports?|assembly|main|dining|school|gym) hall|\baula\b|mehrzweck(halle|raum)|turnhalle|juhlasali"),
    ("corridor", r"korridor|couloir|corridoio|corridor|\bflur\b|\bgang\b|\bhalle?\b|hallway|kaytava|vorraum|degagement|"
                 r"disimpegno"),
    ("storage", r"reduit|ripostiglio|abstell|storage|closet|keller|\bcave\b|cantina|varasto|\bvar\b|vaatehuone|\bvh\b|"
                r"rangement|garderobe|dressing"),
    ("office", r"buro|bureau|ufficio|office|toimisto|tyohuone"),
    ("commercial", r"gewerbe|commerce|commercio|laden|retail|shop|liike"),
    ("technical", r"technik|technique|tecnico|mechanical|utility|plant|tekninen|heizung"),
    ("garage", r"garage|carport|autotalli|parking|einstellhalle"),
    ("room", r"zimmer|piece|stanza|\braum\b|\blocale?\b|\broom\b|huone"),
]
ROOM_TYPES = [(k, re.compile(p)) for k, p in ROOM_TYPES]

# Abbreviations printed on (mostly US) sheets -> the room word they stand for.  A whole token only ("BR", "BR 2",
# "BR2", "MEZZ."), and only when the label as written names no room type, so words of other languages keep theirs.
ABBREVIATIONS = {
    "mezz": "mezzanine", "br": "bedroom", "bdrm": "bedroom", "bedrm": "bedroom", "bath": "bathroom",
    "ba": "bathroom", "kit": "kitchen", "lr": "living room", "liv": "living room", "dr": "dining room",
    "din": "dining room", "mech": "mechanical", "stor": "storage", "cl": "closet", "clo": "closet",
    "corr": "corridor", "elev": "elevator", "util": "utility", "off": "office",
}
_TOKEN = re.compile(r"[a-z]+")
_BESIDE = re.compile(r"\b(?:under|below|above|over|behind|unter|sous|sotto)\b")      # "ROOM UNDER MEZZ."


def expand(label: str | None) -> str:
    """The plain label with its abbreviations written out: "MEZZ." -> "mezzanine", "BR 2" -> "bedroom 2"."""
    return _TOKEN.sub(lambda m: ABBREVIATIONS.get(m.group(0), m.group(0)), plain(label or ""))


def _typed(s: str) -> str | None:
    for kind, rx in ROOM_TYPES:
        if rx.search(s):
            return kind
    return None


def room_type(label: str | None) -> str | None:
    """English kind of room for a label as written, or None when the word is unknown."""
    s = plain(label or "")
    if not s:
        return None
    kind = _typed(s)
    if kind in (None, "room") and not _BESIDE.search(s):    # "MECH. ROOM" is a technical room, "BR 2" a bedroom
        kind = _typed(expand(s)) or kind
    return kind


def indoor(label: str | None) -> bool:
    """False for balconies, loggias, terraces, gardens, shafts and voids; a room without a name counts as indoor."""
    return not (label and OUTDOOR.search(plain(label)))


# ------------------------------------------------------------------ layer and block names
# word -> concept.  Words of five letters or more also match inside a longer token (German compounds:
# "innentueren"); shorter ones and those in EXACT must be a whole token.
WORDS = {
    "door": "door doors dr drs tur ture turen tuer tueren porte portes porta puerta puertas deur ovi ovet",
    "window": "window windows wndw fenster fenetre fenetres finestra finestre ventana ikkuna ikkunat glaz glazing "
              "verglasung vitrage",
    "fixture": "fixture fixtures fixt pfix sanitary sanitar sanr sanitaire sanitaires sanitari sanitaeranlagen plumbing "
               "plmb kalusteet saniteetti apparate",
    "toilet": "wc toilet toilette toiletten klosett wcistuin pytty",
    "urinal": "urinal urinals urinoir pissoir orinatoio",
    "washbasin": "washbasin basin lavabo lavabos lavandino waschbecken waschtisch lavatory lav vanity pesuallas "
                 "handwaschbecken",
    "sink": "sink spule evier lavello tiskiallas",
    "bathtub": "bathtub tub badewanne wanne baignoire vasca amme kylpyamme",
    "shower": "shower dusche douche doccia suihku",
    "wall": "wall walls wand wande waende mauer mur murs muro muri seina seinat",
    "room": "room rooms raum raume raeume space spaces piece pieces locale locali huone huoneet area areas flache "
            "surface",
    "apartment": "unit units apartment apartments wohnung wohnungen whg appartement appartamento logement asunto",
    "stair": "stair stairs treppe treppen escalier scala scale porras portaat",
    "furniture": "furniture furn mobel moebel meuble mobili arredo kaluste",
    "column": "column columns cols stutze stuetze pilier pilastro pilari",
    "dimension": "dimension dimensions dims dim bemassung cote quote mitta",
    "annotation": "annotation anno text texte txt notes beschriftung",
}
EXACT = {"porta", "porte", "space", "spaces", "scale", "scala", "piece", "pieces", "areas", "units", "notes",
         "texte", "basin", "wande", "locale", "locali"}
NOT_A_MATCH = {"door": {"outdoor", "outdoors", "indoor", "indoors"}, "wall": {"wallpaper"}, "bathtub": {"bathroom"}}
LEX = [(w, c) for c, ws in WORDS.items() for w in ws.split()]
# A name with one of these words is about something that belongs to the thing (its tag, a part, an accessory),
# not the thing itself: "Door Tag", "Dispenser-Toilet_Tissue", "Shower_Seat", "Base Cabinet-Double Door".
ACCESSORY = set("tag tags dispenser tissue seat threshold faucet partition screen cabinet drawer holder grab mirror "
                "mullion hardware handle trim sign schedule legend mop curtain rod hook shelf opener closer sill "
                "symbol elevator otis".split())

# AIA CAD layer guidelines: discipline - major group [- minor group ...], four letters each.
AIA = re.compile(r"^[A-Z]{1,2}-[A-Z]{4}(?:-[A-Z0-9]{1,4})*$")
AIA_MAJOR = {"DOOR": "door", "GLAZ": "window", "WNDW": "window", "FIXT": "fixture", "PFIX": "fixture", "SANR": "fixture",
             "WALL": "wall", "AREA": "room", "COLS": "column", "FURN": "furniture", "ANNO": "annotation", "STRS": "stair"}
AIA_CURTAIN = {"CWMG", "CURT"}          # curtain wall grid and panels: glazing, but not windows
# ISO 13567: agent (2) + element (6) + presentation (2).  The element field carries a national classification;
# only the CI/SfB element numbers are understood here.
ISO = re.compile(r"[A-Z][A-Z0-9-]\d{2}[A-Z0-9_-]{4}[A-Z-][A-Z0-9-][A-Z0-9_-]*")
SFB = [("314", "window"), ("315", "door"), ("32", "door"), ("74", "fixture"), ("21", "wall"), ("22", "wall")]


def tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    return [t for t in re.split(r"[^a-z]+", plain(s)) if t]


def concepts(name: str | None) -> dict[str, dict]:
    """What a layer or block name suggests: {concept: {"word", "convention", "weight"}}; empty when nothing matches."""
    out: dict[str, dict] = {}
    if not name:
        return out

    def add(concept, word, convention, weight):
        if concept and (concept not in out or out[concept]["weight"] < weight):
            out[concept] = dict(word=word, convention=convention, weight=weight)

    up = name.strip().upper()
    toks = tokens(name)
    if AIA.match(up):
        # the major group names the thing; IDEN is its annotation; another minor group (FRAM, SILL ...) a part of it;
        # a layer of any other major group is "named otherwise" and is left out of searches by geometry
        parts = up.split("-")
        minors = [p for p in parts[2:] if not p.isdigit()]
        major = next((p for p in parts[1:] if p in AIA_MAJOR), None)
        if "IDEN" in minors or major == "ANNO":
            add("annotation", "IDEN" if "IDEN" in minors else "ANNO", "aia", 0.7)
        elif major is None:
            add("other", parts[1], "aia", 0.7)
        elif AIA_MAJOR[major] == "window" and AIA_CURTAIN & set(minors):
            add("curtain_wall", "-".join(parts[1:]), "aia", 0.7)
        else:
            add(AIA_MAJOR[major], major, "aia", 0.75 if (parts[0], major) == ("P", "FIXT") or "SANR" in parts else 0.7)
            if [p for p in minors if p != major and p not in ("SANR", "FIXT", "PFIX")]:
                out[AIA_MAJOR[major]]["part"] = True
        return out
    if ISO.fullmatch(name.strip()):
        code = re.sub(r"\D", "", up[2:8])
        for prefix, concept in SFB:
            if code.startswith(prefix):
                add(concept, code, "iso13567/ci-sfb", 0.3)
                break
    if acc := ACCESSORY & set(toks):
        add("accessory", sorted(acc)[0], "word", 0.6)
        return out
    for t in toks:
        for w, c in LEX:
            if t == w or (len(w) >= 5 and w not in EXACT and w in t and t not in NOT_A_MATCH.get(c, ())):
                add(c, w, "word", 0.6)
    if "window" in out and out["window"]["word"].startswith(("glaz", "vergl", "vitr")) and {"panel", "system"} & set(toks):
        out["curtain_wall"] = out.pop("window")      # glazed panels of a curtain wall system
    if "door" in out and "window" in out:          # "Window & Door ...": says neither
        del out["door"], out["window"]
    if "sink" in out and ("washbasin" in out or {"bathroom", "pedestal", "bath"} & set(toks)):
        out.setdefault("washbasin", out["sink"])
        del out["sink"]
    return out
