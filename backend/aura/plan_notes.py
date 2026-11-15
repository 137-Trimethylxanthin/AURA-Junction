"""Notes drawn inside a plan image, in the language of the interface that shows it.

The drawing store writes the plan.svg of an image plan once, without a language; a plan whose scale was estimated
carries one English footnote (store/jstore/service/aura_model.py ESTIMATE_NOTE). `GET /drawings/{id}/plan.svg?lang=xx`
serves the same file with that footnote in `xx`; nothing else in the file is touched.
"""

from __future__ import annotations

import html
from pathlib import Path

ESTIMATE_NOTE = "Scale estimated from the sizes of drawn objects (doors, fixtures, rooms): lengths and areas are approximate"
ESTIMATE_NOTE_IN = {
    "de": "Massstab aus den Grössen gezeichneter Objekte geschätzt (Türen, Ausstattung, Räume): Längen und Flächen sind Näherungswerte",
    "fr": "Échelle estimée d’après la taille des objets dessinés (portes, équipements, pièces) : longueurs et surfaces approximatives",
    "fi": "Mittakaava arvioitu piirrettyjen kohteiden koosta (ovet, kalusteet, huoneet): pituudet ja pinta-alat ovat likimääräisiä",
    "it": "Scala stimata dalle dimensioni degli oggetti disegnati (porte, sanitari, locali): lunghezze e superfici sono approssimative",
}


def translated_plan(path: Path, lang: str | None) -> bytes | None:
    """The plan.svg at `path` with its estimated-scale footnote in `lang`, or None when the file is served as it is
    (English, an unknown language, or a plan without the footnote)."""
    note = ESTIMATE_NOTE_IN.get(lang or "")
    if note is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    marker = html.escape(ESTIMATE_NOTE)
    return text.replace(marker, html.escape(note)).encode("utf-8") if marker in text else None
