"""Shared answer logic: every approach produces the same intermediate - a list of Room records - and the queries are
answered from it identically, so approaches differ only in how they *read* the drawing.

Room record: {"number": "2.10" | None, "label": "Zimmer" | None, "area": m2, "unit": "B" | None,
              "printed": bool (area read from a printed label rather than measured), "bbox": [...], "src": ...}
"""
import re

# Spaces that are not indoor floor area.  Multilingual (de/fr/it/en + common variants); matched on the label.
OUTDOOR = re.compile(
    r"balkon|balcon|balcone|loggia|terrass|terrazz|terrace|veranda|patio|garten|jardin|giardino|garden|"
    r"wintergarten|jardin d'hiver|giardino d'inverno|aussen|extérieur|esterno|outdoor|laube|sitzplatz|"
    r"schacht|gaine|cavedio|shaft|luftraum|vide|void", re.I)


def indoor(room):
    return not (room.get("label") and OUTDOOR.search(room["label"]))


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def answer(rooms, kind, q):
    """-> {"answer", "rooms": [numbers used], "method"} or None."""
    numbered = [r for r in rooms if r.get("number") and r.get("area") is not None]
    if kind == "room":
        hit = [r for r in numbered if r["number"] == q["number"]]
        if not hit:
            hit = [r for r in numbered if norm(r["number"]).replace(",", ".") == norm(q["number"]).replace(",", ".")]
        if not hit:
            return None
        r = hit[0]
        return dict(answer=round(r["area"], 2), rooms=[r["number"]],
                    method="printed area" if r.get("printed") else "measured room polygon")
    if kind == "label":
        hit = [r for r in numbered if norm(r.get("label")) == norm(q["label"])]
        return dict(answer=round(sum(r["area"] for r in hit), 2), rooms=[r["number"] for r in hit],
                    method=f"sum over {len(hit)} rooms labelled {q['label']!r}")
    if kind == "floor":
        hit = [r for r in numbered if indoor(r)]
        return dict(answer=round(sum(r["area"] for r in hit), 2), rooms=[r["number"] for r in hit],
                    method=f"sum over {len(hit)} numbered indoor rooms")
    if kind == "apartment":
        hit = [r for r in numbered if r.get("unit") == q["letter"] and indoor(r)]
        if not hit:
            return None
        return dict(answer=round(sum(r["area"] for r in hit), 2), rooms=[r["number"] for r in hit],
                    method=f"sum over {len(hit)} indoor rooms inside apartment {q['letter']}")
    return None


def answer_all(rooms, queries):
    return [answer(rooms, q["kind"], q["query"]) for q in queries]
