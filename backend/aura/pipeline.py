"""One pipeline for /ask and /chat: understand → tools → answer → check.

`run(...)` is an async generator of (event, data) pairs in the /chat order:
progress*, understood, (tool_call, tool_result)*, check?, answer | error. /ask collects it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aura import answer as A
from aura import dimension_answers, tools, units
from aura.agent.loop import MAX_ITERATIONS, AgentResult, describe_call, run_agent
from aura.budget import Budget, BudgetError, watch_refusals
from aura.cache import AnswerCache, CheckCache, answer_key
from aura.config import Settings, models_enabled
from aura.contract import (
    BOX_SUBJECTS,
    AskRequest,
    AskResponse,
    ChatRequest,
    EvidenceGroup,
    EvidenceRoom,
)
from aura.gemini.check import check_key, default_anchors, run_check, slice_check
from aura.gemini.client import GeminiClient, GeminiError, Meter
from aura.gemini.score import FAMILIES as SCORE_FAMILIES
from aura.gemini.score import MEASURED as SCORE_MEASURED
from aura.gemini.score import MIN_CONFIDENCE as SCORE_MIN_CONFIDENCE
from aura.gemini.score import family_of as score_family_of
from aura.gemini.score import run_score
from aura.gemini.understand import MIN_CONFIDENCE, decide_hybrid_route, understand
from aura.provenance import allowed_numbers, filter_prose
from aura.router import (
    COLOR_CUE,
    DEICTIC_SIMILARITY,
    EXACT_INTENTS,
    FIRE_PATTERN,
    QUALIFIER_GUARD,
    ROUTER_VERSION,
    STAIR_ESCALATE,
    is_followup_message,
    route,
)
from aura.steps import answer_with_tools
from aura.store import Store
from aura.writer import Writer

if TYPE_CHECKING:
    from aura.agent_client import AgentClient

log = logging.getLogger("aura.pipeline")

MODEL_STEP_TIMEOUT_S = 8.0  # one model step (choice or check); the agent has AGENT_TIMEOUT_S
CHECK_RETRY_AFTER_S = 600.0  # a failed check of one layer is not re-sent for 10 minutes
HEARTBEAT_INTERVAL_S = 15.0  # SSE `: progress {s}` comment interval during long turns


class PipelineError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class Services:
    settings: Settings
    budget: Budget
    client: GeminiClient | None
    registry: tools.Registry
    store: Store | None
    writer: Writer | None
    answers: AnswerCache
    checks: CheckCache
    agent: AgentClient | None = None
    drawing_store: Any = None  # aura.drawing_store.DrawingStore; available() = Postgres up and ready
    heartbeat_s: float = HEARTBEAT_INTERVAL_S
    check_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    check_failures: dict[str, float] = field(default_factory=dict)  # key → time of last failure

    def models_usable(self) -> bool:
        return (self.client is not None and self.client.available() and models_enabled()
                and self.budget.can_spend())


@dataclass
class Caller:
    ip_hash: str | None = None
    presenter: bool = False


def ensure_ready(svc: Services, drawing_id: str) -> None:
    """Uploads are answered only when ready (409 processing), and not at all once deleted."""
    if not drawing_id.startswith("up-") or svc.store is None:
        return
    row = svc.store.drawing_row(drawing_id)
    if row is None:
        svc.registry.evict(drawing_id)
        raise PipelineError("unknown_drawing", f"Unknown drawing '{drawing_id}'", 404)
    if row["status"] != "ready":
        raise PipelineError("processing", f"Drawing '{drawing_id}' is {row['status']}.", 409)


def with_unknown_areas(out: dict) -> dict:
    """Contract: an evidence room with no known area carries area_m2: null (never 0, never
    dropped), included: false. The only null a response may contain."""
    for room in (out.get("evidence") or {}).get("rooms") or []:
        room.setdefault("area_m2", None)
    return out


def _tool_events(built: A.Built) -> list[tuple[str, dict]]:
    """tool_call / tool_result pairs for the trace nodes that carry a tool."""
    out: list[tuple[str, dict]] = []
    by_id = {r.result_id: r for r in built.results}
    for node in built.body["trace"]:
        tool = node.get("tool")
        if not tool:
            continue
        r = by_id.get(tool["result_id"])
        out.append(("tool_call", {"node": node}))
        out.append(("tool_result", {
            "node_id": node["id"],
            "evidence": {"boxes": [b.model_dump(exclude_none=True) for b in r.boxes] if r else [],
                         "rooms": [x.model_dump(exclude_none=True) | {"area_m2": x.area_m2}
                                   for x in r.rooms] if r else []},
            "summary": r.summary if r else "",
        }))
    return out


def context_from(turns: list[dict]) -> dict | None:
    """Last exact subject, intent and scope of a conversation, for short follow-ups."""
    for t in reversed(turns):
        resp = t.get("response") or {}
        if resp.get("method") == "agent_estimate":
            return None
        ans = resp.get("answer") or {}
        if ans.get("kind") in ("count", "area"):
            return {"intent": ans["kind"], "subject": ans.get("subject"), "scope": resp.get("scope")}
    return None


def last_turn_response(turns: list[dict] | None) -> dict | None:
    """Immediately preceding assistant response in a multi-turn conversation, if any."""
    if not turns:
        return None
    for t in reversed(turns):
        resp = t.get("response")
        if isinstance(resp, dict) and resp.get("answer"):
            return resp
    return None


_REF_PHRASE = re.compile(r"\b(?:that|this|the same|those|these)\s+\w+|\b(?:it|them|its)\b", re.IGNORECASE)


def resolve_reference(r, question: str, history: list[dict] | None, d: tools.LoadedDrawing) -> dict | None:
    """'that room' / 'it' → the room(s) the most recent answer (of the last 3) highlighted.

    On success rewrites `r` into an exact intent (doors/windows: `adjacent`, both sides of a
    wall; fixtures: a count in that one room) and returns {phrase, answer_id, room_ids}. Returns
    None when nothing (or too much) was highlighted, so the turn goes to the model with context."""
    if "anaphora" not in r.matched or not history or not r.subject:
        return None
    if d.drawing.get("kind") != "cad_dxf":
        return None
    known = set(d.ctx.room_ids)
    prev_resp: dict | None = None
    rooms: list[str] = []
    for t in list(reversed(history))[:3]:
        resp = t.get("response")
        if not isinstance(resp, dict) or not resp.get("answer"):
            continue
        ev = resp.get("evidence") or {}
        rooms = [x.get("room_id") for x in ev.get("rooms") or [] if x.get("included")
                 and x.get("room_id") in known]
        if not rooms and (resp.get("scope") or {}).get("kind") == "room":
            rooms = [resp["scope"]["room_id"]] if resp["scope"].get("room_id") in known else []
        if rooms or ev.get("boxes"):
            prev_resp = resp  # the most recent answer that showed something decides
            break
    if prev_resp is None or not rooms or len(rooms) > 3:
        return None
    if r.subject in ("door", "window"):
        r.intent = "adjacent"
    elif len(rooms) == 1:
        r.intent = "count"
    else:
        return None
    r.scope = {"kind": "room", "room_id": rooms[0]}
    r.matched.append("resolved_reference")
    m = _REF_PHRASE.search(question)
    return {"phrase": m.group(0) if m else "that", "answer_id": prev_resp.get("answer_id"),
            "room_ids": rooms}


def decision_node(hd, route: str) -> dict:
    """The gemini-decision-1 routing step as a trace node: every question it was asked, its
    calibrated answer, what was chosen (exact / cache / agent / abstain) and the runner-up."""
    a = hd.asked
    ex = hd.exact_sufficient
    parts: list[str] = []
    if "on_drawing" in a and a["on_drawing"]["p"] is not None:
        parts.append(f"about the drawing? p = {a['on_drawing']['p']:.2f}")
    if ex is not None:
        parts.append(f"a plain count/area answers it? p = {ex:.2f}")
    for k in ("intent", "subject", "scope"):
        if k in a and a[k]["choice"] is not None:
            probs = sorted(a[k]["probabilities"].items(), key=lambda kv: -kv[1])
            ru = f", runner-up {probs[1][0]} {probs[1][1]:.2f}" if len(probs) > 1 else ""
            parts.append(f"{k} = {a[k]['choice']} ({a[k]['confidence']:.2f}{ru})")
    if "cache_match" in a:
        cm = a["cache_match"]
        parts.append(f"earlier answer = {('“' + cm['question'] + '”') if cm.get('question') else cm['choice']} "
                     f"({cm['confidence']:.2f})")
    if route == "exact":
        chosen_conf, runner, runner_conf = ex, "agent", (None if ex is None else 1 - ex)
        matched = f"template {hd.intent}" + (f" · {hd.subject}" if hd.subject else "")
    elif route == "cache":
        chosen_conf, runner, runner_conf = hd.cache_conf, "agent", None
        matched = f"cached question “{hd.cache_question}”"
    elif route == "abstain":
        p = a.get("on_drawing", {}).get("p")
        chosen_conf, runner, runner_conf = (None if p is None else 1 - p), "agent", p
        matched = "off the drawing"
    else:
        chosen_conf, runner, runner_conf = (None if ex is None else 1 - ex), "exact", ex
        matched = "no template or earlier answer fits whole"
    return {
        "id": "decision", "parent": "q", "title": "Route decision · gemini-decision-1",
        "tag": f"Decisions API · {route}" + (f" · {chosen_conf:.2f}" if chosen_conf is not None else ""),
        "body": "; ".join(parts) + f". → {route}: {matched}.",
        "kind": "model", "stage": "decision",
        **({"confidence": round(chosen_conf, 3)} if chosen_conf is not None else {}),
        "detail": {"decider": "gemini-decision-1", "model": hd.model or "gemini-decision-1",
                   "question": "Route this question: off the drawing, an earlier answer, an exact "
                               "template, or the agent?",
                   "options": ["exact", "cache", "agent", "abstain"], "chosen": route,
                   "confidence": chosen_conf, "runner_up": runner,
                   "runner_up_confidence": runner_conf, "matched": matched, "route": route,
                   "asked": a, "calls": hd.calls, "cost_usd": hd.cost_usd,
                   "latency_ms": hd.latency_ms},
    }


def _no_nulls(v: Any) -> Any:
    """The contract carries no nulls: drop None values from trace detail payloads."""
    if isinstance(v, dict):
        return {k: _no_nulls(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_no_nulls(x) for x in v if x is not None]
    return v


def _with_decision(trace: list[dict], node: dict | None) -> list[dict]:
    """Insert the routing decision after the intent node unless the trace already has one."""
    if node is None or any(n.get("stage") == "decision" for n in trace):
        return trace
    i = next((k + 1 for k, n in enumerate(trace) if n.get("id") == "intent"), min(1, len(trace)))
    return [*trace[:i], node, *trace[i:]]


FOLLOW_UP_CUE = re.compile(
    r"^\s*(and|what about|how about|same|et|und|ja|entä|e|ed|plus|also|then)\b", re.IGNORECASE)
WHOLE_FLOOR = re.compile(r"\b(in total|total|whole|entire|all|altogether|insgesamt|gesamt|au total|"
                         r"en tout|yhteensä|kaikki|in tutto)\b", re.IGNORECASE)
_FILTER_RESTRICTION = re.compile(
    r"\b(only|just|exclude|without|except|seulement|uniquement|sans|sauf|vain|ilman|nur|ohne|außer|ausser)\b",
    re.IGNORECASE,
)


def _try_deterministic_color_followup(
    question: str,
    lang: str,
    d: tools.LoadedDrawing,
    history: list[dict] | None,
) -> A.Built | None:
    """`<2ms`, `0`-call color-coding follow-up on a prior turn's rooms (e.g. `"give me all areas"`
    -> `"color in different colors"` / `"color by apartment"` / `"color by type"`)."""
    if not history or not COLOR_CUE.search(question):
        return None
    if (QUALIFIER_GUARD.search(question) or STAIR_ESCALATE.search(question)
            or DEICTIC_SIMILARITY.search(question) or _FILTER_RESTRICTION.search(question)):
        return None
    prev = last_turn_response(history)
    if prev is None:
        return None
    prev_ev = prev.get("evidence") or {}
    known = set(d.ctx.room_ids)
    # Only the previous turn's rooms on THIS drawing (a project turn carries other floors' rooms).
    raw_rooms = [r for r in prev_ev.get("rooms") or []
                 if r.get("drawing_id") in (None, d.id) and r.get("room_id") in known]
    if not raw_rooms:
        return None
    prev_ans = prev.get("answer") or {}
    if prev_ans.get("kind") not in ("area", "count"):
        return None

    by_apt = bool(re.search(r"\b(apartment|wohnung|appartement|asunto|unit)\b", question, re.IGNORECASE))
    colored_dicts, group_dicts = tools.color_code_rooms(d, raw_rooms, by_apartment=by_apt)
    if not group_dicts:
        return None
    colored_rooms = [EvidenceRoom.model_validate(r) for r in colored_dicts]
    groups = [EvidenceGroup.model_validate(g) for g in group_dicts]

    prev_scope = prev.get("scope") or {"kind": "floor"}
    prev_method = prev.get("method") if prev.get("method") in ("cad_geometry", "agent_estimate", "vision") else "cad_geometry"
    rid = "r-color-" + hashlib.sha1(f"{d.id}:{prev.get('answer_id', '')}:{question}".encode()).hexdigest()[:10]
    if prev_ans["kind"] == "area":
        tr = tools.ToolResult(
            tool="room_areas",
            args={"scope": prev_scope, "color_by": "apartment" if by_apt else "category"},
            result_id=rid,
            kind="area",
            value=float(prev_ans["value"]),
            subject="room",
            rooms=colored_rooms,
            groups=groups,
            method=prev_method,  # type: ignore[arg-type]
            summary=f"{float(prev_ans['value']):.2f} m² across {len(groups)} color group(s)",
            meta={"denominator": prev_ans.get("denominator") or "net_floor_area"},
        )
        built = A.area(question, lang, d, tr, prev_scope, None, intent_tag="router · follow-up")
    else:
        subj = prev_ans.get("subject") or "room"
        tr = tools.ToolResult(
            tool="count_objects",
            args={"subject": subj, "scope": prev_scope, "color_by": "apartment" if by_apt else "category"},
            result_id=rid,
            kind="count",
            value=int(prev_ans["value"]),
            subject=subj,
            rooms=colored_rooms,
            groups=groups,
            method=prev_method,  # type: ignore[arg-type]
            summary=f"{int(prev_ans['value'])} {A.noun(subj, int(prev_ans['value']))} across {len(groups)} color group(s)",
        )
        built = A.count(question, lang, d, tr, prev_scope, None, intent_tag="router · follow-up")
    if prev_method == "agent_estimate" and prev.get("estimate"):
        built.body["estimate"] = prev["estimate"]
    return built


def _apply_color_if_requested(d: tools.LoadedDrawing, tr: tools.ToolResult, question: str, matched: list[str]) -> None:
    """Attach categorical color groups (`c1..c6`) in `<2ms` when an exact room/area query asks for color."""
    if "color_cue" in matched and tr.rooms and not tr.groups:
        by_apt = bool(re.search(r"\b(apartment|wohnung|appartement|asunto|unit)\b", question, re.IGNORECASE))
        raw_rooms = [r.model_dump(exclude_none=True) | {"area_m2": r.area_m2} for r in tr.rooms]
        colored_dicts, group_dicts = tools.color_code_rooms(d, raw_rooms, by_apartment=by_apt)
        tr.rooms = [EvidenceRoom.model_validate(r) for r in colored_dicts]
        tr.groups = [EvidenceGroup.model_validate(g) for g in group_dicts]


def identifiers_in(question: str, d: tools.LoadedDrawing) -> str:
    """Numbers in the question that name something on the drawing (room numbers) may be quoted
    back; any other number the user typed is NOT provenance ("user said 7" never becomes 7)."""
    ids = set(d.ctx.room_ids)
    return " ".join(t for t in re.findall(r"[A-Z]{0,2}\d+(?:\.\d+)?", question) if t in ids)


AGENT_TIMEOUT_S = 25.0
AGENT_TAG = "agent · flash-lite"


async def _agent(svc: Services, d: tools.LoadedDrawing, question: str, lang: str, caller: Caller,
                 stats: dict, history: list[dict] | None, choice_tag: str) -> A.Built:
    """Function calling; the answer comes from the ToolResult the model names."""
    meter: Meter = stats["meter"]
    before = meter.calls
    partial = AgentResult(final=None, explanation="")
    try:
        async with asyncio.timeout(AGENT_TIMEOUT_S):
            ar = await run_agent(svc.client, d, question, history=history,  # type: ignore[arg-type]
                                 ip_hash=caller.ip_hash, presenter=caller.presenter,
                                 meter=meter, partial=partial)
    except (BudgetError, GeminiError, TimeoutError) as e:
        stats["no_cache"] = True
        sent = meter.calls - before
        stats["route"] = "agent" if sent else stats["route"]
        what = f"{sent} request(s) sent, no usable answer" if sent else "skipped"
        return A.unclear(question, lang, d, intent_tag=choice_tag,
                         look=f"The model step was {what}: {e or 'timeout'}.")
    stats["route"] = "agent"
    final = ar.final
    nodes = []
    for i, tr in enumerate(ar.results, 1):
        if (final is not None and tr.result_id == final.result_id
                and final.kind in ("count", "area", "text") and final.supported):
            continue  # the answer's own read/collect node carries this call
        nodes.append({"id": f"agent-{i}", "parent": "q", "title": "Tool call", "tag": AGENT_TAG,
                      "body": tr.summary or describe_call(tr), "kind": "exact",
                      "evidence_ids": [b.id for b in tr.boxes] or None,
                      "tool": A.tool_meta(tr)})
    if final is None or final.kind not in ("count", "area", "text"):
        why = {"no_final": "The model did not name a tool result.",
               "limit": f"The model used its {MAX_ITERATIONS} steps without a final answer."}.get(
            ar.stopped, "No tool result answers this question.")
        built = A.unclear(question, lang, d, intent_tag=AGENT_TAG, look=why)
    elif not final.supported:
        built = A.not_supported(question, lang, final.args.get("scope") or {"kind": "floor"}, final,
                                intent_tag=AGENT_TAG)
    elif final.kind == "count":
        built = A.count(question, lang, d, final, final.args.get("scope") or {"kind": "floor"},
                        None, intent_tag=AGENT_TAG)
        if final.args.get("label"):  # rooms with one label inside a scope
            where = tools.where_en(final.args.get("scope") or {"kind": "floor"})
            built.body["canonical_en"] = (f"How many rooms labelled {final.args['label']} are "
                                          f"{where}")
    elif final.kind == "area":
        built = A.area(question, lang, d, final, final.args.get("scope") or {"kind": "floor"},
                       None, intent_tag=AGENT_TAG)
    else:
        built = A.text(question, lang, d, final, final.subject or "label", intent_tag=AGENT_TAG)
    trace = built.body["trace"]
    at = next((i for i, n in enumerate(trace) if n["id"] == "answer"), len(trace))
    nodes = [{k: v for k, v in n.items() if v is not None} for n in nodes]
    if ar.explanation:
        kept, dropped = filter_prose(ar.explanation,
                                     allowed_numbers(ar.results, identifiers_in(question, d)))
        if kept:
            built.body["explanation"] = kept
        if dropped:
            nodes.append({"id": "provenance", "parent": "q", "title": "Provenance filter",
                          "tag": "numbers checked", "kind": "warn",
                          "body": f"Removed {len(dropped)} sentence(s) of model prose with a "
                                  "number that no tool result produced."})
    built.body["trace"] = trace[:at] + nodes + trace[at:]
    built.results = [*ar.results, *[r for r in built.results if r not in ar.results]]
    return built


async def _score(svc: Services, d: tools.LoadedDrawing, question: str, lang: str, caller: Caller,
                 stats: dict, family: str) -> A.Built:
    """Image-only plan, room/fixture count: ONE Decisions score request per drawing (cached),
    answered only at confidence >= 0.6, always as a model estimate."""
    subject = SCORE_FAMILIES[family][0]
    key = f"score:{d.id}"
    full = svc.checks.get(key)
    pre = d.dir / "score.json" if d.dir is not None else None
    if full is None and pre is not None and pre.is_file():  # paid by the uploader at upload time
        full = json.loads(pre.read_text(encoding="utf-8"))
    cached = full is not None

    def refuse(look: str) -> A.Built:
        return A.abstain(question, lang, {"kind": "floor"}, "not_supported_yet",
                         f"How many {A.noun(subject, 2)} are on this floor plan", look,
                         intent_body=f"count · {A.noun(subject, 2)} · whole floor",
                         intent_tag="router · image")

    if full is None:
        image = d.image_path()
        if image is None or not svc.models_usable() or svc.settings.answerer == "exact":
            return refuse("Counting rooms and fixtures on an image needs the model, which is off.")
        meter: Meter = stats["meter"]
        before = meter.calls
        try:
            async with asyncio.timeout(MODEL_STEP_TIMEOUT_S):
                full = await run_score(svc.client, image, ip_hash=caller.ip_hash,  # type: ignore[arg-type]
                                       presenter=caller.presenter, meter=meter)
        except (BudgetError, GeminiError, TimeoutError) as e:
            stats["no_cache"] = True
            sent = meter.calls - before
            what = f"{sent} request sent, no usable answer" if sent else "skipped"
            return refuse(f"The image model step was {what}: {e or 'timeout'}.")
        svc.checks.put(key, d.id, "score", full)
    fam = full["families"].get(family)
    if fam is None:
        return refuse("The model gave no estimate for this.")
    if fam["confidence"] < SCORE_MIN_CONFIDENCE:
        return A.score_low_confidence(question, lang, subject, fam["confidence"],
                                      fam["probabilities"])
    return A.score_estimate(question, lang, subject, round(fam["score"]), fam["confidence"],
                            fam["probabilities"], SCORE_MEASURED, cached)


async def _execute_exact_intent(
    d: tools.LoadedDrawing,
    question: str,
    lang: str,
    intent: str,
    subject: str | None,
    scope: dict,
    overrides: list[dict],
    matched: list[str],
    *,
    tag: str,
    conf: float | None = None,
    pattern: str | None = None,
    topic: str | None = None,
    ref: dict | None = None,
) -> tuple[A.Built, bool]:
    """Execute a deterministic solver call. Returns `(built, supported_cleanly)`."""
    if intent == "room_rank":
        if not d.engine.supports("area"):
            return A.abstain(question, lang, scope, "not_supported_yet", question,
                             "No rooms with areas are extracted from this drawing yet.",
                             intent_tag=tag), False
        printed = pattern.split("|") if pattern else None
        tr = await asyncio.to_thread(tools.rank_rooms, d, subject or "max", scope, printed, topic)
        if tr.kind == "none":
            return A.unclear(question, lang, d, scope, intent_tag=tag, look=tr.summary), False
        return A.room_rank(question, lang, d, tr, scope, matched, intent_tag=tag), True

    if intent == "adjacent":
        room_ids = (ref or {}).get("room_ids") or [scope.get("room_id")]
        tr = await asyncio.to_thread(tools.adjacent_boxes, d, subject or "door", room_ids)
        if tr.kind == "none":
            return A.unclear(question, lang, d, scope, intent_tag=tag, look=tr.summary), False
        return A.adjacent(question, lang, d, tr, matched, ref=ref, intent_tag=tag), True

    if intent == "find_text":
        pat = pattern or FIRE_PATTERN
        tr = await asyncio.to_thread(tools.find_text, d, pat, subject or "label")
        if tr.text:
            return A.text(question, lang, d, tr, subject or "label", intent_tag=tag), True
        n_texts = len(d.drawing.get("texts") or [])
        look = (f"Searched the printed text on this drawing for fire ratings (EI30, 30 MIN, T30): "
                f"none found{f' among {n_texts} texts' if n_texts else ''}.")
        return A.abstain_topic(question, lang, topic or "fire", intent_tag=tag, results=[tr],
                               look=look), False

    if intent == "abstain":
        return A.abstain_topic(question, lang, topic or "material", intent_tag=tag), True

    if intent == "quantity":
        tr = await asyncio.to_thread(tools.measure, d, subject or "", scope)
        if not tr.supported or tr.kind == "none":
            return A.abstain(question, lang, tr.args["scope"], "not_supported_yet",
                             "(an IFC quantity)", tr.summary, intent_tag=tag, results=[tr]), False
        return A.quantity(question, lang, d, tr, tr.args["scope"], intent_tag=tag), True

    if intent == "count":
        subj = subject or "room"
        tr = await asyncio.to_thread(tools.count_objects, d, subj, scope)
        if not tr.supported or tr.meta.get("abstain") == "not_supported_yet":
            return A.not_supported(question, lang, scope, tr, intent_tag=tag), False
        if tr.kind == "none":
            return A.unclear(question, lang, d, scope, intent_tag=tag), False
        _apply_color_if_requested(d, tr, question, matched)
        return A.count(question, lang, d, tr, scope, overrides, intent_tag=tag, intent_conf=conf), True

    # area
    if not d.engine.supports("area"):
        return A.abstain(question, lang, scope, "not_supported_yet", "What is the net floor area",
                         "No rooms with areas are extracted from this drawing yet.",
                         intent_tag=tag), False
    tr = await asyncio.to_thread(tools.room_areas, d, scope, overrides or None)
    if tr.kind == "none":
        return A.unclear(question, lang, d, scope, intent_tag=tag), False
    _apply_color_if_requested(d, tr, question, matched)
    return A.area(question, lang, d, tr, scope, overrides, intent_tag=tag, intent_conf=conf), True


async def _decide(svc: Services, req: AskRequest, d: tools.LoadedDrawing, caller: Caller,
                  stats: dict, context: dict | None = None,
                  history: list[dict] | None = None) -> A.Built:
    """Exact + legacy Gemini choice/agent path (used when `svc.agent is None` or `answerer == 'exact'`)."""
    question = req.question.strip()
    r = route(question, d.ctx)
    lang = r.lang if r.lang_signal else (req.lang_hint or "en")

    # Deterministic `<2ms` color-coding follow-up on prior rooms
    if (color_built := _try_deterministic_color_followup(question, lang, d, history)) is not None:
        return color_built

    if context:
        short = len(question.split()) <= 5
        cue = short and bool(FOLLOW_UP_CUE.search(question))
        if WHOLE_FLOOR.search(question) and r.intent in ("count", "area") and r.scope is None:
            r.scope = {"kind": "floor"}  # "how many doors in total?" never inherits a scope
        if (r.intent == "unknown" and cue and (r.scope or req.scope)
                and context["intent"] in ("count", "area")):
            # "and in apartment B?" → the previous question, new scope
            r.intent, r.subject = context["intent"], context.get("subject")
            r.matched.append("follow-up")
        elif r.intent in ("count", "area") and r.scope is None and cue and context.get("scope"):
            # "and doors?" → new subject, previous scope
            r.scope = context["scope"] if context["scope"].get("kind") != "region" else None
            r.matched.append("follow-up")

    # An image upload without an index is scored on the image; one the drawing store indexed
    # (evidence_method set) answers from its rooms and items like any other drawing.
    if (d.drawing.get("kind") == "raster" and not d.drawing.get("evidence_method")
            and (family := score_family_of(question)) is not None):
        return await _score(svc, d, question, lang, caller, stats, family)

    ref = resolve_reference(r, question, history, d)
    overrides = [o.model_dump() for o in req.overrides or []]
    req_scope = req.scope.model_dump(exclude_none=True) if req.scope else None
    intent, subject, text_scope, conf = r.intent, r.subject, r.scope, None
    tag = "router · follow-up" if "follow-up" in r.matched else "router"

    if intent == "unknown":
        if svc.settings.answerer == "exact":
            return A.abstain(
                question,
                lang,
                req_scope or text_scope or {"kind": "floor"},
                "not_supported_yet",
                question,
                "Exact-only mode is active (`AURA_ANSWERER=exact`); open-ended questions need the model.",
                intent_tag="router · exact",
            )
        if svc.models_usable():
            meter: Meter = stats["meter"]
            before = meter.calls
            try:
                async with asyncio.timeout(MODEL_STEP_TIMEOUT_S):
                    u = await understand(
                        svc.client, question,  # type: ignore[arg-type]
                        units=d.ctx.units, labels=d.ctx.labels, image_path=d.image_path(),
                        ip_hash=caller.ip_hash, presenter=caller.presenter, meter=meter)
            except (BudgetError, GeminiError, TimeoutError) as e:
                stats["no_cache"] = True  # a temporary refusal must not stick
                sent = meter.calls - before
                what = f"{sent} request sent, no usable answer" if sent else "skipped"
                look = (f"The question matched no template, and the model step was {what}: "
                        f"{e or 'timeout'}.")
                return A.unclear(question, lang, d, intent_tag="router", look=look)
            stats["route"] = "choice"
            tag, conf = f"Decisions API · {u.intent_conf:.2f}", u.intent_conf
            if u.off_drawing:
                return A.abstain(
                    question, lang, {"kind": "floor"}, "not_on_drawing", "(off the drawing)",
                    f"The model judged this information is not shown on the drawing "
                    f"(p = {u.on_drawing:.2f}).", intent_body="abstain · not on drawing",
                    intent_tag=tag)
            # Hand over to the agent when unsure, off-template, or when a room count has no
            # label (live: "places to sleep in apartment A" → choice 'room' would count all 11).
            vague_rooms = (u.intent == "count" and u.subject == "room"
                           and (text_scope or u.scope or {}).get("kind") != "label")
            unsure_subject = u.intent == "count" and u.subject_conf < MIN_CONFIDENCE
            if (not u.confident or u.intent == "other" or unsure_subject
                    or (u.intent == "count" and not u.subject) or vague_rooms):
                return await _agent(svc, d, question, lang, caller, stats, history, tag)
            if u.intent == "count" and u.subject:
                intent, subject = "count", u.subject
            elif u.intent == "area":
                intent = "area"
            elif u.intent == "length":
                if d.ctx.printed_dimensions:                 # the store read dimensions printed on the sheet
                    return await _agent(svc, d, question, lang, caller, stats, history, tag)
                return A.abstain_topic(question, lang, "length", intent_tag=tag)
            elif u.intent == "text":
                return A.abstain(
                    question, lang, {"kind": "floor"}, "not_supported_yet", "(a list or text fact)",
                    "Lists and other non-numeric facts are not extracted from this drawing yet.",
                    intent_body="text · not extracted yet", intent_tag=tag)
            else:  # pragma: no cover - every intent option is handled above
                return A.unclear(question, lang, d, intent_tag=tag)
            if text_scope is None and u.scope and u.scope_conf >= MIN_CONFIDENCE:
                text_scope = u.scope
        else:
            return A.unclear(question, lang, d)

    scope = req_scope or text_scope or {"kind": "floor"}
    built, _ = await _execute_exact_intent(
        d,
        question,
        lang,
        intent,
        subject,
        scope,
        overrides,
        r.matched,
        tag=tag,
        conf=conf,
        pattern=r.pattern,
        topic=r.topic,
        ref=ref,
    )
    return built


async def _check(svc: Services, d: tools.LoadedDrawing, built: A.Built, caller: Caller,
                 stats: dict) -> dict | None:
    if svc.settings.answerer == "exact":
        return None
    subject = built.subject
    if not built.check_ids or subject not in BOX_SUBJECTS:
        return None

    is_agent_est = built.body.get("method") == "agent_estimate"
    if is_agent_est:
        if (built.body.get("scope") or {}).get("kind") == "project":
            return None
        boxes = list((built.body.get("evidence") or {}).get("boxes") or [])
        if not boxes or len(boxes) > 30:
            return None
        ids = [f"{b['id']}@{b['x']},{b['y']}" for b in boxes]
        key = check_key(d.id, f"agent:{subject}", ids)
    else:
        layer = await asyncio.to_thread(tools.count_objects, d, subject, {"kind": "floor"})
        boxes = [b.model_dump() for b in layer.boxes]
        ids = [b["id"] for b in boxes]
        key = check_key(d.id, subject, ids)

    full = svc.checks.get(key, None if is_agent_est else d.dir, subject)
    cached = full is not None
    if full is None and d.image_path() is None and (d.source == "upload"
                                                     or d.drawing.get("kind") == "ifc"):
        if not is_agent_est:
            stats["no_image"] = True
        return None
    if full is None:
        image = d.image_path()
        if image is None or not svc.models_usable():
            return None

        def recently_failed() -> bool:
            t = svc.check_failures.get(key)
            return t is not None and time.time() - t < CHECK_RETRY_AFTER_S

        if recently_failed():
            stats["check_note"] = "a check of this layer failed a moment ago; not retried yet"
            stats["no_cache"] = True  # the check-less answer must not stick after the pause
            return None  # M5: a slow or failing check is not re-sent on every ask
        if svc.agent is not None and built.body.get("method") == "cad_geometry":
            # Hybrid answerer: an exact answer never waits on a model. The first image check of a
            # layer runs in the background and fills the check cache; this answer goes out now.
            if key not in _BG_CHECK_KEYS:
                _BG_CHECK_KEYS.add(key)
                bg_stats = {"check_meter": Meter(), "check_calls": 0, "check_cost_usd": 0.0,
                            "check_tokens": 0}
                bg_built = A.Built(built.body, built.results, list(ids), subject)
                task = asyncio.create_task(_check_in_background(svc, d, bg_built, caller, bg_stats, key))
                _BG_CHECKS.add(task)
                task.add_done_callback(_BG_CHECKS.discard)
            stats["check_note"] = "the image check of this layer is running in the background"
            stats["no_cache"] = True  # so the next ask picks up the check
            return None
        return await _check_inline(svc, d, built, caller, stats, key, boxes, ids, is_agent_est, image)
    return _slice(d, full, built, stats, is_agent_est, cached)


_BG_CHECKS: set[asyncio.Task] = set()
_BG_CHECK_KEYS: set[str] = set()


async def _check_in_background(svc: Services, d: tools.LoadedDrawing, built: A.Built, caller: Caller,
                               stats: dict, key: str) -> None:
    try:
        layer = await asyncio.to_thread(tools.count_objects, d, built.subject or "", {"kind": "floor"})
        boxes = [b.model_dump() for b in layer.boxes]
        image = d.image_path()
        if image is not None:
            await _check_inline(svc, d, built, caller, stats, key, boxes, [b["id"] for b in boxes],
                                False, image)
    except Exception:  # a background check must never take the server down
        log.exception("background image check failed")
    finally:
        _BG_CHECK_KEYS.discard(key)


async def _check_inline(svc: Services, d: tools.LoadedDrawing, built: A.Built, caller: Caller,
                        stats: dict, key: str, boxes: list[dict], ids: list[str], is_agent_est: bool,
                        image) -> dict | None:
    subject = built.subject or ""

    def recently_failed() -> bool:
        t = svc.check_failures.get(key)
        return t is not None and time.time() - t < CHECK_RETRY_AFTER_S

    lock = svc.check_locks.setdefault(key, asyncio.Lock())
    async with lock:  # concurrent first asks share one request
        full = svc.checks.get(key, None if is_agent_est else d.dir, subject)
        cached = full is not None
        if full is None and recently_failed():  # failed while this ask waited for the lock
            stats["check_note"] = "a check of this layer failed a moment ago; not retried yet"
            stats["no_cache"] = True
            return None
        if full is None:
            anchors = d.anchors or default_anchors(d.drawing.get("rooms", []), boxes)
            cm: Meter = stats["check_meter"]
            try:
                async with asyncio.timeout(MODEL_STEP_TIMEOUT_S):
                    full = await run_check(
                        svc.client,  # type: ignore[arg-type]
                        drawing_id=d.id, image_path=image, subject=subject, boxes=boxes,
                        anchors=anchors, ip_hash=caller.ip_hash, presenter=caller.presenter,
                        meter=cm)
            except (BudgetError, GeminiError, TimeoutError) as e:
                log.warning("check skipped: %s", e or "timeout")
                if not isinstance(e, BudgetError):
                    svc.check_failures[key] = time.time()
                stats["check_note"] = str(e) or "timeout"
                stats["no_cache"] = True
                _settle_check_cost(stats)
                if cm.calls:  # sent but unusable: no numbers, but its cost is reported
                    return {"method": "decisions_api", "status": "sample",
                            "image": "upload" if d.drawing.get("kind") == "raster"
                            else "dataset_png", **_check_cost(stats, False)}
                return None
            full["key"] = key
            svc.checks.put(key, d.id, subject, full)
            svc.check_failures.pop(key, None)
    svc.check_locks.pop(key, None)  # R3: the result is cached; the lock is not needed again
    return _slice(d, full, built, stats, is_agent_est, cached)


def _slice(d: tools.LoadedDrawing, full: dict, built: A.Built, stats: dict, is_agent_est: bool,
           cached: bool) -> dict | None:
    vision = d.drawing.get("kind") == "raster" or is_agent_est
    sliced = slice_check(full, built.check_ids, image="upload" if d.drawing.get("kind") == "raster" else "dataset_png")
    if sliced is not None and vision:
        # On raster / agent_estimate the check only ranks boxes for the user; it never filters or "agrees"
        sliced.pop("agreed", None)
    if sliced is None:
        return None
    stats["check_unscored"] = sliced.pop("unscored", 0)  # told in the trace, not a contract field
    _settle_check_cost(stats)
    return sliced | _check_cost(stats, cached)


def _settle_check_cost(stats: dict) -> None:
    cm: Meter = stats["check_meter"]
    stats["check_calls"] = cm.calls
    stats["check_cost_usd"] = cm.cost_usd
    stats["check_tokens"] = cm.tokens


def _check_cost(stats: dict, cached: bool) -> dict:
    return {"calls": stats["check_calls"], "cost_usd": round(stats["check_cost_usd"], 8),
            "tokens": stats["check_tokens"], "cached": cached}


def _check_node(check: dict, cached: bool, stats: dict, subject: str) -> dict:
    lc = check["least_certain_id"]
    p_lc = check["per_box"][lc]
    agreed = check.get("agreed")
    total = check["total"]
    image = "uploaded image" if check.get("image") == "upload" else "dataset image"
    parts = [(f"Gemini Decisions looked at {total} drawn {A.noun(subject, total)} as zoomed tiles "
              f"of the {image}.")]
    if stats.get("check_unscored"):
        parts.append(f"{stats['check_unscored']} more drawn {A.noun(subject, 2)} got no score from "
                     "the model and are not ranked.")
    if agreed is None and check.get("image") == "upload":
        parts.append("On an image the check only ranks the boxes; it removes none.")
    elif agreed is None:
        parts.append("No known-negative anchors could be placed, so no box is marked agreed.")
    else:
        parts.append(f"{agreed} of {total} agreed (score above the known-negative anchors, "
                     f"{check['anchors_max']:.2f}, by 0.05).")
    parts.append(f"Least certain: {lc} at {p_lc:.2f}; check that one first.")
    parts.append("The check covers drawn boxes only; it cannot find missed objects.")
    if cached:
        parts.append("Cached check: 0 model calls for this answer.")
    else:
        parts.append(f"1 model call, {stats['check_tokens']} tokens, ${stats['check_cost_usd']:.4f}.")
    return {"id": "check", "parent": "q", "title": "Cross-check on the image",
            "tag": "Decisions API · " + ("cached" if cached else "live"),
            "body": " ".join(parts), "kind": "model", "confidence": round(p_lc, 3),
            "evidence_ids": check["ranking"]}


async def _finalize_and_emit(
    svc: Services,
    d: tools.LoadedDrawing,
    req: AskRequest,
    built: A.Built,
    caller: Caller,
    stats: dict,
    meters: dict[str, Meter],
    t0: float,
    cache_key: str,
    *,
    allow_cache: bool,
) -> AsyncIterator[tuple[str, dict]]:
    A.honest_method(built.body, d, built.subject)  # a scan's geometry is never presented as CAD
    check = await _check(svc, d, built, caller, stats)
    body = built.body
    if check is not None and "ranking" not in check:  # sent, but no usable result
        body["check"] = check
        for n in body["trace"]:
            if n["id"] == "check":
                n.update(tag="Decisions API · failed", kind="warn",
                         body=f"{check['calls']} check request sent, no usable answer "
                              f"({stats.get('check_note')}); no box is marked checked.")
                n.pop("confidence", None)
    elif check is not None:
        body["check"] = check
        node = _check_node(check, stats["check_calls"] == 0, stats, built.subject or "")
        body["trace"] = [node if n["id"] == "check" else n for n in body["trace"]]
        yield "check", {"check": check}
    elif stats.get("no_image"):
        body["check"] = {"method": "decisions_api", "status": "sample", "image": "none"}
        for n in body["trace"]:
            if n["id"] == "check":
                n.update(tag="no image",
                         body="No independent image to check against: this drawing has only its "
                              "own geometry, and checking our own rendering would be circular.")
                n.pop("confidence", None)
                n["kind"] = "info"
    elif stats.get("check_note"):
        for n in body["trace"]:
            if n["id"] == "check":
                n["body"] = f"The image check was skipped: {stats['check_note']}."
                n["tag"] = "Decisions API · skipped"

    body["calls"] = meters["answer"].calls  # every request sent for the answer, failed or not
    body["cost_usd"] = round(meters["answer"].cost_usd, 8)
    body["trace"] = _with_decision(body["trace"], stats.get("decision_node"))
    for n in body["trace"]:
        if n.get("stage") == "answer" and isinstance(n.get("detail"), dict):
            n["detail"] |= {"calls": body["calls"], "cost_usd": body["cost_usd"]}
        if isinstance(n.get("detail"), dict):
            n["detail"] = _no_nulls(n["detail"])
    if svc.settings.trace_detail == "demo":
        body["trace"] = [{k: v for k, v in n.items() if k != "detail"} for n in body["trace"]
                         if n.get("stage") not in ("resources", "filter")]
    body["route"] = stats["route"]
    body["answer_id"] = "a-" + uuid.uuid4().hex
    body["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    resp = AskResponse.model_validate(body)  # contract gate: fails loudly on a bad body
    out = with_unknown_areas(resp.model_dump(exclude_none=True))
    _persist(svc, d.id, req.question, out, stats)
    # An abstention reached after a budget / rate-limit / presenter / models-off refusal is
    # temporary: it is never stored for replay, whichever step swallowed the refusal.
    refused = bool(stats.get("refusals")) and (out.get("answer") or {}).get("kind") == "abstain"
    if allow_cache and not stats.get("no_cache") and not refused:
        cached_body = out
        if check is not None and "ranking" in check and stats["check_calls"]:
            node = _check_node(check, True, stats, built.subject or "")
            cached_body = out | {
                "trace": [node if n["id"] == "check" else n for n in out["trace"]],
                "check": out["check"] | {"calls": 0, "cost_usd": 0.0, "tokens": 0, "cached": True}}
        svc.answers.put(
            cache_key,
            cached_body,
            drawing_id=d.id,
            question=req.question,
        )
    yield "answer", {"response": out}


async def run(svc: Services, req: AskRequest, caller: Caller, context: dict | None = None,
              history: list[dict] | None = None,
              meters: dict[str, Meter] | None = None,
              deadline_s: float | None = None,
              agent_deadline_s: float | None = None) -> AsyncIterator[tuple[str, dict]]:
    """`meters` ({'answer', 'check'}) lets a caller read what was spent even if this
    generator is cancelled at the turn deadline. `agent_deadline_s` (streaming /chat only)
    replaces the turn deadline once the turn escalates to the sidecar agent."""
    meters = meters if meters is not None else {"answer": Meter(), "check": Meter()}
    turn_deadline = deadline_s if deadline_s is not None else float(svc.settings.turn_deadline_s)
    t0 = time.perf_counter()
    ensure_ready(svc, req.drawing_id)
    lib = svc.registry.store_library
    if lib is not None and req.drawing_id.startswith("st-"):
        # a store-library drawing and its sibling storeys are built off the event loop (also for MCP)
        await asyncio.to_thread(lib.warm, req.drawing_id)
    d = svc.registry.get(req.drawing_id)
    if d is None:
        raise PipelineError("unknown_drawing", f"Unknown drawing '{req.drawing_id}'", 404)

    question = req.question.strip()
    scope_dump = req.scope.model_dump(exclude_none=True) if req.scope else None
    ov_dump = [o.model_dump() for o in req.overrides] if req.overrides else None
    # The drawings' data version is part of the key: a re-imported drawing (or any floor of an all-floors scope)
    # must not keep answering from the old data out of the cache.
    key = (answer_key(d.id, question, scope_dump, ov_dump, req.lang_hint, models_enabled())
           + f"|router:{ROUTER_VERSION}|data:{tools.data_version(d, scope_dump)}")
    prev_resp = last_turn_response(history)
    r_pre = route(question, d.ctx)
    has_followup_semantics = prev_resp is not None and (
        is_followup_message(question, r_pre)
        or (prev_resp.get("method") == "agent_estimate" and bool(FOLLOW_UP_CUE.search(question)))
    )
    if has_followup_semantics and prev_resp is not None:
        key += f"|prev:{prev_resp.get('answer_id', '')}"
    elif context:
        key += json.dumps(context, sort_keys=True)

    def replay(cached: dict) -> list[tuple[str, dict]]:
        # A fresh answer id, persisted: its CSV link and /stats see this answer too.
        body = dict(cached) | {"route": "cache", "calls": 0, "cost_usd": 0.0,
                               "answer_id": "a-" + uuid.uuid4().hex,
                               "latency_ms": int((time.perf_counter() - t0) * 1000)}
        _persist(svc, d.id, req.question, body, {"check_calls": 0, "check_cost_usd": 0.0})
        evs: list[tuple[str, dict]] = [("understood", {"detected_lang": body["detected_lang"],
                                                       "canonical_en": body["canonical_en"], "route": "cache"})]
        if "check" in body:
            evs.append(("check", {"check": body["check"]}))
        return [*evs, ("answer", {"response": body})]

    hit = svc.answers.get(key)
    deferred_agent_hit: dict | None = None
    if hit is not None and hit.get("method") == "agent_estimate":
        if r_pre.intent in EXACT_INTENTS:
            hit = None  # an exact route always wins over a cached model estimate
        elif svc.agent is not None:
            # A cached sidecar estimate is replayed only once the cheaper, verifiable tool-step
            # tier has declined this question (step 4b), in place of a new sidecar run.
            deferred_agent_hit, hit = hit, None
    if hit is not None:
        for ev in replay(hit):
            yield ev
        return

    stats = {"refusals": watch_refusals(),
             "route": "template", "check_calls": 0, "check_cost_usd": 0.0, "check_tokens": 0,
             "meter": meters["answer"], "check_meter": meters["check"]}

    # ---- Sidecar-enabled `hybrid` / `agent` modes ----
    mode = svc.settings.answerer
    # The single answering path: router -> exact -> gate -> tool steps -> (sidecar, only when one is
    # configured) -> abstain. Without a sidecar it runs in full mode (the drawing store is on: the
    # merged backend). Quick mode without a sidecar (e.g. Modal) keeps the legacy path below, which
    # has its own choice step, in-backend agent and image scoring.
    full = svc.drawing_store is not None and svc.drawing_store.available()
    if mode in ("hybrid", "agent") and (svc.agent is not None or full):
        lang = r_pre.lang if r_pre.lang_signal else (req.lang_hint or "en")

        # 1. `<2ms` deterministic color-coding follow-up on prior rooms (`0` model calls)
        if (color_built := _try_deterministic_color_followup(question, lang, d, history)) is not None:
            yield "understood", {"detected_lang": color_built.body["detected_lang"],
                                 "canonical_en": color_built.body["canonical_en"], "route": "template"}
            for ev in _tool_events(color_built):
                yield ev
            async for ev in _finalize_and_emit(svc, d, req, color_built, caller, stats, meters, t0, key, allow_cache=True):
                yield ev
            return

        # 1b. A plain length question ("overall width", "how wide is the kitchen", "longest printed dimension")
        # answered from the printed dimensions, else the measured outline: 0 model calls, always a length.
        if not req.overrides and (scope_dump is None or scope_dump.get("kind") == "floor"):
            dim_built = dimension_answers.try_answer(question, lang, d)
            if dim_built is not None:
                yield "understood", {"detected_lang": dim_built.body["detected_lang"],
                                     "canonical_en": dim_built.body["canonical_en"], "route": "template"}
                for ev in _tool_events(dim_built):
                    yield ev
                async for ev in _finalize_and_emit(svc, d, req, dim_built, caller, stats, meters, t0, key, allow_cache=True):
                    yield ev
                return

        r = route(question, d.ctx)
        if context and not has_followup_semantics:
            short = len(question.split()) <= 5
            cue = short and bool(FOLLOW_UP_CUE.search(question))
            if WHOLE_FLOOR.search(question) and r.intent in ("count", "area") and r.scope is None:
                r.scope = {"kind": "floor"}
            if (r.intent == "unknown" and cue and (r.scope or req.scope)
                    and context["intent"] in ("count", "area")):
                r.intent, r.subject = context["intent"], context.get("subject")
                r.matched.append("follow-up")
            elif r.intent in ("count", "area") and r.scope is None and cue and context.get("scope"):
                r.scope = context["scope"] if context["scope"].get("kind") != "region" else None
                r.matched.append("follow-up")

        ref = resolve_reference(r, question, history, d)
        if ref is not None:
            has_followup_semantics = False  # the reference is resolved; answer it exactly
        eff_scope = scope_dump or r.scope or {"kind": "floor"}
        overrides = [o.model_dump() for o in req.overrides or []]
        is_project = eff_scope.get("kind") == "project"
        dkind = d.drawing.get("kind")
        # A PDF/image the drawing store indexed answers exact templates from its index like CAD.
        indexed = dkind in ("cad_dxf", "ifc") or bool(d.drawing.get("evidence_method"))
        is_pdf = d.drawing.get("source_ext") == ".pdf"
        is_deictic_region = eff_scope.get("kind") == "region" and bool(DEICTIC_SIMILARITY.search(question))

        # 2. Hybrid mode exact fast-path (`<2ms`, `0` model calls) when router matches cleanly
        # A count across a building's floors is exact too (each floor counted, then summed): no model needed.
        # ... and so is the net floor area across them (each floor summed by the one-floor rule, then added).
        exact_project = is_project and (r.intent == "count" or (
            r.intent == "area" and (r.scope or {}).get("kind") in (None, "floor", "model")))
        if mode == "hybrid" and (not is_project or exact_project) and not has_followup_semantics:
            if (indexed and r.intent in (
                    "count", "area", "quantity", "find_text", "room_rank", "adjacent")) or (
                    # Detected image/PDF page: door/window counts (and show/list variants) come from
                    # raster.json at 0 calls, as a model estimate; never the sidecar.
                    isinstance(d.engine, tools.RasterEngine) and r.intent == "count"
                    and d.engine.supports(r.subject or "")):
                tag = ("router · reference" if ref else
                       "router · follow-up" if "follow-up" in r.matched else "router")
                exact_built, ok_exact = await _execute_exact_intent(
                    d, question, lang, r.intent, r.subject, eff_scope, overrides, r.matched,
                    tag=tag, pattern=r.pattern, topic=r.topic, ref=ref,
                )
                if ok_exact or not models_enabled() or not svc.budget.can_spend():
                    yield "understood", {"detected_lang": exact_built.body["detected_lang"],
                                         "canonical_en": exact_built.body["canonical_en"], "route": "template"}
                    for ev in _tool_events(exact_built):
                        yield ev
                    async for ev in _finalize_and_emit(svc, d, req, exact_built, caller, stats, meters, t0, key, allow_cache=True):
                        yield ev
                    return
            elif (dkind == "cad_dxf" and r.intent == "abstain" and r.topic in ("height", "material")
                  and not (svc.models_usable() and d.drawing.get("rooms"))):
                # 0-call topic abstain only when the tool steps cannot run; otherwise their reasoning
                # step gets the question first and can say what is missing or compute around it.
                abs_built = A.abstain_topic(question, lang, r.topic, intent_tag="router")
                yield "understood", {"detected_lang": abs_built.body["detected_lang"],
                                     "canonical_en": abs_built.body["canonical_en"], "route": "template"}
                async for ev in _finalize_and_emit(svc, d, req, abs_built, caller, stats, meters, t0, key, allow_cache=True):
                    yield ev
                return

        # 3. Check model kill-switch / budget before Decision gate or Sidecar run
        if not models_enabled() or not svc.budget.can_spend():
            stats["no_cache"] = True
            off_built = A.abstain(
                question,
                lang,
                eff_scope,
                "not_supported_yet",
                question,
                "Models are off or the event budget is exhausted; only exact pre-indexed geometry questions are available.",
                intent_tag="router · hybrid",
            )
            yield "understood", {"detected_lang": off_built.body["detected_lang"],
                                 "canonical_en": off_built.body["canonical_en"], "route": "template"}
            async for ev in _finalize_and_emit(svc, d, req, off_built, caller, stats, meters, t0, key, allow_cache=False):
                yield ev
            return

        # 4. Calibrated `decide_hybrid_route` gate (`on_drawing`, `exact_sufficient`, qualifier-strict `cache_match`)
        hard_guard = (
            mode == "agent"
            or is_project
            or is_pdf
            or has_followup_semantics
            or is_deictic_region
            or r.topic == "length"
            or bool({"qualifier_guard", "stair_escalate", "deictic_similarity", "needs_model"} & set(r.matched))
        )
        check_exact = (not hard_guard) and indexed
        skip_on_drawing = has_followup_semantics or is_deictic_region
        cached_candidates = (
            [] if (has_followup_semantics or is_deictic_region or ov_dump)
            else svc.answers.entries_for_drawing(d.id, scope=scope_dump, overrides=ov_dump, models_on=models_enabled())
        )
        # Only answers written by the current router, and only real answers: an old abstain must
        # never come back as "the same question" once routing or the index improved.
        cur = f"|router:{ROUTER_VERSION}"
        cached_candidates = [(k, q) for k, q in cached_candidates if cur in k
                             and ((svc.answers.get(k) or {}).get("answer") or {}).get("kind") != "abstain"]

        intent_tag = f"router · {mode}"
        if svc.client is not None and svc.client.available():
            try:
                async with asyncio.timeout(MODEL_STEP_TIMEOUT_S):
                    hd = await decide_hybrid_route(
                        svc.client,
                        question,
                        drawing_kind=str(dkind or "raster"),
                        source_ext=d.drawing.get("source_ext"),
                        units=d.ctx.units,
                        labels=d.ctx.labels,
                        cached_entries=cached_candidates,
                        image_path=d.image_path(),
                        ip_hash=caller.ip_hash,
                        presenter=caller.presenter,
                        meter=meters["answer"],
                        check_exact=check_exact,
                        skip_on_drawing=skip_on_drawing,
                    )
                if hd.on_drawing is not None:
                    intent_tag = f"Decisions API · {hd.on_drawing:.2f}"
                stats["decision_node"] = decision_node(hd, "agent")  # refined below
                # On a drawing with an index the tool steps try first: the gate looks at a picture
                # and was measured to reject answerable filters ("rooms larger than 15.5 m²").
                if hd.off_drawing and not d.drawing.get("rooms"):
                    stats["decision_node"] = decision_node(hd, "abstain")
                    stats["route"] = "choice"
                    off_b = A.abstain(
                        question,
                        lang,
                        {"kind": "floor"},
                        "not_on_drawing",
                        "(off the drawing)",
                        f"The model judged this information is not shown on the drawing (p = {hd.on_drawing:.2f}).",
                        intent_body="abstain · not on drawing",
                        intent_tag=intent_tag,
                    )
                    yield "understood", {"detected_lang": off_b.body["detected_lang"],
                                         "canonical_en": off_b.body["canonical_en"], "route": "choice"}
                    async for ev in _finalize_and_emit(svc, d, req, off_b, caller, stats, meters, t0, key, allow_cache=True):
                        yield ev
                    return

                if hd.cache_key:
                    sem_hit = svc.answers.get(hd.cache_key)
                    if sem_hit is not None and (sem_hit.get("answer") or {}).get("kind") != "abstain":
                        sem_trace = _with_decision(
                            [n for n in sem_hit.get("trace") or [] if n.get("stage") != "decision"],
                            decision_node(hd, "cache"))
                        sem_trace = [n | ({"detail": _no_nulls(n["detail"])} if n.get("detail") else {})
                                     for n in sem_trace]
                        sem_trace.append({
                            "id": "semantic-cache",
                            "parent": "q",
                            "title": "Semantic cache match",
                            "tag": f"Decisions API · {hd.cache_conf:.2f}",
                            "body": f"Matched to an earlier verified question on this drawing (confidence {hd.cache_conf:.2f}).",
                            "kind": "model",
                        })
                        body = dict(sem_hit) | {
                            "route": "cache",
                            "calls": meters["answer"].calls,
                            "cost_usd": round(meters["answer"].cost_usd, 8),
                            "answer_id": "a-" + uuid.uuid4().hex,
                            "latency_ms": int((time.perf_counter() - t0) * 1000),
                            "trace": sem_trace,
                        }
                        _persist(svc, d.id, req.question, body, {"check_calls": 0, "check_cost_usd": 0.0})
                        yield "understood", {"detected_lang": body["detected_lang"],
                                             "canonical_en": body["canonical_en"], "route": "cache"}
                        if "check" in body:
                            yield "check", {"check": body["check"]}
                        yield "answer", {"response": body}
                        return

                if hd.can_use_exact and hd.intent in ("count", "area"):
                    ex_scope = hd.scope if (scope_dump is None and hd.scope and hd.scope_conf >= MIN_CONFIDENCE) else eff_scope
                    ex_built, ex_ok = await _execute_exact_intent(
                        d,
                        question,
                        lang,
                        hd.intent,
                        hd.subject,
                        ex_scope,
                        overrides,
                        r.matched,
                        tag=f"Decisions API · {hd.exact_sufficient:.2f}",
                        conf=hd.exact_sufficient,
                    )
                    if ex_ok:
                        stats["route"] = "choice"
                        stats["decision_node"] = decision_node(hd, "exact")
                        yield "understood", {"detected_lang": ex_built.body["detected_lang"],
                                             "canonical_en": ex_built.body["canonical_en"], "route": "choice"}
                        for ev in _tool_events(ex_built):
                            yield ev
                        async for ev in _finalize_and_emit(svc, d, req, ex_built, caller, stats, meters, t0, key, allow_cache=True):
                            yield ev
                        return
            except (BudgetError, GeminiError, TimeoutError) as e:
                log.warning("decision gate skipped (%s); continuing with the tool steps", e or "timeout")

        # 4b. Tool-step answerer: fixed read-only tools over the indexed drawing, a few model calls, no generated code.
        #     Returns None when it cannot answer; only then does the turn go to the sidecar agent.
        # A store-library drawing without rooms (a DWG, an uninterpreted scan) still has the sql tool.
        if (svc.models_usable() and (d.drawing.get("rooms") or getattr(d, "store_owned", False) is True)
                and not is_project and not is_deictic_region):
            gate_node = stats.get("decision_node")
            tools_built: A.Built | None = None
            try:
                tools_built = await answer_with_tools(svc, d, question, lang, caller, stats, history, eff_scope)
            except Exception:  # the tool path must never take the turn down; the agent still runs
                log.exception("tool-step answerer failed; escalating to sidecar agent")
            if tools_built is not None:
                ans = tools_built.body.get("answer") or {}
                if ans.get("kind") == "abstain" and r.intent == "abstain" and r.topic:
                    # a known off-plan topic (ceiling height, material): the tools and their reasoning
                    # step tried; the answer keeps the topic's reason and where to find it
                    tools_built.body["answer"] = A.abstain_topic(question, lang, r.topic).body["answer"]
                    ans = tools_built.body["answer"]
                elif ans.get("kind") == "abstain" and ans.get("reason") == "not_on_drawing":
                    # The rule: not_on_drawing only when the tools were NOT tried (router topic or
                    # a confident gate on a drawing without an index); tried and empty = not_answerable.
                    ans["reason"] = "not_answerable"
                if gate_node:
                    tools_built.body["trace"] = [gate_node, *tools_built.body.get("trace", [])]
                yield "understood", {"detected_lang": tools_built.body["detected_lang"],
                                     "canonical_en": tools_built.body["canonical_en"], "route": stats["route"]}
                for ev in _tool_events(tools_built):
                    yield ev
                # An answer shaped by earlier turns must not be cached under a key that ignores them.
                async for ev in _finalize_and_emit(svc, d, req, tools_built, caller, stats, meters, t0, key,
                                                   allow_cache=not history):
                    yield ev
                return

        if deferred_agent_hit is not None:  # tools declined: the cached sidecar answer stands
            for ev in replay(deferred_agent_hit):
                yield ev
            return

        if svc.agent is None and r.intent == "abstain" and r.topic:
            # the router knew the topic (height, material): tools tried, the topic's own abstain
            # with where to find it is the honest last word
            stats["no_cache"] = True
            topic_built = A.abstain_topic(question, lang, r.topic, intent_tag=intent_tag)
            yield "understood", {"detected_lang": topic_built.body["detected_lang"],
                                 "canonical_en": topic_built.body["canonical_en"], "route": stats["route"]}
            async for ev in _finalize_and_emit(svc, d, req, topic_built, caller, stats, meters, t0, key,
                                               allow_cache=False):
                yield ev
            return
        if svc.agent is None:  # no sidecar: what the drawing's data cannot answer is said plainly
            stats["no_cache"] = True
            nothing = A.abstain(
                question, lang, eff_scope, "not_answerable", question,
                "Tried: the exact templates and the tool steps over this drawing's indexed rooms, "
                "openings and texts. None of them gives this answer.",
                intent_body="abstain · not answerable from the drawing's data",
                intent_tag=intent_tag)
            nothing.body["trace"][-1]["body"] = "I cannot answer this from the drawing's data."
            yield "understood", {"detected_lang": nothing.body["detected_lang"],
                                 "canonical_en": nothing.body["canonical_en"], "route": stats["route"]}
            async for ev in _finalize_and_emit(svc, d, req, nothing, caller, stats, meters, t0, key,
                                               allow_cache=False):
                yield ev
            return

        # 5. Escalate directly to `agent_client` (`deep_agent`) — NEVER fall back to legacy `understand` or `flash-lite`!
        agent_deadline = max(turn_deadline, agent_deadline_s or 0.0)
        remaining_s = max(5.0, agent_deadline - (time.perf_counter() - t0))
        agent_built: A.Built | None = None
        understood_emitted = False
        async for ev_name, ev_data in svc.agent.run_turn(
            d=d,
            question=question,
            lang=lang,
            scope=eff_scope,
            caller=caller,
            stats=stats,
            history=history if has_followup_semantics else None,
            deadline_s=remaining_s,
            intent_tag=intent_tag,
        ):
            if ev_name == "_ready":
                understood_emitted = True
                yield "understood", ev_data
            elif ev_name == "_built":
                agent_built = ev_data
            else:
                yield ev_name, ev_data

        if agent_built is None:
            raise PipelineError("internal", "Agent turn ended without a result.", 500)
        if not understood_emitted:
            yield "understood", {"detected_lang": agent_built.body["detected_lang"],
                                 "canonical_en": agent_built.body["canonical_en"], "route": stats["route"]}
        async for ev in _finalize_and_emit(
            svc,
            d,
            req,
            agent_built,
            caller,
            stats,
            meters,
            t0,
            key,
            allow_cache=True,
        ):
            yield ev
        return

    # ---- Legacy / unit-test in-process path (`svc.agent is None` or `answerer == 'exact'`) ----
    built = await _decide(svc, req, d, caller, stats, context, history)
    yield "understood", {"detected_lang": built.body["detected_lang"],
                         "canonical_en": built.body["canonical_en"], "route": stats["route"]}
    for ev in _tool_events(built):
        yield ev
    allow_cache = not (stats["route"] == "agent" and history)
    async for ev in _finalize_and_emit(svc, d, req, built, caller, stats, meters, t0, key, allow_cache=allow_cache):
        yield ev


def _persist(svc: Services, drawing_id: str, question: str, body: dict, stats: dict) -> None:
    if svc.writer is None:
        return
    now = time.time()
    svc.writer.submit(
        "INSERT INTO answers(id, t, day, drawing_id, question, route, method, calls, cost_usd, "
        "check_calls, check_cost_usd, response) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (body["answer_id"], now, svc.budget.day(now), drawing_id, question, body.get("route"),
         body["method"], body["calls"], body["cost_usd"], stats["check_calls"],
         stats["check_cost_usd"], json.dumps(body, ensure_ascii=False)),
    )


def with_units(base_run):
    """Wrap a `run`-shaped generator so a question may ask for a unit ("in square feet").

    The one place units are handled: the wrapped run (any route: exact, template, tool steps,
    cache replay, building totals) answers the metric twin of the question, then the finished
    response gets its `display` block. The cache therefore only ever holds metric answers.
    "and in inches?" re-shows the previous turn's answer without running anything.
    """
    async def run_units(svc: Services, req: AskRequest, caller: Caller, *args, **kw):
        up = units.plan(req.question)
        # Earlier turns are passed on in metric too (args: context, history, ...).
        if len(args) > 1:
            history = units.metric_turns(args[1])
            args = (args[0], history, *args[2:])
        else:
            history = units.metric_turns(kw.get("history"))
            if "history" in kw:
                kw["history"] = history
        if up.unit is None and not up.changed:
            async for item in base_run(svc, req, caller, *args, **kw):
                yield item
            return
        prev = last_turn_response(history)
        if up.pure and prev is not None and units.has_measure(prev):
            t0 = time.perf_counter()
            body = units.apply(prev, up.unit) | {
                "route": "template", "calls": 0, "cost_usd": 0.0, "answer_id": "a-" + uuid.uuid4().hex}
            for k in ("conversation_id", "turn_id"):
                body.pop(k, None)
            body["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            _persist(svc, req.drawing_id, req.question, body, {"check_calls": 0, "check_cost_usd": 0.0})
            yield "understood", {"detected_lang": body["detected_lang"],
                                 "canonical_en": body["canonical_en"], "route": "template"}
            yield "answer", {"response": body}
            return
        metric_req = req.model_copy(update={"question": up.question}) if up.changed else req
        async for event, data in base_run(svc, metric_req, caller, *args, **kw):
            if event == "answer" and up.unit is not None:
                data = {"response": units.apply(data["response"], up.unit)}
            yield event, data

    return run_units


async def ask(svc: Services, req: AskRequest, caller: Caller,
              meters: dict[str, Meter] | None = None, run_fn=None) -> dict:
    # `run_fn`: a drop-in for `run` (whole-building answers, aura/building.py); default `run`.
    async for event, data in with_units(run_fn or run)(svc, req, caller, meters=meters,
                                             deadline_s=float(svc.settings.turn_deadline_s)):
        if event == "answer":
            return data["response"]
    raise PipelineError("internal", "The pipeline ended without an answer.", 500)


async def chat_events(svc: Services, body: ChatRequest, caller: Caller,
                      deadline_s: float, run_fn=None) -> AsyncIterator[tuple[str, dict]]:
    """/chat: turn, progress*, understood, (tool_call, tool_result)*, check?, answer | error, done.

    The pipeline runs in its own task so the turn deadline is exact; on timeout the task is
    cancelled and the stream ends with error {code: 'timeout'} then done. Every `svc.heartbeat_s`
    (15s) of idle waiting emits `(":progress", {"seconds": elapsed})` so proxies and the browser
    idle watchdog keep long deep-agent runs alive.
    """
    store, writer = svc.store, svc.writer
    existing = store.conversation(body.conversation_id) if store and body.conversation_id else None
    # L4: ids are issued by the server; an unknown client-chosen id starts a NEW conversation.
    conv_id = body.conversation_id if existing is not None else "c-" + uuid.uuid4().hex
    turn_id = "t-" + uuid.uuid4().hex[:16]
    now = time.time()
    if writer is not None and existing is None:
        await writer.execute("INSERT OR IGNORE INTO conversations(id, t, drawing_id) VALUES (?,?,?)",
                             (conv_id, now, body.drawing_id))
    yield "turn", {"conversation_id": conv_id, "turn_id": turn_id}

    history = store.recent_turns(conv_id) if store and existing else []
    context = context_from(history)
    req = AskRequest(drawing_id=body.drawing_id, question=body.message, scope=body.scope,
                     overrides=body.overrides, lang_hint=body.lang_hint)
    queue: asyncio.Queue = asyncio.Queue()

    meters = {"answer": Meter(), "check": Meter()}

    async def produce() -> None:
        try:
            async for item in with_units(run_fn or run)(svc, req, caller, context, history, meters,
                                              deadline_s=deadline_s,
                                              agent_deadline_s=svc.settings.agent_deadline_s):
                await queue.put(item)
        except PipelineError as e:
            await queue.put(("error", {"error": str(e), "code": e.code}))
        except Exception:
            log.exception("chat turn failed")
            await queue.put(("error", {"error": "Internal error.", "code": "internal"}))
        await queue.put(None)

    task = asyncio.create_task(produce(), name=f"chat-{turn_id}")
    loop = asyncio.get_running_loop()
    start_t = loop.time()
    deadline = start_t + deadline_s
    hb_s = max(0.02, float(svc.heartbeat_s))
    response: dict | None = None
    error: dict | None = None
    try:
        while True:
            rem = deadline - loop.time()
            if rem <= 0:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                spent = meters["answer"].calls + meters["check"].calls
                cost = round(meters["answer"].cost_usd + meters["check"].cost_usd, 8)
                error = {"error": "The answer took too long.", "code": "timeout",
                         "calls": spent, "cost_usd": cost}
                yield "error", error
                break
            slice_s = min(hb_s, rem)
            try:
                item = await asyncio.wait_for(queue.get(), slice_s)
            except TimeoutError:
                if loop.time() >= deadline:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)  # settle the meters
                    spent = meters["answer"].calls + meters["check"].calls
                    cost = round(meters["answer"].cost_usd + meters["check"].cost_usd, 8)
                    # Every request already sent is reported, even without an answer.
                    error = {"error": "The answer took too long.", "code": "timeout",
                             "calls": spent, "cost_usd": cost}
                    yield "error", error
                    break
                elapsed = max(1, round(loop.time() - start_t))
                yield ":progress", {"seconds": elapsed}
                continue
            if item is None:
                break
            event, data = item
            if event == "understood" and data.get("route") == "agent":
                # Agent runs take minutes, not seconds; heartbeats keep the stream alive meanwhile.
                deadline = start_t + max(deadline_s, float(svc.settings.agent_deadline_s))
            if event == "answer":
                response = data["response"] | {"conversation_id": conv_id, "turn_id": turn_id}
                data = {"response": response}
            elif event == "error":
                error = data
            yield event, data
    finally:
        if not task.done():
            task.cancel()
        if writer is not None:
            await writer.execute(  # committed before `done`, so a fast follow-up sees it
                "INSERT INTO turns(id, conversation_id, t, message, response, error) "
                "VALUES (?,?,?,?,?,?)",
                (turn_id, conv_id, time.time(), body.message,
                 json.dumps(response, ensure_ascii=False) if response else None,
                 json.dumps({"contract_version": AskResponse.model_fields["contract_version"].default,
                             **error}) if error else None))
    yield "done", {}
