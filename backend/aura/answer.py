"""Builds AskResponse bodies. The ONLY place `answer` values are set, always from a ToolResult.

Trace layout follows frontend local.ts: q → intent, source → read/collect/scope/whatif,
check, answer. Gold is looked up by scope and family in the drawing's gold table and
reported with the dataset line for the question's language.
"""

from __future__ import annotations

import re
from typing import Any

from aura.contract import CONTRACT_VERSION, ToolResult
from aura.router import TOPIC_EN, WHERE_TO_FIND, contract_lang
from aura.tools import LoadedDrawing, noun, scope_text, where_en

LANG_NAME = {"en": "English", "fr": "French", "fi": "Finnish", "de": "German", "it": "Italian"}


def room_eid(room_id: str) -> str:
    return f"room-{room_id}"


def same_scope(a: dict, b: dict) -> bool:
    if a.get("kind", "floor") != b.get("kind", "floor"):
        return False
    k = a.get("kind", "floor")
    if k in ("floor", "model"):
        return True
    if k == "region":
        return False
    if k == "project":
        return (
            a.get("project_id") == b.get("project_id")
            and a.get("drawing_ids") == b.get("drawing_ids")
        )
    key = {"unit": "unit", "room": "room_id", "label": "label"}[k]
    return a.get(key) == b.get(key)


def gold_for(d: LoadedDrawing, lang: str, kind: str, subject: str | None, scope: dict,
             value: float) -> dict | None:
    for g in d.gold:
        ga = g.get("answer") or {}
        if ga.get("kind") != kind:
            continue
        if kind in ("count", "quantity") and ga.get("subject", ga.get("family")) != subject:
            continue
        if not same_scope(g.get("scope") or {"kind": "floor"}, scope):
            continue
        gv = float(g["value"])
        tol = g.get("tolerance", "exact" if kind == "count" else "2pct")
        match = value == gv if tol == "exact" else abs(value - gv) <= 0.02 * abs(gv)
        qid = g.get(f"dataset_qid_{lang}") if lang != "en" else None
        return {"dataset_qid": qid or g["dataset_qid"], "value": g["value"], "tolerance": tol,
                "match": bool(match)}
    return None


def question_node(question: str, lang: str) -> dict:
    return {"id": "q", "title": "Question", "tag": f"{LANG_NAME.get(lang, 'English')} · detected",
            "body": f"“{question}”", "kind": "info"}


def intent_node(body: str, tag: str = "router", confidence: float | None = None) -> dict:
    n: dict[str, Any] = {"id": "intent", "parent": "q", "title": "Intent", "tag": tag,
                         "body": body, "kind": "info"}
    if confidence is not None:
        n["confidence"] = round(confidence, 3)
    return n


PICK_SOURCE = {
    "id": "source", "parent": "q", "title": "Pick the best source", "tag": "router",
    "body": "The CAD geometry of this drawing answers counts and areas exactly, so no image model "
            "is needed for the answer.",
    "kind": "exact",
}


_METHOD_RANK = {"cad_geometry": 0, "pdf_vector": 1, "ocr": 2, "vision": 3}
_HONEST = {  # method -> (answer tag, node kind, where the numbers come from)
    "vision": ("estimate · measured on the image", "model",
               "outlines found on the page image by a model, so this is an estimate"),
    "ocr": ("estimate · read by OCR", "model",
            "values read by OCR from the page image, so this is an estimate"),
    "pdf_vector": ("PDF vectors", "exact", "the PDF's vector lines and printed text"),
}


def honest_method(body: dict, d: LoadedDrawing, subject: str | None = None) -> None:
    """The last word on `method` for a drawing the store indexed (`evidence_method` per subject).

    A solver may call its result exact CAD geometry because it only read the drawing index; whether
    that index was drawn (CAD / IFC) or found on pixels is a fact of the drawing. Whatever the
    answer used (its subject, its rooms, its boxes) decides, the weakest source winning, and an
    answer never stays `cad_geometry` over geometry that came from a scan, OCR or a detection."""
    em: dict = d.drawing.get("evidence_method") or {}
    if em and body.get("method") in ("vision", "ocr"):
        # already an estimate: its answer node must not read as exact either
        for n in body.get("trace") or []:
            if n.get("id") == "answer" and n.get("kind") == "exact":
                n["kind"] = "model"
                if "estimate" not in str(n.get("tag", "")):
                    n["tag"] = f"estimate · {n.get('tag') or 'answer'}"
        return
    if not em or body.get("method") != "cad_geometry":
        return
    ev = body.get("evidence") or {}
    used = {"room" if subject in ("room", "area", "apartment") else subject}
    if ev.get("rooms"):
        used.add("room")
    used |= {b.get("subject") for b in ev.get("boxes") or [] if isinstance(b, dict)}
    methods = [em[u] for u in used if u in em] or list(em.values())
    method = max(methods, key=lambda m: _METHOD_RANK.get(m, 3))
    if _METHOD_RANK.get(method, 3) == 0:
        return
    tag, kind, source = _HONEST.get(method, _HONEST["vision"])
    body["method"] = method if method in _HONEST else "vision"
    for n in body.get("trace") or []:
        exact_claim = "CAD" in str(n.get("tag", "")) or str(n.get("tag", "")).startswith("exact")
        if not (exact_claim or (n.get("id") == "answer" and n.get("kind") == "exact")):
            continue
        if n.get("id") == "answer":
            n["tag"] = tag
            n["body"] = (str(n.get("body", "")).split(" Source: indexed CAD")[0].rstrip()
                         + f" Source: {source}.")
        else:
            n["tag"] = str(n["tag"]).replace("exact · ", "").replace("CAD", "drawing index")
        n["kind"] = kind
        if isinstance(n.get("detail"), dict) and n["detail"].get("source") == "cad_geometry":
            n["detail"]["source"] = body["method"]


def pick_source(method: str | None, d: LoadedDrawing) -> dict:
    """The 'Pick the best source' node, worded after the evidence the answer really used."""
    store = bool(d.drawing.get("evidence_method"))
    if method == "vision":
        body = ("The drawing store indexed this upload; these items were detected on the page image by "
                "a model, so the count is an estimate." if store else
                "This drawing is an image with no CAD geometry, so Gemini found the boxes on the image "
                "once at upload. The count is a model estimate.")
        return {**PICK_SOURCE, "body": body, "kind": "model"}
    if method == "ocr":
        return {**PICK_SOURCE, "kind": "model",
                "body": "These values were read by OCR from the page image, so the answer is an estimate."}
    if method == "pdf_vector":
        return {**PICK_SOURCE, "body": "The rooms of this PDF were read from its vector lines and printed "
                                      "text, so counts and areas come from that index without an image model."}
    if d.drawing.get("kind") == "ifc":
        return {**PICK_SOURCE, "body": "The IFC model's elements and stored quantities answer counts and "
                                      "areas exactly, so no image model is needed for the answer."}
    return dict(PICK_SOURCE)


def tool_meta(tr: ToolResult) -> dict:
    return {"name": tr.tool, "args": tr.args, "result_id": tr.result_id, "ms": tr.ms}


class Built:
    """A response body under construction plus the ToolResult(s) that justify it."""

    def __init__(self, body: dict, results: list[ToolResult], check_ids: list[str],
                 subject: str | None):
        self.body = body
        self.results = results
        self.check_ids = check_ids  # evidence ids the image check may cover
        self.subject = subject


def _base(question: str, lang: str, scope: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "detected_lang": contract_lang(lang),
        "scope": scope,
        "calls": 0,
        "cost_usd": 0.0,
        "source": "api",
    }


def area_basis(d: Any, room_ids: list[str], method: str | None) -> dict:
    """{"basis": ...} for an area built from these rooms, when their drawing says where each area comes from
    (`area_source`): all printed on the sheet -> printed (whoever read it); any measured under an estimated scale,
    or measured on outlines a model found on a scan -> estimate; measured on exact geometry -> measured.
    Rooms without `area_source` (the bundled library) leave the answer as it was."""
    src = {r.get("room_id"): r.get("area_source") for r in d.drawing.get("rooms", [])}
    got = [src.get(i) for i in room_ids]
    if not got or any(g is None for g in got):
        return {}
    if all(g == "printed" for g in got):
        return {"basis": "printed"}
    if "estimate" in got or method in ("vision", "ocr", "detector", "agent_estimate"):
        return {"basis": "estimate"}
    return {"basis": "measured"}


def abstain(question: str, lang: str, scope: dict, reason: str, canonical: str, look: str,
            where_to_find: str | None = None, intent_body: str | None = None,
            intent_tag: str = "router", results: list[ToolResult] | None = None) -> Built:
    ans: dict[str, Any] = {"kind": "abstain", "reason": reason}
    if where_to_find:
        ans["where_to_find"] = where_to_find
    body = _base(question, lang, scope) | {
        "answer": ans,
        "evidence": {"boxes": [], "rooms": []},
        "canonical_en": canonical,
        "method": "none",
        "trace": [
            question_node(question, lang),
            intent_node(intent_body or f"abstain · {reason.replace('_', ' ')}", intent_tag),
            {"id": "look", "parent": "q", "title": "Look for the data", "tag": "drawing",
             "body": look, "kind": "warn"},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "abstain",
             "body": "Abstained. Nothing guessed.", "kind": "warn"},
        ],
    }
    return Built(body, results or [], [], None)


def abstain_topic(question: str, lang: str, topic: str, intent_tag: str = "router",
                  results: list[ToolResult] | None = None, look: str | None = None) -> Built:
    canonical, default_look = TOPIC_EN[topic]
    reason = "not_supported_yet" if topic == "length" else "not_on_drawing"
    where = WHERE_TO_FIND.get(topic, {}).get(contract_lang(lang))
    return abstain(question, lang, {"kind": "floor"}, reason, canonical, look or default_look,
                   where, intent_tag=intent_tag, results=results)


def unclear(question: str, lang: str, d: LoadedDrawing, scope: dict | None = None,
            intent_tag: str = "router", look: str | None = None) -> Built:
    if look is None:
        if scope and scope.get("kind") in ("unit", "room", "label"):
            rooms = d.drawing.get("rooms", [])
            units = [u["id"] for u in d.drawing.get("units", [])]
            look = (f"This drawing has no {scope_text(scope)}; it has rooms "
                    f"{rooms[0]['num'] if rooms else '?'}–{rooms[-1]['num'] if rooms else '?'}"
                    f" in apartments {' and '.join(units) or '(none)'}.")
        else:
            look = ("The question matched nothing this drawing answers: rooms, areas, doors, "
                    "windows or fixtures.")
    return abstain(question, lang, scope or {"kind": "floor"}, "unclear_question", "(no match)",
                   look, intent_tag=intent_tag)


def check_placeholder(ids: list[str]) -> dict:
    return {"id": "check", "parent": "q", "title": "Cross-check on the image",
            "tag": "Decisions API · not run",
            "body": "No model has checked this answer.", "kind": "model",
            "confidence": "sample", "evidence_ids": ids}


def count(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, scope: dict,
          overrides: list | None, intent_tag: str = "router",
          intent_conf: float | None = None) -> Built:
    subject = tr.subject or "room"
    if tr.value is None:  # never show a missing value as 0
        return unclear(question, lang, d, intent_tag=intent_tag,
                       look="The tool returned no value for this count.")
    value = int(tr.value)
    unlabelled_note: str | None = None
    plural = noun(subject, 2)
    gold = None if overrides or scope.get("kind") == "region" or tr.method == "vision" else (
        gold_for(d, lang, "count", subject, scope, value))
    trace: list[dict] = [question_node(question, lang),
                         intent_node(f"count · {plural} · {scope_text(scope)}", intent_tag,
                                     intent_conf),
                         pick_source(tr.method, d)]
    vision = tr.method == "vision"
    if tr.boxes or subject not in ("room", "apartment"):
        ids = [b.id for b in tr.boxes]
        trace.append({"id": "read", "parent": "source",
                      "title": "Boxes found on the image" if vision else "Read the layer",
                      "tag": "Gemini detection · estimate" if vision else f"DXF · {subject} layer",
                      "body": tr.summary, "kind": "model" if vision else "exact",
                      "evidence_ids": ids, "tool": tool_meta(tr)})
        if scope.get("kind", "floor") != "floor":
            trace.append({"id": "scope", "parent": "source", "title": "Apply the scope",
                          "tag": "geometry",
                          "body": (f"Kept the ones whose centre is inside {scope.get('label')} → "
                                   f"{value}." if scope.get("kind") == "region" else
                                   f"Kept the ones {where_en(scope)} → {value}."),
                          "kind": "exact", "evidence_ids": ids})
        answer_ids = ids
        check_ids = ids
        answer_body = (f"About {value} {noun(subject, value)}. Model estimate from {value} boxes "
                       "found, check the boxes." if vision else
                       f"{value} {noun(subject, value)}. Each one is drawn on the plan for you to "
                       "check.")
        if value == 0 and tr.meta.get("none_in_model"):
            answer_body = tr.summary  # a model file without elements of this class: a real zero, said as such
        canonical = f"How many {plural} are drawn {where_en(scope)}"
    else:
        all_ids = [room_eid(r.room_id) for r in tr.rooms]
        answer_ids = [room_eid(r.room_id) for r in tr.rooms if r.included]
        trace.append({"id": "collect", "parent": "source", "title": "Collect rooms",
                      "tag": "room labels", "body": tr.summary, "kind": "exact",
                      "evidence_ids": all_ids, "tool": tool_meta(tr)})
        check_ids = []
        answer_body = f"{value} {noun(subject, value)}. Each room is lit on the plan for you to check."
        bare = {r.get("room_id") for r in d.drawing.get("rooms", []) if r.get("unlabelled")}
        n_bare = sum(1 for r in tr.rooms if r.included and r.room_id in bare and not getattr(r, "drawing_id", None))
        if subject == "room" and n_bare and scope.get("kind", "floor") == "floor":
            # spaces the sheet prints no label for are rooms too: the count says how many of each
            answer_body = (f"{value} {noun(subject, value)} ({value - n_bare} labelled, {n_bare} without a label). "
                           "Each room is lit on the plan for you to check.")
            # the sentence people read, in the language they asked in (the trace stays English)
            said = UNLABELLED_NOTE.get(said_lang(lang)) if value > 1 else None
            unlabelled_note = said.format(n=value, a=value - n_bare, b=n_bare) if said else answer_body
        if subject == "apartment":
            canonical = ("How many apartments are on this floor plan" if scope.get("kind") == "floor"
                         else f"How many apartments are {where_en(scope)}")
        elif scope.get("kind") == "label":
            canonical = f"How many rooms are labelled {scope['label']}"
        else:
            canonical = f"How many numbered rooms are {where_en(scope)}"
    trace.append(check_placeholder(answer_ids))
    trace.append({"id": "answer", "parent": "q", "title": "Answer",
                  "tag": "estimate" if vision else "count", "body": answer_body,
                  "kind": "model" if vision else "exact", "evidence_ids": answer_ids})
    evidence: dict = {"boxes": [b.model_dump(exclude_none=True) for b in tr.boxes],
                      "rooms": [r.model_dump(exclude_none=True) for r in tr.rooms]}
    if tr.meta.get("rows") and (not tr.boxes or tr.meta.get("rows_beside_boxes")):
        evidence["rows"] = tr.meta["rows"]  # IFC elements without a 2D footprint
    if tr.groups:
        evidence["groups"] = [g.model_dump(exclude_none=True) for g in tr.groups]
    if tr.links:
        evidence["links"] = [lk.model_dump(exclude_none=True) for lk in tr.links]
    body = _base(question, lang, scope) | {
        "answer": {"kind": "count", "value": value, "subject": subject},
        "evidence": evidence,
        "canonical_en": canonical,
        "method": tr.method,
        "trace": trace,
    }
    if gold:
        body["gold"] = gold
    if value == 0 and tr.meta.get("none_in_model"):
        body["explanation"] = none_in_model_said(lang, subject, tr.summary)
    if unlabelled_note:
        body["explanation"] = unlabelled_note
    if subject == "room" and scope.get("kind", "floor") == "floor" and not overrides:
        # the header counts every space of the sheet; the answer leaves the outdoor ones out, and says so
        outdoor = {r.get("room_id") for r in d.drawing.get("rooms", []) if r.get("outdoor")}
        n_out = sum(1 for r in tr.rooms if not r.included and r.room_id in outdoor
                    and not getattr(r, "drawing_id", None))
        if n_out:
            one, many = OUTDOOR_NOTE.get(said_lang(lang), OUTDOOR_NOTE["en"])
            note = (one if n_out == 1 else many).format(n=n_out)
            body["explanation"] = f"{body['explanation']} {note}" if body.get("explanation") else note
    return Built(body, [tr], check_ids, subject)


def area(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, scope: dict,
         overrides: list | None, intent_tag: str = "router",
         intent_conf: float | None = None) -> Built:
    if tr.value is None:  # never show a missing value as 0
        return unclear(question, lang, d, intent_tag=intent_tag,
                       look="The tool returned no value for this area.")
    value = round(float(tr.value), 2)
    rooms = tr.rooms
    included = [r for r in rooms if r.included]
    out = len(rooms) - len(included)
    ids = [room_eid(r.room_id) for r in rooms]
    inc_ids = [room_eid(r.room_id) for r in included]
    used = [o for o in overrides or [] if any(r.room_id == o["room_id"] for r in rooms)]
    k = scope.get("kind", "floor")
    # areas measured under an estimated scale (a scan that states none) make the answer approximate
    estimates = {r.get("room_id") for r in d.drawing.get("rooms", []) if r.get("area_source") == "estimate"}
    approx = (any(r.room_id in estimates and not getattr(r, "edited", False) for r in included)
              or bool(tr.meta.get("approximate")))
    floors = tr.meta.get("floor_sums") or []   # an all-floors sum: one line per floor
    canonical = ("What is the net floor area of all numbered indoor rooms" if k == "floor" else
                 f"What is the total area of rooms labelled {scope['label']}" if k == "label" else
                 f"What is the net floor area inside {scope.get('label')}" if k == "region" else
                 f"What is the net floor area of {scope_text(scope)}")
    if used:
        canonical += ", with " + " and ".join(
            f"room {o['room_id']} at {o['w_m']} × {o['h_m']} m" for o in used)
    trace: list[dict] = [
        question_node(question, lang),
        intent_node(f"area · net · {scope_text(scope)}" + (" · without outdoor" if out else "")
                    + "".join(f" · what-if {o['room_id']} = {round(o['w_m'] * o['h_m'], 2):.2f} m²"
                              for o in used), intent_tag, intent_conf),
        pick_source(tr.method, d),
        {"id": "collect", "parent": "source", "title": "Collect rooms",
         "tag": "room labels · printed areas", "body": tr.summary, "kind": "exact",
         "evidence_ids": ids, "tool": tool_meta(tr)},
    ]
    edited = [r for r in rooms if r.edited]
    if edited:
        trace.append({"id": "whatif", "parent": "source", "title": "Apply your what-if size",
                      "tag": "what-if",
                      "body": " ".join(
                          f"Room {o['room_id']} uses your what-if size {o['w_m']} × {o['h_m']} m = "
                          f"{round(o['w_m'] * o['h_m'], 2):.2f} m²." for o in used),
                      "kind": "info", "evidence_ids": [room_eid(r.room_id) for r in edited]})
    trace.append(check_placeholder(inc_ids))
    trace.append({"id": "answer", "parent": "q", "title": "Answer", "tag": "sum",
                  "body": (f"{value:.2f} m², shown on the plan." if len(included) == 1 else
                           f"Sum of {len(included)} net room areas on {len(floors)} floors = {value:.2f} m² "
                           f"({'; '.join(floors)})." if len(floors) > 1 else
                           f"Sum of {len(included)} net room areas = {value:.2f} m², shown room by room."),
                  "kind": "exact", "evidence_ids": inc_ids})
    gold = None if overrides or k == "region" else gold_for(d, lang, "area", None, scope, value)
    ev_dict: dict = {"boxes": [], "rooms": [r.model_dump(exclude_none=True) for r in rooms]}
    if tr.groups:
        ev_dict["groups"] = [g.model_dump(exclude_none=True) for g in tr.groups]
    if tr.links:
        ev_dict["links"] = [lk.model_dump(exclude_none=True) for lk in tr.links]
    body = _base(question, lang, scope) | {
        "answer": {"kind": "area", "value": value, "unit": "m2"} | ({"approximate": True} if approx else {}),
        "evidence": ev_dict,
        "canonical_en": canonical,
        "method": tr.method,
        "trace": trace,
        **area_basis(d, [r.room_id for r in included if not getattr(r, "edited", False)] if not used else [], tr.method),
    }
    if gold:
        body["gold"] = gold
    return Built(body, [tr], [], "area")


QUANTITY_EN = {"wall_length": "total wall length", "wall_side_area": "net wall side area",
               "wall_volume": "net wall volume", "slab_volume": "net slab volume"}


def quantity(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, scope: dict,
             intent_tag: str = "router") -> Built:
    """An IFC or agent-measured quantity, given with its unit and evidence."""
    what = QUANTITY_EN.get(tr.subject or "", tr.subject or "quantity")
    unit = tr.meta.get("unit", "m")
    rows = tr.meta.get("rows", [])
    ev_dict: dict[str, Any] = {
        "boxes": [b.model_dump(exclude_none=True) for b in tr.boxes],
        "rooms": [r.model_dump(exclude_none=True) for r in tr.rooms],
    }
    if rows:
        ev_dict["rows"] = rows
    if tr.groups:
        ev_dict["groups"] = [g.model_dump(exclude_none=True) for g in tr.groups]
    if tr.links:
        ev_dict["links"] = [lk.model_dump(exclude_none=True) for lk in tr.links]
    is_agent = tr.method == "agent_estimate"
    ids = [b.id for b in tr.boxes]
    body = _base(question, lang, scope) | {
        "answer": {"kind": "quantity", "value": float(tr.value), "unit": unit},
        "evidence": ev_dict,
        "canonical_en": f"What is the {what} {where_en(scope)}",
        "method": tr.method,
        "trace": [
            question_node(question, lang),
            intent_node(f"quantity · {what} · {scope_text(scope)}", intent_tag),
            {"id": "read", "parent": "q",
             "title": "Measured on the drawing" if is_agent else "Read the IFC quantities",
             "tag": "Agent · measurement" if is_agent else "IFC · Qto",
             "body": tr.summary, "kind": "model" if is_agent else "exact",
             **({"evidence_ids": ids} if ids else {}), "tool": tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "quantity",
             "body": (f"{tr.value} {unit} (agent measurement)." if is_agent
                      else f"{tr.value} {unit}, as stated in the model's quantity sets."),
             "kind": "model" if is_agent else "exact",
             **({"evidence_ids": ids} if ids else {})},
        ],
    }
    gold = None if is_agent else gold_for(d, lang, "quantity", tr.subject, scope, float(tr.value))
    if gold:
        body["gold"] = gold
    return Built(body, [tr], ids if is_agent else [], tr.subject)


def said_lang(lang: str) -> str:
    """The language an answer sentence is written in: the question's, Italian included."""
    return contract_lang(lang)


#: "10 rooms (8 labelled, 2 without a label)" in the other interface languages
UNLABELLED_NOTE = {
    "de": "{n} Räume ({a} beschriftet, {b} ohne Beschriftung). Jeder Raum ist im Plan markiert, zum Nachprüfen.",
    "fr": "{n} pièces ({a} avec libellé, {b} sans libellé). Chaque pièce est mise en évidence sur le plan pour vérification.",
    "fi": "{n} huonetta ({a} nimettyä, {b} ilman nimeä). Jokainen huone on korostettu pohjapiirroksessa tarkistusta varten.",
    "it": "{n} locali ({a} con etichetta, {b} senza etichetta). Ogni locale è evidenziato sulla pianta per la verifica.",
}

#: "(1 outdoor space not counted)": why a room count is lower than the number of spaces on the sheet
OUTDOOR_NOTE = {
    "en": ("({n} outdoor space not counted)", "({n} outdoor spaces not counted)"),
    "de": ("({n} Außenbereich nicht mitgezählt)", "({n} Außenbereiche nicht mitgezählt)"),
    "fr": ("({n} espace extérieur non compté)", "({n} espaces extérieurs non comptés)"),
    "fi": ("({n} ulkotilaa ei laskettu mukaan)", "({n} ulkotilaa ei laskettu mukaan)"),
    "it": ("({n} spazio esterno non contato)", "({n} spazi esterni non contati)"),
}

#: "0 windows: the model file contains no window elements" in the other interface languages
NONE_IN_MODEL = {
    "de": "0 {things}: {where} enthält keine {element}-Elemente.",
    "fr": "0 {things} : {where} ne contient aucun élément de type {element}.",
    "fi": "0 {things}: {where} ei sisällä {element}-elementtejä.",
    "it": "0 {things}: {where} non contiene elementi di tipo {element}.",
}
_NONE_WORDS = {
    "de": {"door": ("Türen", "Tür"), "window": ("Fenster", "Fenster"), "room": ("Räume", "Raum"),
           "floor": "dieses Geschoss der Modelldatei", "model": "die Modelldatei"},
    "fr": {"door": ("portes", "porte"), "window": ("fenêtres", "fenêtre"), "room": ("pièces", "espace"),
           "floor": "cet étage de la maquette", "model": "la maquette"},
    "fi": {"door": ("ovea", "ovi"), "window": ("ikkunaa", "ikkuna"), "room": ("huonetta", "tila"),
           "floor": "mallitiedoston tämä kerros", "model": "mallitiedosto"},
    "it": {"door": ("porte", "porta"), "window": ("finestre", "finestra"), "room": ("locali", "locale"),
           "floor": "questo piano del modello", "model": "il file del modello"},
}


def none_in_model_said(lang: str, subject: str, summary: str) -> str:
    """The reason of a real zero from a model file, in the language of the question (English otherwise)."""
    code = said_lang(lang)
    words = _NONE_WORDS.get(code, {})
    if code not in NONE_IN_MODEL or subject not in words or "contains no" not in summary:
        return summary
    things, element = words[subject]
    where = words["floor"] if "this storey" in summary or "this floor" in summary else words["model"]
    text = NONE_IN_MODEL[code].format(things=things, where=where, element=element)
    return text[:1].upper() + text[1:] if not text[0].isdigit() else text


#: "X is the largest room: 45.60 m²; next is Y at 45.50 m²" in the other interface languages
RANK_SAID = {
    "de": {"max": "{name} ist der größte Raum: {area}", "min": "{name} ist der kleinste Raum: {area}",
           "next": "; danach folgt {name} mit {area}", "tied": " (gleichauf)", "estimate": "Schätzung", "room": "Raum"},
    "fr": {"max": "{name} est la plus grande pièce : {area}", "min": "{name} est la plus petite pièce : {area}",
           "next": " ; vient ensuite {name} avec {area}", "tied": " (à égalité)", "estimate": "estimation", "room": "Pièce"},
    "fi": {"max": "{name} on suurin huone: {area}", "min": "{name} on pienin huone: {area}",
           "next": "; seuraavana on {name}, {area}", "tied": " (tasan)", "estimate": "arvio", "room": "Huone"},
    "it": {"max": "{name} è il locale più grande: {area}", "min": "{name} è il locale più piccolo: {area}",
           "next": "; segue {name} con {area}", "tied": " (a pari merito)", "estimate": "stima", "room": "Locale"},
}

#: an id the store gives a room or thing that carries no number on the sheet ("e143313"): never shown to people
# a store entity id ("e143313") or an IFC GlobalId (22 characters): never shown beside a room name
INTERNAL_ID = re.compile(r"e\d{3,}|[0-9A-Za-z_$]{22}")


def plain_ids(text: str, drawing: dict) -> str:
    """Prose without the store's internal ids: "Hall (e143313)" -> "Hall", "with ID e143299" -> "",
    "room e143304" -> "room Enclosed Walkway" (the room's name when the drawing has it; else the id stays)."""
    if not text or "e" not in text:
        return text
    names = {str(r.get("room_id")): str(r.get("name") or "").strip() for r in drawing.get("rooms", [])}
    text = re.sub(r"\s*\((?:(?:entity[ _]?)?id[:= ]*)?e\d{3,}\)", "", text, flags=re.I)
    text = re.sub(r",?\s*\b(?:with|having)\s+(?:the\s+)?(?:entity[ _]?)?id[:= ]*e\d{3,}\b", "", text, flags=re.I)
    return re.sub(r"\be\d{3,}\b", lambda m: names.get(m.group(0)) or m.group(0), text)


def note(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, scope: dict,
         intent_tag: str = "router") -> Built:
    """Open-ended answer from the deep agent (`AnswerNote`, contract 1.2)."""
    value = plain_ids(tr.text or tr.summary or "", d.drawing)
    ans: dict[str, Any] = {"kind": "note", "value": value}
    if tr.value is not None:
        ans["number"] = float(tr.value)
    if tr.meta.get("unit"):
        ans["unit"] = str(tr.meta["unit"])
    ev_dict: dict[str, Any] = {
        "boxes": [b.model_dump(exclude_none=True) for b in tr.boxes],
        "rooms": [r.model_dump(exclude_none=True) for r in tr.rooms],
    }
    if tr.meta.get("rows") and not tr.boxes:
        ev_dict["rows"] = tr.meta["rows"]
    if tr.groups:
        ev_dict["groups"] = [g.model_dump(exclude_none=True) for g in tr.groups]
    if tr.links:
        ev_dict["links"] = [lk.model_dump(exclude_none=True) for lk in tr.links]
    ids = [b.id for b in tr.boxes] or [room_eid(r.room_id) for r in tr.rooms if r.included]
    body = _base(question, lang, scope) | {
        "answer": ans,
        "evidence": ev_dict,
        "canonical_en": question,
        "method": tr.method,
        "trace": [
            question_node(question, lang),
            intent_node(f"note · {scope_text(scope)}", intent_tag),
            {"id": "read", "parent": "q", "title": "Inspect the drawing", "tag": "Agent",
             "body": tr.summary or value, "kind": "model",
             **({"evidence_ids": ids} if ids else {}), "tool": tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "note",
             "body": value, "kind": "model",
             **({"evidence_ids": ids} if ids else {})},
        ],
    }
    return Built(body, [tr], [], tr.subject)


def stage_node(stage: str, nid: str, title: str, tag: str, body: str, *, kind: str = "exact",
               parent: str = "q", detail: dict | None = None, evidence_ids: list[str] | None = None,
               tool: dict | None = None, confidence: float | None = None) -> dict:
    """A trace node marked with its answering stage (resources → decision → filter → answer)."""
    n: dict[str, Any] = {"id": nid, "parent": parent, "title": title, "tag": tag, "body": body,
                         "kind": kind, "stage": stage}
    if detail is not None:
        n["detail"] = detail
    if evidence_ids is not None:
        n["evidence_ids"] = evidence_ids
    if tool is not None:
        n["tool"] = tool
    if confidence is not None:
        n["confidence"] = round(float(confidence), 3)
    return n


def rule_decision_node(chosen: str, matched: list[str], route: str = "exact",
                       body: str | None = None) -> dict:
    """The routing decision when the deterministic router decided (no model asked)."""
    return stage_node(
        "decision", "decision", "Route decision", "router · rules",
        body or f"Matched the '{chosen}' template by rule ({', '.join(matched)}); "
                "no model needed, answered from the indexed drawing.",
        kind="info",
        detail={"decider": "router", "model": None, "chosen": chosen, "confidence": 1.0,
                "matched": matched, "route": route, "runner_up": None})


def room_rank(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, scope: dict,
              matched: list[str], intent_tag: str = "router") -> Built:
    """'Which room is the biggest/smallest': one room picked from the grabbed room table."""
    m = tr.meta
    win, ru = m["winner"], m.get("runner_up")
    word = "largest" if m["order"] == "max" else "smallest"
    what = m.get("concept") or "room"
    # areas measured under an estimated scale (a scan that states none) are approximate: no decimals they do not have
    estimates = {r.get("room_id") for r in d.drawing.get("rooms", []) if r.get("area_source") == "estimate"}
    approx = win["id"] in estimates

    def _a(room: dict) -> str:
        return f"≈ {round(room['area_m2'])} m² (estimate)" if room["id"] in estimates else f"{room['area_m2']:.2f} m²"

    def _n(room: dict, default: str) -> str:
        # a room number the sheet prints ("1.03") tells rooms of one name apart; a store id ("e143313") tells nobody anything
        shown = "" if INTERNAL_ID.fullmatch(str(room["id"])) else f" ({room['id']})"
        return f"{room['name'] or default}{shown}"

    value = (f"{_n(win, 'Room')} is the {word} {what}: {_a(win)}"
             + (f"; next is {_n(ru, 'room')} at {_a(ru)}" if ru else "")
             + (" (tied)" if m.get("tie") else "") + ".")
    # the sentence people read, in the language they asked in (the trace stays English); only for plain rooms
    t = RANK_SAID.get(said_lang(lang)) if what == "room" else None
    said = value
    if t:
        def _al(room: dict) -> str:
            if room["id"] in estimates:
                return f"≈ {round(room['area_m2'])} m² ({t['estimate']})"
            return f"{room['area_m2']:.2f}".replace(".", ",") + " m²"
        said = (t[m["order"] if m["order"] in ("max", "min") else "max"].format(name=_n(win, t["room"]), area=_al(win))
                + (t["next"].format(name=_n(ru, t["room"]), area=_al(ru)) if ru else "")
                + (t["tied"] if m.get("tie") else "") + ".")
    all_ids = [room_eid(r.room_id) for r in tr.rooms]
    win_ids = [room_eid(win["id"])]
    dropped = m.get("dropped") or []
    reasons: dict[str, int] = {}
    for x in dropped:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
    trace = [
        question_node(question, lang),
        intent_node(f"{word} {what} · by net area · {scope_text(scope)}", intent_tag),
        rule_decision_node("room_rank", matched),
        stage_node("resources", "grab", "Grab candidates", "drawing index · rooms",
                   f"Read the indexed room table: {m['grabbed']} rooms with their printed or outlined "
                   f"areas in {tr.ms} ms, 0 model calls.",
                   detail={"tables": ["rooms"], "grabbed": m["grabbed"], "ms": tr.ms},
                   evidence_ids=all_ids, tool=tool_meta(tr)),
        stage_node("filter", "filter", "Filter candidates",
                   f"code · {'max' if m['order'] == 'max' else 'min'} by area",
                   f"Kept {len(m['kept'])} indoor {noun(what, 2)} with an area"
                   + (f"; dropped {len(dropped)} (" + ", ".join(f"{v} {k}" for k, v in reasons.items()) + ")"
                      if dropped else "")
                   + f". Sorted by net area: {win['name']} {win['id']} {win['area_m2']:.2f} m²"
                   + (f" before {ru['name']} {ru['id']} {ru['area_m2']:.2f} m²." if ru else "."),
                   parent="grab",
                   detail={"rule": f"{m['order']}(area_m2)", "kept": m["kept"], "dropped": dropped},
                   evidence_ids=win_ids),
        stage_node("answer", "answer", "Answer", "exact · CAD room areas",
                   value + " Source: indexed CAD room areas; 0 model calls, $0.",
                   detail={"source": "cad_geometry", "calls": 0, "cost_usd": 0.0},
                   evidence_ids=win_ids),
    ]
    body = _base(question, lang, scope) | {
        "answer": {"kind": "note", "value": said, "number": round(float(win["area_m2"]), 2), "unit": "m2"}
                  | ({"approximate": True} if approx else {}),
        "evidence": {"boxes": [], "rooms": [r.model_dump(exclude_none=True) for r in tr.rooms]},
        "canonical_en": f"Which is the {word} {what} {where_en(scope)}",
        **area_basis(d, [win["id"]], tr.method),
        "method": "cad_geometry",
        "trace": trace,
    }
    return Built(body, [tr], [], "room")


def adjacent(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, matched: list[str],
             ref: dict | None = None, intent_tag: str = "router") -> Built:
    """Doors/windows opening into given room(s); `ref` says how 'that room' was resolved."""
    m = tr.meta
    subject = tr.subject or "door"
    room_ids = m["room_ids"]
    rooms = {r["room_id"]: r for r in d.drawing.get("rooms", [])}
    names = ", ".join(f"{rooms.get(r, {}).get('name') or 'Room'} {r}" for r in room_ids)
    scope = {"kind": "room", "room_id": room_ids[0]}
    built = count(question, lang, d, tr, scope, None, intent_tag=intent_tag)
    n = int(tr.value or 0)
    ids = [b.id for b in tr.boxes]
    decision = (stage_node(
        "decision", "decision", "Resolve the reference", "context · previous answer",
        f"“{ref['phrase']}” = {names}, the room(s) the previous answer highlighted.",
        kind="info", detail={"decider": "context", "model": None, "chosen": room_ids,
                             "confidence": 1.0, "matched": matched, "route": "exact",
                             "source_answer_id": ref.get("answer_id"), "runner_up": None})
        if ref else rule_decision_node("adjacent", matched))
    built.body["trace"] = [
        question_node(question, lang),
        intent_node(f"{noun(subject, 2)} opening into {names}", intent_tag),
        decision,
        stage_node("resources", "grab", "Grab candidates", f"DXF · {subject} layer + room outlines",
                   f"Read all {m['grabbed']} {noun(subject, 2)} and the outline of {names} in {tr.ms} ms, "
                   "0 model calls.",
                   detail={"tables": [f"{subject} boxes", "room outlines"], "grabbed": m["grabbed"],
                           "ms": tr.ms}, tool=tool_meta(tr)),
        stage_node("filter", "filter", "Filter candidates", "geometry · touches the room",
                   tr.summary, parent="grab",
                   detail={"rule": f"box within {m.get('reach_m', 0.35)} m of the room outline (both sides of a wall)",
                           "kept": m["kept"], "dropped": [{"id": x, "reason": "not on this room's walls"}
                                                          for x in m["dropped"]]},
                   evidence_ids=ids),
        stage_node("answer", "answer", "Answer", "exact · CAD geometry",
                   f"{n} {noun(subject, n)} open into {names}, each drawn on the plan. 0 model calls, $0.",
                   detail={"source": "cad_geometry", "calls": 0, "cost_usd": 0.0}, evidence_ids=ids),
    ]
    built.body["canonical_en"] = f"Which {noun(subject, 2)} open into {names}"
    built.body.pop("gold", None)
    built.check_ids = []  # exact geometry; no image-model cross-check (0 model calls)
    return built


def text(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, subject: str,
         intent_tag: str = "router") -> Built:
    """A printed text found on the drawing (contract 1.1 answer kind 'text')."""
    value = tr.text or ""
    text_boxes = [b.model_dump(exclude_none=True) for b in tr.boxes]
    ids = [b["id"] for b in text_boxes]
    body = _base(question, lang, {"kind": "floor"}) | {
        "answer": {"kind": "text", "value": value, "subject": subject},
        "evidence": {"boxes": text_boxes, "rooms": []},
        "canonical_en": "What is the fire rating" if subject == "fire_rating" else
                        "What does the printed label say",
        "method": tr.method,
        "trace": [
            question_node(question, lang),
            intent_node(f"text · {subject.replace('_', ' ')}", intent_tag),
            {"id": "find", "parent": "q", "title": "Find printed text", "tag": "DXF · TEXT",
             "body": tr.summary, "kind": "exact", "evidence_ids": ids, "tool": tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "text",
             "body": f"Printed on the drawing: “{value}”.", "kind": "exact", "evidence_ids": ids},
        ],
    }
    return Built(body, [tr], [], subject)


def not_supported(question: str, lang: str, scope: dict, tr: ToolResult,
                  intent_tag: str = "router") -> Built:
    plural = noun(tr.subject or "", 2)
    return abstain(question, lang, scope, "not_supported_yet",
                   f"How many {plural} are drawn {where_en(scope)}", tr.summary,
                   intent_tag=intent_tag, results=[tr])


def score_estimate(question: str, lang: str, subject: str, value: int, conf: float,
                   probs: dict[str, float], measured: dict, cached: bool) -> Built:
    """A count read by Gemini Decisions from the image: always worded as a model estimate."""
    word = noun(subject, value)
    pct = int(conf * 100)
    body = _base(question, lang, {"kind": "floor"}) | {
        "answer": {"kind": "count", "value": value, "subject": subject},
        "evidence": {"boxes": [], "rooms": []},
        "canonical_en": f"How many {noun(subject, 2)} are on this floor plan",
        "method": "vision",
        "estimate": {"confidence": round(conf, 4), "probabilities": probs, "measured": measured},
        "trace": [
            question_node(question, lang),
            intent_node(f"count · {noun(subject, 2)} · whole floor", "router · image"),
            {"id": "read", "parent": "q", "title": "Read the image", "tag": "Decisions API · score",
             "body": (f"Gemini Decisions estimated {value} {word} from the image, {pct} % sure"
                      + (" (cached: 0 model calls)." if cached else ".")),
             "kind": "model", "confidence": round(conf, 4)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "model estimate",
             "body": (f"{value} {word} · model estimate, {pct} % sure. Not measured from geometry; "
                      f"no boxes. At this confidence such answers were right {measured['correct']} "
                      f"of {measured['n']} times ({measured['source']})."),
             "kind": "model"},
        ],
    }
    return Built(body, [], [], subject)


def score_low_confidence(question: str, lang: str, subject: str, conf: float,
                         probs: dict[str, float]) -> Built:
    """Below the gate: no count is stated; the probabilities are shown."""
    top = ", ".join(f"{k}: {int(v * 100)} %" for k, v in
                    sorted(probs.items(), key=lambda kv: -kv[1])[:3])
    b = abstain(question, lang, {"kind": "floor"}, "low_confidence",
                f"How many {noun(subject, 2)} are on this floor plan",
                f"Gemini Decisions was only {int(conf * 100)} % sure ({top}); below the 60 % gate "
                "no count is given.", intent_body=f"count · {noun(subject, 2)} · whole floor",
                intent_tag="router · image")
    b.body["estimate"] = {"confidence": round(conf, 4), "probabilities": probs}
    return b
