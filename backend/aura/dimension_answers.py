"""Length questions answered without a model: what the sheet prints, else what the plan measures.

Three questions, each finishing with a length (never a count):

  - the overall width / depth / size of the plan: the printed overall dimension, else the sum of a
    chain of printed dimensions along one side (said so), else the extent of the walls / room
    outlines (labelled measured; an estimate on a scan that states no scale);
  - the width / length / size of a room: the room size printed in it, else the sides of the
    smallest rotated rectangle around its outline (labelled measured / estimate);
  - the longest / shortest printed dimension.

Every dimension used is evidence: its box on the page shown, the printed text as its label. A
value printed in feet and inches is quoted as printed beside the metres. The answer itself is
metric; `units.apply` converts it when the question asks for another unit.
"""
from __future__ import annotations

import dataclasses
import math
import re
from typing import Any

from . import answer as A
from .contract import Box, EvidenceRoom, ToolResult
from .router import LABEL_CONCEPTS, ROOMS_WITH, _scope
from .tools import LoadedDrawing, _rid


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![^\W\d_])(?:{p})(?![^\W\d_])", re.IGNORECASE)


WIDTH = _rx(r"wide|width|widths|breit|breite|largeur|large|leveys|leveä|levyinen|larghezza|largo|larga")
LENGTH = _rx(r"long|length|lengths|depth|deep|länge|laenge|lang|tiefe|tief|longueur|longue|profondeur|profond|"
             r"pituus|pitkä|syvyys|lunghezza|lungo|lunga|profondità")
SIZE = _rx(r"dimensions?|size|sizes|measurements?|measures?|abmessung(?:en)?|masse|maße|ausmasse|grösse|größe|"
           r"dimensioni|misure|mitat|koko|taille|cotes")
WHOLE = _rx(r"building|house|home|plan|floor ?plan|floor|storey|story|level|flat|footprint|overall|total|whole|"
            r"entire|drawing|gebäude|haus|grundriss|gesamt\w*|geschoss|bâtiment|batiment|maison|étage|"
            r"rakennu\w*|talo\w*|kerro\w*|asunno\w*|edificio|casa|piano|pianta")
SUPER_MAX = _rx(r"longest|largest|biggest|greatest|maximum|längste[rsn]?|grösste[rsn]?|größte[rsn]?|plus longue?|"
                r"plus grande?|pisin|suurin|più lung[ao]|più grande")
SUPER_MIN = _rx(r"shortest|smallest|minimum|kürzeste[rsn]?|kleinste[rsn]?|plus courte?|plus petite?|lyhin|pienin|"
                r"più cort[ao]|più piccol[ao]")
DIM_WORD = _rx(r"dimensions?|measurements?|dimension lines?|masse?|maße?|masslinien?|bemassung\w*|cotes?|mitta\w*|"
               r"quot[ae]|misur[ae]")
#: things a length question may be about that this module does not answer
OTHER = _rx(r"doors?|windows?|walls?|stairs?|staircases?|steps?|ceiling|türe?n?|fenster|wand|wände|treppe\w*|portes?|"
            r"fenêtres?|murs?|escalier\w*|ovi\w*|ove[tn]\w*|ikkun\w+|sein\w+|port[ae]|finestr[ae]|mur[oi]|scal[ae]|perimeter|"
            r"périmètre|umfang|area|fläche|surface|pinta-ala|volume\w*|tilavuus|height|high|tall|höhe|hoch|hauteur|"
            r"haut|korkeus|altezza|alto|between|distance|far|zwischen|entre|väli\w*|thick\w*|dick\w*|épais\w*")
FILTER = _rx(r"which|how many|wie viele|combien|kuinka monta|quant[ie]|more than|less than|at least|at most|wider than|"
             r"longer than|narrower than|shorter than|larger than|smaller than|mehr als|plus de|enemmän kuin")

#: "the largest room", "das grösste Zimmer": the room is picked by its area, then its sides are given
RANK_ROOM = _rx(r"rooms?|space|zimmer|raum|pièce|chambre|huone|stanza|camera|locale")

IMPERIAL = ("ft-in", "ft", "in")


def _m(v: float) -> str:
    return f"{v:.2f} m"


def _printed(text: str | None, value_m: float, unit: str | None) -> str:
    """'12' 6" as printed = 3.81 m' for a value the sheet does not print in metres, else '3.20 m as printed'."""
    t = (text or "").strip()
    if t and (unit in IMPERIAL or "'" in t or '"' in t):
        return f"{t} as printed = {_m(value_m)}"
    if t and unit in ("mm", "cm"):
        return f"{t} {unit} as printed = {_m(value_m)}"
    if t and t not in _m(value_m) and re.fullmatch(r"[\d.,\s]+", t):
        # a bare number that is not the metres: the file's own drawing units (inches or millimetres on a CAD sheet)
        return f"{_m(value_m)} (written {t} in the drawing's own units)"
    return f"{_m(value_m)} as printed" + (f" ({t})" if t and t not in _m(value_m) else "")


def _usable(d: LoadedDrawing) -> list[dict]:
    """The printed lengths an answer may use: on the page shown when any is, else on any page."""
    dims = [x for x in d.drawing.get("dimensions") or []
            if x.get("kind") in ("linear", "overall") and isinstance(x.get("value_m"), (int, float)) and x["value_m"] > 0]
    shown = [x for x in dims if "x" in x]
    return shown or dims


def _box(x: dict) -> Box | None:
    if "x" not in x:
        return None
    return Box(id=x["id"], x=x["x"], y=x["y"], w=x["w"], h=x["h"], subject="label", note=str(x.get("text") or ""),
               room_id=x.get("room_id"))


def chains(dims: list[dict], orientation: str, tol: float = 0.8, gap: float = 1.2) -> list[list[dict]]:
    """Runs of printed dimensions that continue each other along one line (end to start, same offset)."""
    axis, across = (0, 1) if orientation == "horizontal" else (1, 0)
    rows: list[tuple[float, float, float, dict]] = []
    for x in dims:
        ln = x.get("line")
        if x.get("orientation") != orientation or not ln or len(ln) < 2:
            continue
        a, b = sorted((ln[0][axis], ln[-1][axis]))
        rows.append(((ln[0][across] + ln[-1][across]) / 2, a, b, x))
    rows.sort(key=lambda r: (r[0], r[1]))
    out: list[list[dict]] = []
    used: set[int] = set()
    for i, (off, _a, b, x) in enumerate(rows):
        if i in used:
            continue
        run, end = [x], b
        used.add(i)
        for j, (off2, a2, b2, x2) in enumerate(rows):
            if j in used or abs(off2 - off) > tol:
                continue
            if abs(a2 - end) <= gap:
                run.append(x2)
                end = b2
                used.add(j)
        if len(run) >= 2:
            out.append(run)
    return out


def _side(dims: list[dict], orientation: str) -> dict | None:
    """The plan's size along one direction from what is printed: an overall dimension, else the longest of (a single
    dimension, a chain of dimensions summed)."""
    mine = [x for x in dims if x.get("orientation") == orientation]
    overall = [x for x in mine if x.get("kind") == "overall"]
    if overall:
        x = max(overall, key=lambda x: x["value_m"])
        return {"value": x["value_m"], "dims": [x], "how": "overall"}
    best = max(mine, key=lambda x: x["value_m"], default=None)
    run = max(chains(dims, orientation), key=lambda r: sum(x["value_m"] for x in r), default=None)
    if run and (best is None or sum(x["value_m"] for x in run) > best["value_m"] * 1.02):
        return {"value": round(sum(x["value_m"] for x in run), 3), "dims": run, "how": "chain"}
    if best is not None:
        return {"value": best["value_m"], "dims": [best], "how": "longest"}
    return None


def _side_text(name: str, s: dict) -> str:
    x = s["dims"][0]
    if s["how"] == "overall":
        return f"{name}: {_printed(x.get('text'), s['value'], x.get('unit'))} (the overall dimension printed on the sheet)"
    if s["how"] == "chain":
        parts = " + ".join(str(x.get("text")) for x in s["dims"])
        return (f"{name}: {_m(s['value'])}, the sum of {len(s['dims'])} dimensions printed in a row along one side "
                f"({parts}); no overall dimension is printed")
    return (f"{name}: {_printed(x.get('text'), s['value'], x.get('unit'))}, the longest {x.get('orientation')} dimension "
            f"printed; no overall dimension is printed, so the whole side may be longer")


def classify(question: str, d: LoadedDrawing) -> dict | None:
    """What a plain length question asks for, or None when it is not one this module answers."""
    q = question
    wants_w, wants_l, wants_s = bool(WIDTH.search(q)), bool(LENGTH.search(q)), bool(SIZE.search(q))
    if not (wants_w or wants_l or wants_s):
        return None
    sup = "max" if SUPER_MAX.search(q) else "min" if SUPER_MIN.search(q) else None
    if sup and DIM_WORD.search(q) and not OTHER.search(q):
        return {"ask": "extreme", "which": sup}
    axis = "size" if wants_s or (wants_w and wants_l) else "width" if wants_w else "length"
    if sup and RANK_ROOM.search(q) and not (OTHER.search(q) or FILTER.search(q) or ROOMS_WITH.search(q)):
        return {"ask": "room", "axis": axis, "scope": {"kind": "floor"}, "rank": sup}  # "how wide is the largest room"
    if OTHER.search(q) or FILTER.search(q) or sup or ROOMS_WITH.search(q):
        return None
    scope = _scope(q, d.ctx)
    if scope and scope.get("kind") in ("room", "label"):
        return {"ask": "room", "axis": axis, "scope": scope}
    if scope is None:
        types = _types_asked(q, d)
        if types:
            return {"ask": "room", "axis": axis, "scope": {"kind": "floor"}, "types": types}
        if WHOLE.search(q):
            return {"ask": "overall", "axis": axis}
    return None


#: the router's room concepts -> the store's normalised room types (rooms.room_type), for labels in any language
CONCEPT_TYPES = {"bedroom": ("bedroom",), "living": ("living", "living_room", "dining"),
                 "kitchen": ("kitchen", "kitchen_dining"),
                 "bathroom": ("bathroom",), "storage": ("storage",), "corridor": ("corridor", "hall", "entrance"),
                 "outdoor": ("terrace", "balcony", "loggia"), "stairs": ("staircase",)}


def _types_asked(q: str, d: LoadedDrawing) -> set[str]:
    """The room types the question names that this drawing has (a room labelled TUPAK is a kitchen)."""
    have = {str(r.get("room_type")) for r in d.drawing.get("rooms") or [] if r.get("room_type")}
    out: set[str] = set()
    for concept, rx, _printed_labels in LABEL_CONCEPTS:
        if rx.search(q):
            out |= have & set(CONCEPT_TYPES.get(concept, ()))
    for t in have - {"room"}:  # the type's own name ("office", "toilet", "laundry")
        if _rx(re.escape(t.replace("_", " ")) + "s?").search(q):
            out.add(t)
    return out


def _result(d: LoadedDrawing, args: dict, kind: str, value: float | None, text: str, boxes: list[Box],
            rooms: list[EvidenceRoom], method: str) -> ToolResult:
    return ToolResult(result_id=_rid(d.id, "sql", args), tool="sql", args=args, kind=kind, subject="length",
                      value=value, text=text, numbers=[value] if value is not None else [], boxes=boxes, rooms=rooms,
                      summary=text, method=method)


def _measure(label: str, value_m: float, x: dict | None = None, *, source: str = "printed") -> dict:
    """One length the answer states, as a value (the units hook converts values, not prose)."""
    out: dict[str, Any] = {"label": label, "value": round(float(value_m), 4), "unit": "m", "source": source}
    if x is not None and x.get("text"):
        out["printed"] = str(x["text"])
    return out


def _body(question: str, lang: str, d: LoadedDrawing, scope: dict, tr: ToolResult, canonical: str, *, value: float | None,
          text: str, printed: bool, estimate: bool, measures: list[dict] | None = None) -> A.Built:
    ids = [b.id for b in tr.boxes] or [A.room_eid(r.room_id) for r in tr.rooms]
    answer: dict[str, Any] = ({"kind": "quantity", "value": round(float(value), 3), "unit": "m"} if value is not None
                              else {"kind": "note", "value": text})
    kind = "exact" if printed or not estimate else "model"
    tag = "read from the sheet · as printed" if printed else "estimate · measured" if estimate else "measured on the plan"
    body = A._base(question, lang, scope) | {
        "answer": answer,
        "evidence": {"boxes": [b.model_dump(exclude_none=True) for b in tr.boxes],
                     "rooms": [r.model_dump(exclude_none=True) | {"area_m2": r.area_m2} for r in tr.rooms]},
        "canonical_en": canonical,
        "method": tr.method,
        **({"explanation": text} if value is not None else {}),  # a note is its own explanation
        **({"measures": measures} if measures else {}),
        # printed values are quoted as printed whoever read them; a measure on a scan's outlines is an estimate
        "basis": ("printed" if printed else "estimate" if estimate or tr.method in ("vision", "ocr") else "measured"),
        "trace": [
            A.question_node(question, lang),
            A.intent_node(f"length · {A.scope_text(scope)}", "router · length"),
            {"id": "read", "parent": "q",
             "title": "Read the printed dimensions" if printed else "Measured the outline (estimate)" if kind == "model" else "Measured the outline",
             "tag": "Drawing store · dimensions" if printed else "Drawing store · geometry",
             "body": text, "kind": kind, **({"evidence_ids": ids} if ids else {}), "tool": A.tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": tag, "body": text, "kind": kind,
             **({"evidence_ids": ids} if ids else {})},
        ],
    }
    return A.Built(body, [tr], [], None)


def _weakest(dims: list[dict]) -> str:
    rank = {"cad_geometry": 0, "pdf_vector": 1, "ocr": 2, "vision": 3}
    return max((x.get("method") or "vision" for x in dims), key=lambda m: rank.get(m, 3), default="vision")


def _overall(question: str, lang: str, d: LoadedDrawing, axis: str) -> A.Built | None:
    dims = _usable(d)
    scope = {"kind": "floor"}
    sides = {"width": _side(dims, "horizontal"), "length": _side(dims, "vertical")}
    names = {"width": "Width (across the page)", "length": "Depth (up the page)"}
    wanted = ["width", "length"] if axis == "size" else [axis]
    ext = d.drawing.get("extent_m") or {}
    measured = {"width": ext.get("width_m"), "length": ext.get("depth_m")}
    est = bool(ext.get("estimate"))
    scan = (d.drawing.get("evidence_method") or {}).get("room") in ("vision", "ocr")
    label = ("an estimate: the sheet states no scale" if est else
             "measured on the outlines traced on the scan, not printed: an estimate" if scan else
             "measured on the plan, not printed")
    parts, used, values, printed_any, lower_bound = [], [], [], False, False
    measures: list[dict] = []
    mname = {"width": "width", "length": "depth"}
    for k in wanted:
        s = sides[k]
        # a single dimension much shorter than the plan itself is not the plan's size: the measured extent answers
        if s and s["how"] != "overall" and measured[k] and not 0.8 * measured[k] <= s["value"] <= 1.25 * measured[k]:
            s = None
        if s and s["how"] == "longest" and not measured[k]:
            x = s["dims"][0]
            parts.append(f"{names[k]}: the sheet prints no overall dimension and no row of dimensions that spans this "
                         f"side; the longest {x.get('orientation')} dimension printed is "
                         f"{_printed(x.get('text'), s['value'], x.get('unit'))}, so the plan is at least that {'wide' if k == 'width' else 'deep'} here")
            used += s["dims"]
            lower_bound = True
        elif s:
            parts.append(_side_text(names[k], s))
            used += s["dims"]
            values.append(s["value"])
            measures.append(_measure(mname[k], s["value"], s["dims"][0] if len(s["dims"]) == 1 else None))
            printed_any = True
        elif measured[k]:
            measures.append(_measure(mname[k], measured[k], source="estimate" if est else "measured"))
            parts.append(f"{names[k]}: {'≈ ' if est else ''}{_m(measured[k])}, the extent of the {ext.get('source', 'rooms')} "
                         f"({label})")
            values.append(measured[k])
        elif axis == "size" or sides["length" if k == "width" else "width"]:
            parts.append(f"{names[k]}: no dimension is printed along this side on the page shown")
            lower_bound = True
    if axis != "size" and not values and not used:
        other = "length" if axis == "width" else "width"
        if sides[other]:  # asked for one direction, only the other is printed: say what is there
            parts.append(_side_text(names[other], sides[other]))
            used += sides[other]["dims"]
            printed_any = True
    if not used and not values:
        why = ("The sheet prints no overall dimension, and this page has no walls or room outlines in a known scale "
               "to measure the plan's extent from.")
        built = A.abstain(question, lang, scope, "not_answerable", "What is the overall size of the plan", why,
                          intent_tag="router · length")
        built.body["explanation"] = why
        return built
    text = ". ".join(parts) + "."
    boxes = [b for b in (_box(x) for x in used) if b is not None]
    if any("x" not in x for x in used):
        text += " (Printed on another page of the file than the one shown.)"
    if values and not printed_any and isinstance(ext.get("box"), dict):
        eb = ext["box"]  # what was measured: the extent of the plan, drawn as one box
        boxes.append(Box(id="extent", x=eb["x"], y=eb["y"], w=eb["w"], h=eb["h"], subject="label",
                         note=f"{_m(ext['width_m'])} × {_m(ext['depth_m'])} measured"))
    method = _weakest(used) if used else (d.drawing.get("evidence_method") or {}).get("room") or "cad_geometry"
    value = values[0] if len(values) == 1 and not lower_bound else None
    tr = _result(d, {"ask": "overall", "axis": axis}, "quantity" if value is not None else "note", value, text, boxes, [],
                 method)
    what = {"width": "overall width", "length": "overall depth", "size": "overall size"}[axis]
    return _body(question, lang, d, scope, tr, f"What is the {what} of the plan", value=value, text=text,
                 printed=printed_any or lower_bound, estimate=(est or scan) and not printed_any, measures=measures)


RULE = "Width is the side across the page, depth the side up the page"


def _across_up(r: dict, d: LoadedDrawing) -> tuple[float, float] | None:
    """(across the page, up the page) in metres of a room whose outline lies square to the page, else None: the
    sides of a turned room have no page direction, so there the shorter side stays the width (and is said so)."""
    s = r.get("size") or {}
    mx, my = d.drawing.get("m_per_pct_x") or 0, d.drawing.get("m_per_pct_y") or 0
    pts = [p for p in r.get("points") or [] if len(p) >= 2]
    if s.get("source") == "printed" or len(pts) < 3 or not (mx > 0 and my > 0):
        return None
    w = (max(p[0] for p in pts) - min(p[0] for p in pts)) * mx
    h = (max(p[1] for p in pts) - min(p[1] for p in pts)) * my
    lo, hi = sorted((w, h))
    if lo <= 0 or abs(hi - s["length_m"]) > 0.06 * hi or abs(lo - s["width_m"]) > 0.06 * lo + 0.05:
        return None
    return w, h


def _median(vals: list[float]) -> float | None:
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else None


def near_dimensions(d: LoadedDrawing, r: dict) -> dict | None:
    """The dimension lines printed beside a room that has a label but no outline: {"width": dim, "depth": dim,
    "agrees": bool}.

    The page may have no scale, so the neighbourhood is sized by the dimension lines themselves (page percent per
    metre) and by the room's printed area (its sides are around the root of it). A horizontal line counts for the
    width when it passes the label's x and lies within the room's reach above or below it; likewise a vertical one
    for the depth. With a printed area, a pair whose product agrees with it wins; a single line must be a plausible
    side of that area (aspect up to 4:1), which keeps wall thicknesses and door offsets out."""
    at, pts = r.get("label_at"), r.get("points") or []
    if isinstance(r.get("size"), dict) or not at or not pts:      # no points: the label is not on the page shown
        return None
    lx, ly = float(at[0]), float(at[1])
    lines = []
    for x in d.drawing.get("dimensions") or []:
        ln, v = x.get("line"), x.get("value_m")
        if (x.get("kind") != "linear" or x.get("orientation") not in ("horizontal", "vertical") or not ln
                or len(ln) < 2 or not isinstance(v, (int, float)) or v <= 0):
            continue
        lines.append(x)
    per_m = {o: _median([math.dist(x["line"][0], x["line"][-1]) / x["value_m"] for x in lines if x["orientation"] == o])
             for o in ("horizontal", "vertical")}
    sx, sy = per_m["horizontal"] or per_m["vertical"], per_m["vertical"] or per_m["horizontal"]
    if not sx or not sy:
        return None
    area = r.get("area_m2") if isinstance(r.get("area_m2"), (int, float)) and r["area_m2"] > 0 else None
    side = math.sqrt(area) if area else None
    reach = 0.75 * side + 1.0 if side else 4.0                      # metres from the label to a line of this room
    lo, hi = (side / 2, side * 2) if side else (0.9, 30.0)
    cands: dict[str, list[tuple[float, dict]]] = {"horizontal": [], "vertical": []}
    for x in lines:
        (x0, y0), (x1, y1) = x["line"][0][:2], x["line"][-1][:2]
        if not lo <= x["value_m"] <= hi:
            continue
        if x["orientation"] == "horizontal":
            a, b = sorted((x0, x1))
            off = abs((y0 + y1) / 2 - ly) / sy
            inside = a - 0.5 * sx <= lx <= b + 0.5 * sx
        else:
            a, b = sorted((y0, y1))
            off = abs((x0 + x1) / 2 - lx) / sx
            inside = a - 0.5 * sy <= ly <= b + 0.5 * sy
        if inside and off <= reach:
            cands[x["orientation"]].append((off, x))
    hs, vs = sorted(cands["horizontal"], key=lambda c: c[0]), sorted(cands["vertical"], key=lambda c: c[0])
    if area:
        pairs = [(abs(h["value_m"] * v["value_m"] - area) / area, h, v) for _o, h in hs for _p, v in vs]
        pairs = [p for p in pairs if p[0] <= 0.15]
        if pairs:
            _err, h, v = min(pairs, key=lambda p: p[0])
            return {"width": h, "depth": v, "agrees": True}
    if not hs and not vs:
        return None
    return {"width": hs[0][1] if hs else None, "depth": vs[0][1] if vs else None, "agrees": False}


def _room(question: str, lang: str, d: LoadedDrawing, axis: str, scope: dict, types: set[str] | None = None,
          rank: str | None = None) -> A.Built | None:
    if rank:
        cands = [r for r in d.drawing.get("rooms") or []
                 if isinstance(r.get("area_m2"), (int, float)) and r["area_m2"] > 0 and not r.get("outdoor")]
        rooms = [(max if rank == "max" else min)(cands, key=lambda r: r["area_m2"])] if cands else []
    else:
        rooms = [r for r in d.drawing.get("rooms") or []
                 if (r.get("room_type") in types if types else
                     r["room_id"] == scope.get("room_id") if scope["kind"] == "room" else r.get("name") == scope.get("label"))]
    if (types or rank) and len(rooms) == 1:
        scope = {"kind": "room", "room_id": rooms[0]["room_id"]}
    if not rooms:
        return None
    near = {r["room_id"]: n for r in rooms if not isinstance(r.get("size"), dict) and (n := near_dimensions(d, r))}
    sized = [r for r in rooms if isinstance(r.get("size"), dict) or r["room_id"] in near]
    if not sized:
        # a length question never falls through to a count of the rooms it names
        on_page = any(r.get("points") for r in rooms)
        why = ("The sheet prints no size in this room, and the room has no outline on this page to measure "
               "(only its label was read)" +
               ("; no dimension line printed beside the label is a plausible side of the room." if on_page else
                "; its label is on another page of the file than the one shown, so the dimension lines beside it "
                "cannot be matched to it."))
        built = A.abstain(question, lang, scope, "not_answerable", f"What is the size of the room {A.where_en(scope)}",
                          why, intent_tag="router · length")
        built.body["explanation"] = why
        return built
    by_room: dict[str, list[dict]] = {}
    for x in d.drawing.get("dimensions") or []:
        if x.get("kind") == "room_size" and x.get("room_id") and "x" in x:
            by_room.setdefault(x["room_id"], []).append(x)
    lines, boxes, ev_rooms, used, measures = [], [], [], [], []
    scan = (d.drawing.get("evidence_method") or {}).get("room") in ("vision", "ocr")
    value, printed_all, est_any, turned = None, True, False, False
    many = len(sized) > 1
    for r in sized:
        name = f"{r.get('name') or 'Room'}{' ' + r['num'] if r.get('num') and (many or rank) else ''}"
        if rank and isinstance(r.get("area_m2"), (int, float)):
            name = f"The {'largest' if rank == 'max' else 'smallest'} room by area, {name} ({r['area_m2']:.2f} m²)"
        pre = f"{name} " if many else ""
        ev_rooms.append(EvidenceRoom(room_id=r["room_id"], included=True, area_m2=r.get("area_m2")))
        if r["room_id"] in near:
            # no outline, no printed size: the dimension lines printed beside the room's label, quoted as printed
            n = near[r["room_id"]]
            w, dp = n["width"], n["depth"]
            mine = [x for x in (w, dp) if x is not None]
            bits = []
            if w is not None and axis in ("width", "size"):
                bits.append(f"{_printed(w.get('text'), w['value_m'], w.get('unit'))} wide (across the page)")
                measures.append(_measure(f"{pre}width", w["value_m"], w))
                value = w["value_m"] if axis == "width" else value
            if dp is not None and axis in ("length", "size"):
                bits.append(f"{_printed(dp.get('text'), dp['value_m'], dp.get('unit'))} deep (up the page)")
                measures.append(_measure(f"{pre}depth", dp["value_m"], dp))
                value = dp["value_m"] if axis == "length" else value
            if axis == "size" and (w is None or dp is None):
                bits.append(f"no dimension line for its {'width' if w is None else 'depth'} is printed beside the label")
            if not bits:      # asked for one direction, only the other has a line: say what is there
                other = w if w is not None else dp
                word = "width (across the page)" if w is not None else "depth (up the page)"
                bits.append(f"no dimension line for its {'depth' if w is not None else 'width'} is printed beside the "
                            f"label; its {word} is {_printed(other.get('text'), other['value_m'], other.get('unit'))}")
                measures.append(_measure(f"{pre}{'width' if w is not None else 'depth'}", other["value_m"], other))
            how = "read from the dimension lines printed beside the room's label"
            if n["agrees"]:
                how += (f"; their product, {w['value_m'] * dp['value_m']:.2f} m², agrees with the room's printed area "
                        f"({r['area_m2']:.2f} m²)")
            else:
                how += "; the room has no outline on this page, so a line may cover more or less than the room itself"
            lines.append(f"{name}: {(' × ' if w is not None and dp is not None else '; ').join(bits)} ({how})")
            used += mine
            boxes += [b for b in (_box(x) for x in mine) if b is not None]
            continue
        s = r["size"]
        length, width, src = float(s["length_m"]), float(s["width_m"]), s.get("source")
        printed = src == "printed"
        printed_all &= printed
        est_any |= src == "estimate"
        how = (f"as printed ({s['text']})" if printed and s.get("text") else "as printed" if printed else
               "an estimate from the room outline: the sheet states no scale" if src == "estimate" else
               "measured on the outline traced on the scan, not printed: an estimate" if scan else
               "measured on the room outline, not printed")
        approx = "≈ " if src == "estimate" else ""
        if printed and s.get("text") and ("'" in s["text"] or '"' in s["text"]):
            how = f"{s['text']} as printed"
        msrc = "printed" if printed else "estimate" if src == "estimate" else "measured"
        au = _across_up(r, d)
        if au:      # the outline lies square to the page: width across, depth up
            w_m, d_m = au
            if axis == "width":
                lines.append(f"{name}: {approx}{_m(w_m)} wide (across the page; {how})")
                value = w_m
            elif axis == "length":
                lines.append(f"{name}: {approx}{_m(d_m)} deep (up the page; {how})")
                value = d_m
            else:
                lines.append(f"{name}: {approx}{_m(w_m)} wide (across the page) × {_m(d_m)} deep (up the page) ({how})")
            if axis in ("width", "size"):
                measures.append(_measure(f"{pre}width", w_m, source=msrc))
            if axis in ("length", "size"):
                measures.append(_measure(f"{pre}depth", d_m, source=msrc))
        else:       # a printed "a × b" or a turned room: no page direction, the shorter side is the width
            turned = True
            if axis == "width":
                lines.append(f"{name}: {approx}{_m(width)} wide (the shorter side; {how})")
                value = width
            elif axis == "length":
                lines.append(f"{name}: {approx}{_m(length)} long (the longer side; {how})")
                value = length
            else:
                lines.append(f"{name}: {approx}{_m(length)} × {_m(width)} ({how})")
            if axis in ("width", "size"):
                measures.append(_measure(f"{pre}width", width, source=msrc))
            if axis in ("length", "size"):
                measures.append(_measure(f"{pre}length", length, source=msrc))
        mine = by_room.get(r["room_id"]) or []
        used += mine
        boxes += [b for b in (_box(x) for x in mine) if b is not None]
    text = "; ".join(lines) + ". " + RULE
    text += ("; where a room's size is printed without a direction or the room is turned on the page, its shorter "
             "side is given as the width." if turned else ".")
    if len(sized) != 1:
        value = None
    em = (d.drawing.get("evidence_method") or {}).get("room")
    method = _weakest(used) if used and printed_all else em or "cad_geometry"
    tr = _result(d, {"ask": "room", "axis": axis, "scope": scope}, "quantity" if value is not None else "note", value,
                 text, boxes, ev_rooms, method)
    what = {"width": "width", "length": "length", "size": "size"}[axis]
    return _body(question, lang, d, scope, tr, f"What is the {what} of the room {A.where_en(scope)}", value=value, text=text,
                 printed=printed_all, estimate=est_any, measures=measures)


def _extreme(question: str, lang: str, d: LoadedDrawing, which: str) -> A.Built | None:
    dims = _usable(d)
    word = "longest" if which == "max" else "shortest"
    if not dims:
        # never a row count for a length: say that no dimension line is stored, and what the plan measures instead
        scope = {"kind": "floor"}
        areas = sum(1 for x in d.drawing.get("dimensions") or [] if x.get("kind") == "area")
        ext = d.drawing.get("extent_m") or {}
        why = "No dimension line with a length is printed on this sheet (none was read)"
        if areas:
            why += f"; its {areas} printed values are room areas"
        why += "."
        if ext.get("width_m") and ext.get("depth_m"):
            why += (f" The plan itself measures {'≈ ' if ext.get('estimate') else ''}{_m(ext['width_m'])} × "
                    f"{_m(ext['depth_m'])} ({'an estimate: the sheet states no scale' if ext.get('estimate') else 'measured on the outline, not printed'}).")
        built = A.abstain(question, lang, scope, "not_answerable", f"What is the {word} dimension printed on the sheet",
                          why, intent_tag="router · length")
        built.body["explanation"] = why
        return built
    x = (max if which == "max" else min)(dims, key=lambda x: x["value_m"])
    text = (f"The {word} dimension printed on the sheet: {_printed(x.get('text'), x['value_m'], x.get('unit'))} "
            f"({x.get('orientation') or x.get('kind')}; of {len(dims)} printed dimensions).")
    b = _box(x)
    if b is None:
        text += " (Printed on another page of the file than the one shown.)"
    tr = _result(d, {"ask": "extreme", "which": which}, "quantity", float(x["value_m"]), text, [b] if b else [], [],
                 _weakest([x]))
    return _body(question, lang, d, {"kind": "floor"}, tr, f"What is the {word} dimension printed on the sheet",
                 value=float(x["value_m"]), text=text, printed=True, estimate=False)


def _rect_sides(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    """(longer, shorter) side in metres of the smallest rotated rectangle around a room outline."""
    try:
        import shapely
        rect = shapely.Polygon(pts).minimum_rotated_rectangle
        c = list(rect.exterior.coords)
        a, b = math.dist(c[0], c[1]), math.dist(c[1], c[2])
    except Exception:  # noqa: BLE001  a degenerate outline: no size
        return None
    return (max(a, b), min(a, b)) if a > 0 and b > 0 else None


def with_measured(d: LoadedDrawing) -> LoadedDrawing:
    """The drawing with its measurable sizes filled in when its document does not carry them.

    A drawing built from a CAD / model file outside the drawing store (the bundled library) has room outlines in
    page percent and metres per percent, but no `extent_m` and no `rooms[].size`. Both follow from the outlines, so
    a length question gets the same answer as on a store drawing: measured on the outline, not printed."""
    doc = d.drawing
    mx, my = doc.get("m_per_pct_x") or 0, doc.get("m_per_pct_y") or 0
    rooms = doc.get("rooms") or []
    if (doc.get("extent_m") or doc.get("kind") not in ("cad_dxf", "ifc") or doc.get("evidence_method")
            or not (mx > 0 and my > 0) or any(isinstance(r.get("size"), dict) for r in rooms)):
        return d
    out_rooms, xs, ys = [], [], []
    for r in rooms:
        pts = [(float(p[0]), float(p[1])) for p in r.get("points") or [] if len(p) >= 2]
        row = r
        if len(pts) >= 3:
            sides = _rect_sides([(x * mx, y * my) for x, y in pts])
            if sides:
                row = dict(r) | {"size": {"length_m": round(sides[0], 2), "width_m": round(sides[1], 2),
                                          "source": "measured"}}
            if not r.get("outdoor"):
                xs += [p[0] for p in pts]
                ys += [p[1] for p in pts]
        out_rooms.append(row)
    new = dict(doc) | {"rooms": out_rooms}
    if xs and max(xs) > min(xs) and max(ys) > min(ys):
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        new["extent_m"] = {"width_m": round((x1 - x0) * mx, 2), "depth_m": round((y1 - y0) * my, 2), "source": "rooms",
                           "estimate": False, "box": {"x": round(x0, 2), "y": round(y0, 2), "w": round(x1 - x0, 2),
                                                      "h": round(y1 - y0, 2)}}
    return dataclasses.replace(d, drawing=new)


def try_answer(question: str, lang: str, d: LoadedDrawing) -> A.Built | None:
    """A built answer for a plain length question the drawing's own data answers, else None (the usual path runs)."""
    c = classify(question, d)
    if c is None:
        return None
    d = with_measured(d)
    doc = d.drawing
    if c["ask"] != "extreme" and not (doc.get("dimensions") or doc.get("extent_m")
                                      or any(isinstance(r.get("size"), dict) for r in doc.get("rooms") or [])):
        return None
    if c["ask"] == "overall":
        return _overall(question, lang, d, c["axis"])
    if c["ask"] == "room":
        return _room(question, lang, d, c["axis"], c["scope"], c.get("types"), c.get("rank"))
    return _extreme(question, lang, d, c["which"])
