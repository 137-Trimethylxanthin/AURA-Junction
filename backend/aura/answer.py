"""Builds AskResponse bodies. The ONLY place `answer` values are set, always from a ToolResult.

Trace layout follows frontend local.ts: q → intent, source → read/collect/scope/whatif,
check, answer. Gold is looked up by scope and family in the drawing's gold table and
reported with the dataset line for the question's language.
"""

from __future__ import annotations

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
    plural = noun(subject, 2)
    gold = None if overrides or scope.get("kind") == "region" or tr.method in ("vision", "agent_estimate") else (
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
        canonical = f"How many {plural} are drawn {where_en(scope)}"
    else:
        all_ids = [room_eid(r.room_id) for r in tr.rooms]
        answer_ids = [room_eid(r.room_id) for r in tr.rooms if r.included]
        trace.append({"id": "collect", "parent": "source", "title": "Collect rooms",
                      "tag": "room labels", "body": tr.summary, "kind": "exact",
                      "evidence_ids": all_ids, "tool": tool_meta(tr)})
        check_ids = []
        answer_body = f"{value} {noun(subject, value)}. Each room is lit on the plan for you to check."
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
    if tr.meta.get("rows") and not tr.boxes:
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
                           f"Sum of {len(included)} net room areas = {value:.2f} m², shown room by room."),
                  "kind": "exact", "evidence_ids": inc_ids})
    # A model's free-form answer is not the template the dataset row asks ("the area of the rooms with two doors" is
    # not the floor total): only exact template answers are compared with the dataset.
    gold = (None if overrides or k == "region" or tr.method == "agent_estimate"
            else gold_for(d, lang, "area", None, scope, value))
    ev_dict: dict = {"boxes": [], "rooms": [r.model_dump(exclude_none=True) for r in rooms]}
    if tr.groups:
        ev_dict["groups"] = [g.model_dump(exclude_none=True) for g in tr.groups]
    if tr.links:
        ev_dict["links"] = [lk.model_dump(exclude_none=True) for lk in tr.links]
    body = _base(question, lang, scope) | {
        "answer": {"kind": "area", "value": value, "unit": "m2"},
        "evidence": ev_dict,
        "canonical_en": canonical,
        "method": tr.method,
        "trace": trace,
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


def note(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, scope: dict,
         intent_tag: str = "router") -> Built:
    """Open-ended answer from the deep agent (`AnswerNote`, contract 1.2)."""
    value = tr.text or tr.summary or ""
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
    value = (f"{win['name'] or 'Room'} ({win['id']}) is the {word} {what}: {win['area_m2']:.2f} m²"
             + (f"; next is {ru['name'] or 'room'} ({ru['id']}) at {ru['area_m2']:.2f} m²" if ru else "")
             + (" (tied)" if m.get("tie") else "") + ".")
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
        "answer": {"kind": "note", "value": value, "number": round(float(win["area_m2"]), 2), "unit": "m2"},
        "evidence": {"boxes": [], "rooms": [r.model_dump(exclude_none=True) for r in tr.rooms]},
        "canonical_en": f"Which is the {word} {what} {where_en(scope)}",
        "method": "cad_geometry",
        "trace": trace,
    }
    return Built(body, [tr], [], "room")


_RBC: dict[str, dict] = {
    "en": {"room": "Room", "together": "together",
           "ops": {"gt": "more than", "ge": "at least", "lt": "fewer than", "le": "at most", "eq": "exactly"},
           "things": {"door": ("door", "doors"), "window": ("window", "windows")},
           "head": lambda k, p: f"{k} {'room has' if k == 1 else 'rooms have'} {p}"},
    "de": {"room": "Raum", "together": "zusammen",
           "ops": {"gt": "mehr als", "ge": "mindestens", "lt": "weniger als", "le": "höchstens", "eq": "genau"},
           "things": {"door": ("Tür", "Türen"), "window": ("Fenster", "Fenster")},
           "head": lambda k, p: f"{k} {'Raum hat' if k == 1 else 'Räume haben'} {p}"},
    "fr": {"room": "Pièce", "together": "au total",
           "ops": {"gt": "plus de", "ge": "au moins", "lt": "moins de", "le": "au plus", "eq": "exactement"},
           "things": {"door": ("porte", "portes"), "window": ("fenêtre", "fenêtres")},
           "head": lambda k, p: f"{k} {'pièce a' if k == 1 else 'pièces ont'} {p}"},
    "fi": {"room": "Huone", "together": "yhteensä",
           "ops": {"gt": "enemmän kuin", "ge": "vähintään", "lt": "vähemmän kuin", "le": "enintään", "eq": "tasan"},
           "things": {"door": ("ovi", "ovea"), "window": ("ikkuna", "ikkunaa")},
           "head": lambda k, p: f"{k} huoneessa on {p}"},
}


def rooms_by_count(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, matched: list[str],
                   intent_tag: str = "router") -> Built:
    """'Rooms with more than 1 door' (marked, counted or summed by area): an exact filter over the indexed rooms."""
    m = tr.meta
    subject = tr.subject or "room"
    box_subject = tr.args.get("subject", "door")
    kept = m["kept"]
    scope = {"kind": "floor"}
    L = _RBC.get(lang, _RBC["en"])
    op, n = tr.args["op"], int(tr.args["n"])
    thing = L["things"][box_subject][0 if n == 1 else 1]
    names = ", ".join(f"{k['name'] or L['room']} {k['id']} ({k['n']})" for k in kept)
    head = L["head"](len(kept), f"{L['ops'][op]} {n} {thing}")
    if m["measure"] == "area":
        value = f"{head}; {L['together']} {m['total_m2']:.2f} m²" + (f": {names}." if kept else ".")
        number, unit = m["total_m2"], "m2"
    else:
        value = head + (f": {names}." if kept else ".")
        number, unit = float(len(kept)), None
    kept_ids = [room_eid(k["id"]) for k in kept]
    box_ids = [b.id for b in tr.boxes]
    trace = [
        question_node(question, lang),
        intent_node(f"rooms with {m['phrase']} · {m['measure']} · {scope_text(scope)}", intent_tag),
        rule_decision_node("rooms_by_count", matched),
        stage_node("resources", "grab", "Grab candidates", f"drawing index · rooms + {noun(box_subject, 2)}",
                   f"Read {m['candidates']} indoor rooms and every {box_subject} box on this floor in {tr.ms} ms, "
                   "0 model calls." + (f" {m['outdoor']} outdoor spaces left out." if m["outdoor"] else ""),
                   detail={"tables": ["rooms", box_subject], "grabbed": m["candidates"], "ms": tr.ms},
                   evidence_ids=[room_eid(r.room_id) for r in tr.rooms], tool=tool_meta(tr)),
        stage_node("filter", "filter", "Filter candidates", f"code · {box_subject}s touching each room outline",
                   f"A {box_subject} counts for every room whose outline its box touches (within "
                   f"{m['reach_m']:.2f} m), so one on a shared wall counts for both rooms. Kept the rooms with "
                   f"{m['phrase']}: {len(kept)}.",
                   parent="grab", detail={"rule": f"{tr.args['op']} {tr.args['n']}", "kept": kept},
                   evidence_ids=kept_ids + box_ids),
        stage_node("answer", "answer", "Answer", "exact · CAD rooms and boxes",
                   value + " Source: indexed CAD rooms and boxes; 0 model calls, $0.",
                   detail={"source": "cad_geometry", "calls": 0, "cost_usd": 0.0}, evidence_ids=kept_ids),
    ]
    body = _base(question, lang, scope) | {
        "answer": {"kind": "note", "value": value, "number": round(float(number), 2),
                   **({"unit": unit} if unit else {})},
        "evidence": {"boxes": [b.model_dump(exclude_none=True) for b in tr.boxes],
                     "rooms": [r.model_dump(exclude_none=True) for r in tr.rooms]},
        "canonical_en": f"Which rooms have {m['phrase']}" + (" and what is their total area" if unit else ""),
        "method": "cad_geometry",
        "trace": trace,
    }
    return Built(body, [tr], [], subject)


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


def dimension(question: str, lang: str, d: LoadedDrawing, tr: ToolResult, matched: list[str],
              intent_tag: str = "router") -> Built:
    """A printed dimension of the plan (drawing.dimensions): a note with the number in metres and the dimension
    texts as evidence boxes.  0 model calls."""
    boxes = [b.model_dump(exclude_none=True) for b in tr.boxes]
    ids = [b["id"] for b in boxes]
    subject = tr.args.get("subject", "overall")
    what = (f"length of wall #{subject.split(':')[1]}" if subject.startswith("wall:") else
            {"width": "width of the building", "depth": "depth of the building",
             "length": "length of the building"}.get(subject, "overall dimensions of the building"))
    body = _base(question, lang, {"kind": "floor"}) | {
        "answer": {"kind": "note", "value": tr.text or "", "number": float(tr.value or 0), "unit": "m"},
        "evidence": {"boxes": boxes, "rooms": []},
        "canonical_en": f"What is the {what}",
        "method": tr.method,
        "trace": [
            question_node(question, lang),
            intent_node(f"dimension · {what}", intent_tag),
            rule_decision_node("dimension", matched),
            {"id": "read", "parent": "q", "title": "Read the printed dimensions", "tag": "OCR · dimension strings",
             "body": tr.summary, "kind": "exact", "evidence_ids": ids, "tool": tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "dimension",
             "body": f"{tr.text}, as printed on the drawing (converted to metres).", "kind": "exact",
             "evidence_ids": ids},
        ],
    }
    return Built(body, [tr], [], "dimension")


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
