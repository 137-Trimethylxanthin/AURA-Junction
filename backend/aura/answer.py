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
    if k == "floor":
        return True
    if k == "region":
        return False
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
    gold = None if overrides or scope.get("kind") == "region" or tr.method == "vision" else (
        gold_for(d, lang, "count", subject, scope, value))
    trace: list[dict] = [question_node(question, lang),
                         intent_node(f"count · {plural} · {scope_text(scope)}", intent_tag,
                                     intent_conf),
                         PICK_SOURCE]
    vision = tr.method == "vision"
    if vision:
        trace[2] = {"id": "source", "parent": "q", "title": "Pick the best source",
                    "tag": "router", "kind": "model",
                    "body": "This drawing is an image with no CAD geometry, so Gemini found the "
                            "boxes on the image once at upload. The count is a model estimate."}
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
        PICK_SOURCE,
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
    gold = None if overrides or k == "region" else gold_for(d, lang, "area", None, scope, value)
    body = _base(question, lang, scope) | {
        "answer": {"kind": "area", "value": value, "unit": "m2"},
        "evidence": {"boxes": [], "rooms": [r.model_dump(exclude_none=True) for r in rooms]},
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
    """An IFC quantity, given exactly as the model states it (never recomputed)."""
    what = QUANTITY_EN.get(tr.subject or "", tr.subject or "quantity")
    unit = tr.meta.get("unit", "m")
    rows = tr.meta.get("rows", [])
    body = _base(question, lang, scope) | {
        "answer": {"kind": "quantity", "value": float(tr.value), "unit": unit},
        "evidence": {"boxes": [], "rooms": [], **({"rows": rows} if rows else {})},
        "canonical_en": f"What is the {what} {where_en(scope)}",
        "method": tr.method,
        "trace": [
            question_node(question, lang),
            intent_node(f"quantity · {what} · {scope_text(scope)}", intent_tag),
            {"id": "read", "parent": "q", "title": "Read the IFC quantities", "tag": "IFC · Qto",
             "body": tr.summary, "kind": "exact", "tool": tool_meta(tr)},
            {"id": "answer", "parent": "q", "title": "Answer", "tag": "quantity",
             "body": f"{tr.value} {unit}, as stated in the model's quantity sets.",
             "kind": "exact"},
        ],
    }
    gold = gold_for(d, lang, "quantity", tr.subject, scope, float(tr.value))
    if gold:
        body["gold"] = gold
    return Built(body, [tr], [], tr.subject)


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
