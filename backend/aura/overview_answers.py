"""“What does this drawing show?”: answered from what the drawing's document already says, with 0 model calls.

The description is built from the title, the source and file format, the kind of sheet when the title or
the content says it (floor plan, site plan, elevation, section, detail, schedule), the page shown for a
PDF of several pages, and the counts of what was read from the sheet (rooms, doors, windows, fixtures,
printed dimensions). It states nothing the document does not hold, so it needs no evidence boxes.
The sentence is written in English, German, French or Finnish (the interface languages); the title, the
source and the names stay as they are.
"""

from __future__ import annotations

import re
from typing import Any

from . import answer as A
from .contract import ToolResult
from .tools import LoadedDrawing, _rid

_THING = r"(?:drawing|sheet|plan|floor ?plan|page|file|image|document|picture)"
_DE = r"(?:zeichnung|plan|grundriss|blatt|seite|datei|bild)"
_FR = r"(?:dessin|plan|feuille|page|fichier|image|document)"
_FI = r"(?:piirustus|piirustuksessa|pohjapiirros|pohjapiirroksessa|kuva|kuvassa|sivu|sivulla|tiedosto)"
_END = r"[\s?.!…]*$"
ASK = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|bitte\s+)?(?:"
    rf"what (?:does|do) (?:this|the) {_THING} (?:show|depict|contain|represent)(?: me)?"
    rf"|what(?:'s| is)(?: shown| depicted| drawn)?(?: on| in)? (?:this|the) {_THING}(?: about| showing)?"
    rf"|what (?:kind|type|sort) of {_THING} is (?:this|it)"
    rf"|what (?:am i|are we) looking at"
    rf"|(?:briefly )?(?:describe|summari[sz]e|explain) (?:this|the) {_THING}(?: briefly| for me| to me)?"
    rf"|(?:give me |show me )?an? (?:short |brief |quick )?(?:overview|summary|description) of (?:this|the) {_THING}"
    rf"|was zeigt (?:diese|dieser|dieses|die|der|das) {_DE}"
    rf"|was (?:ist|sieht man|sehe ich) (?:auf|in) (?:dieser|diesem|der|dem) {_DE}(?: zu sehen| dargestellt| abgebildet)?"
    rf"|was (?:ist|stellt) (?:das|dies|diese|dieser|dieses) (?:für )?(?:eine?n? )?{_DE}(?: dar)?"
    rf"|was für eine?n? {_DE} ist (?:das|dies)"
    rf"|beschreiben? (?:sie )?(?:mir )?(?:diese|diesen|dieses|die|den|das) {_DE}"
    rf"|que (?:montre|représente|contient) (?:ce|cette|le|la) {_FR}"
    rf"|qu['’ ]est[- ]ce que (?:ce|cette|le|la) {_FR} (?:montre|représente|contient)"
    rf"|qu['’ ]est[- ]ce que c['’ ]est (?:que )?(?:ce|cette) {_FR}"
    rf"|décri(?:s|vez|re) (?:ce|cette|le|la) {_FR}"
    rf"|mitä (?:tämä|tässä|tällä) {_FI} (?:esittää|näyttää|kuvaa|on)"
    rf"|kuvaile (?:tämä|tätä) {_FI}"
    rf"){_END}", re.IGNORECASE)

# the kind of sheet, read from its title (first match wins): (pattern, {lang: name})
SHEET_KINDS: list[tuple[re.Pattern[str], dict[str, str]]] = [
    (re.compile(r"\bsite\s*plan\b|\blageplan\b|\bplan de (?:masse|situation)\b|\basemapiirros\b", re.IGNORECASE),
     {"en": "a site plan", "de": "ein Lageplan", "fr": "un plan de masse", "fi": "asemapiirros"}),
    (re.compile(r"\belevations?\b|\bansicht(?:en)?\b|\bfa[cç]ades?\b|\bjulkisivu", re.IGNORECASE),
     {"en": "an elevation sheet", "de": "ein Ansichtsblatt", "fr": "une feuille d’élévations", "fi": "julkisivupiirustus"}),
    (re.compile(r"\bsections?\b|\bschnitte?\b|\bcoupes?\b|\bleikkaus", re.IGNORECASE),
     {"en": "a section sheet", "de": "ein Schnittblatt", "fr": "une feuille de coupes", "fi": "leikkauspiirustus"}),
    (re.compile(r"\bschedules?\b|\blegend\b|\bt[üu]rliste\b|\bnomenclature\b", re.IGNORECASE),
     {"en": "a schedule sheet (not a plan)", "de": "ein Listenblatt (kein Grundriss)",
      "fr": "une feuille de nomenclature (pas un plan)", "fi": "luettelolehti (ei pohjapiirros)"}),
    (re.compile(r"\bdetails?\b|\bdetailzeichnung\b|\bd[ée]tails?\b|\bdetalji", re.IGNORECASE),
     {"en": "a detail sheet", "de": "ein Detailblatt", "fr": "une feuille de détails", "fi": "detaljipiirustus"}),
    (re.compile(r"\b(?:cover|title)\s*(?:sheet|page)\b|\bgeneral notes\b|\bdeckblatt\b", re.IGNORECASE),
     {"en": "a cover or notes sheet (not a plan)", "de": "ein Deck- oder Hinweisblatt (kein Grundriss)",
      "fr": "une page de garde ou de notes (pas un plan)", "fi": "kansi- tai huomautuslehti (ei pohjapiirros)"}),
    (re.compile(r"\broof\s*plan\b|\bdachaufsicht\b|\bplan de toiture\b", re.IGNORECASE),
     {"en": "a roof plan", "de": "eine Dachaufsicht", "fr": "un plan de toiture", "fi": "kattopiirustus"}),
    (re.compile(r"\bfloor\s*plan\b|\bgrundriss\b|\bplan d['’]étage\b|\bpohjapiirros\b|\b(?:first|second|third|ground|"
                r"basement|upper|lower|main)\s+(?:floor|level)\b|\bfloor\b|\bgeschoss\b|\bétage\b|\bkerros\b", re.IGNORECASE),
     {"en": "a floor plan", "de": "ein Grundriss", "fr": "un plan d’étage", "fi": "pohjapiirros"}),
]
FLOOR_PLAN = SHEET_KINDS[-1][1]
DRAWING = {"en": "a drawing", "de": "eine Zeichnung", "fr": "un dessin", "fi": "piirustus"}

FORMATS = {"DWG", "DXF", "IFC", "PDF", "PNG", "JPG", "SVG"}
FORMAT_OF_KIND = {"cad_dxf": "CAD", "ifc": "IFC", "raster": "image"}
#: evidence subjects that are not fixtures of their own
NOT_FIXTURES = {"door", "window", "label", "room"}

# (singular, plural) per language
NOUNS: dict[str, dict[str, tuple[str, str]]] = {
    "en": {"room": ("room", "rooms"), "door": ("door", "doors"), "window": ("window", "windows"),
           "fixture": ("fixture", "fixtures"), "dimension": ("printed dimension", "printed dimensions")},
    "de": {"room": ("Raum", "Räume"), "door": ("Tür", "Türen"), "window": ("Fenster", "Fenster"),
           "fixture": ("Ausstattungsobjekt", "Ausstattungsobjekte"), "dimension": ("Maßangabe", "Maßangaben")},
    "fr": {"room": ("pièce", "pièces"), "door": ("porte", "portes"), "window": ("fenêtre", "fenêtres"),
           "fixture": ("équipement", "équipements"), "dimension": ("cote imprimée", "cotes imprimées")},
    "fi": {"room": ("huone", "huonetta"), "door": ("ovi", "ovea"), "window": ("ikkuna", "ikkunaa"),
           "fixture": ("kaluste", "kalustetta"), "dimension": ("mittamerkintä", "mittamerkintää")},
}
TEXT = {
    "en": {"is": "“{title}” is {kind}", "from": " from {source}", "page": "page {n} of {m}", "found": "Read from it: {list}.",
           "and": " and ", "none": "No rooms, doors, windows, fixtures or printed dimensions were read from it.",
           "outdoor": " ({n} of them outdoor)", "this_page": "The counts are for the page shown."},
    "de": {"is": "„{title}“ ist {kind}", "from": " aus {source}", "page": "Seite {n} von {m}", "found": "Daraus gelesen: {list}.",
           "and": " und ", "none": "Es wurden keine Räume, Türen, Fenster, Ausstattungsobjekte oder Maßangaben daraus gelesen.",
           "outdoor": " (davon {n} außen)", "this_page": "Die Zahlen gelten für die gezeigte Seite."},
    "fr": {"is": "« {title} » est {kind}", "from": " de {source}", "page": "page {n} sur {m}", "found": "Lu sur la feuille : {list}.",
           "and": " et ", "none": "Aucune pièce, porte, fenêtre, aucun équipement ni aucune cote n’y a été lu.",
           "outdoor": " (dont {n} en extérieur)", "this_page": "Les nombres valent pour la page affichée."},
    "fi": {"is": "”{title}” on {kind}", "from": ", lähde {source}", "page": "sivu {n}/{m}", "found": "Siitä luettu: {list}.",
           "and": " ja ", "none": "Siitä ei luettu huoneita, ovia, ikkunoita, kalusteita eikä mittamerkintöjä.",
           "outdoor": " (joista {n} ulkona)", "this_page": "Luvut koskevat näytettyä sivua."},
}


def is_overview_question(question: str) -> bool:
    return bool(ASK.match(question or ""))


def source_and_format(doc: dict) -> tuple[str | None, str | None]:
    """The listing's subtitle names the source right before the file format ("OpeningDesign · CTR · PDF · …")."""
    parts = [p.strip() for p in str(doc.get("subtitle") or "").split(" · ") if p.strip()]
    for i, p in enumerate(parts):
        if p and all(x in FORMATS for x in p.split(" + ")):
            return (" · ".join(parts[:i]) or None) if i <= 2 else parts[i - 1], p
    credit = str((doc.get("credit") or {}).get("text") or "").removeprefix("Source:").strip() or None
    ext = str(doc.get("source_ext") or "").lstrip(".").upper()
    return credit, ext or FORMAT_OF_KIND.get(str(doc.get("kind") or ""))


def counts_of(doc: dict) -> dict[str, int]:
    ev = doc.get("evidence") or {}
    rooms = doc.get("rooms") or []
    dims = int(doc.get("printed_dimensions") or 0)
    if isinstance(doc.get("sheet"), dict) and isinstance(doc.get("dimensions"), list):
        dims = sum(1 for x in doc["dimensions"] if "x" in x)   # one page of several: the dimensions placed on it
    return {
        "room": len(rooms), "outdoor": sum(1 for r in rooms if r.get("outdoor")),
        "door": len(ev.get("door") or []), "window": len(ev.get("window") or []),
        "fixture": sum(len(v or []) for k, v in ev.items() if k not in NOT_FIXTURES),
        "dimension": dims,
    }


def sheet_kind(doc: dict, counts: dict[str, int], lang: str) -> str:
    title = str(doc.get("title") or "")
    for rx, names in SHEET_KINDS:
        if rx.search(title):
            return names[lang]
    return (FLOOR_PLAN if counts["room"] else DRAWING)[lang]


def describe(doc: dict, lang: str) -> str:
    lang = lang if lang in TEXT else "en"
    t, nouns = TEXT[lang], NOUNS[lang]
    c = counts_of(doc)
    source, fmt = source_and_format(doc)
    sheet = doc.get("sheet") if isinstance(doc.get("sheet"), dict) else None
    head = t["is"].format(title=str(doc.get("title") or doc.get("id") or "").strip(), kind=sheet_kind(doc, c, lang))
    if source:
        head += t["from"].format(source=source)
    extra = [x for x in (fmt, t["page"].format(n=sheet["page"], m=sheet["pages"]) if sheet else None) if x]
    if extra:
        head += f" ({', '.join(extra)})"
    found = []
    for k in ("room", "door", "window", "fixture", "dimension"):
        if c[k]:
            s = f"{c[k]} {nouns[k][0 if c[k] == 1 else 1]}"
            if k == "room" and c["outdoor"]:
                s += t["outdoor"].format(n=c["outdoor"])
            found.append(s)
    if not found:
        return f"{head}. {t['none']}"
    listed = found[0] if len(found) == 1 else ", ".join(found[:-1]) + t["and"] + found[-1]
    return f"{head}. {t['found'].format(list=listed)}" + (f" {t['this_page']}" if sheet else "")


_LANG_OF = [("de", re.compile(r"^\W*(?:bitte\s+)?(?:was|beschreib)", re.IGNORECASE)), ("fr", re.compile(r"^\W*(?:que?\b|qu['’]|décri)", re.IGNORECASE)),
            ("fi", re.compile(r"^\W*(?:mitä|kuvaile)", re.IGNORECASE))]


def try_answer(question: str, lang: str, d: LoadedDrawing) -> A.Built | None:
    """The description as a built note for an overview question, else None (the usual path runs)."""
    if not is_overview_question(question):
        return None
    # the question's own words say its language where the router saw no signal ("Was zeigt diese Zeichnung?")
    lang = next((code for code, rx in _LANG_OF if rx.match(question)), lang)
    doc = d.drawing
    if not (doc.get("store") or "evidence" in doc):   # a drawing answered from a model file: its counts are not in the document
        return None
    text = describe(doc, lang)
    methods = [m for m in (doc.get("evidence_method") or {}).values() if m]
    tr = ToolResult(result_id=_rid(d.id, "sql", {"overview": True}), tool="sql", args={"overview": True}, kind="note",
                    subject=None, text=text, summary=text, numbers=[float(v) for v in counts_of(doc).values() if v],
                    method=methods[0] if len(set(methods)) == 1 else "cad_geometry" if not methods else methods[0])
    scope: dict[str, Any] = {"kind": "floor"}
    body = A._base(question, lang, scope) | {
        "answer": {"kind": "note", "value": text},
        "evidence": {"boxes": [], "rooms": []},
        "canonical_en": "What does this drawing show",
        "method": tr.method,
        "trace": [
            A.question_node(question, lang),
            A.intent_node("overview · whole drawing", "router · overview"),
            {"id": "read", "parent": "q", "title": "Read the drawing’s record", "tag": "Drawing store · summary",
             "body": text, "kind": "exact", "tool": A.tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "from the drawing’s record", "body": text, "kind": "exact"},
        ],
    }
    return A.Built(body, [tr], [], None)
