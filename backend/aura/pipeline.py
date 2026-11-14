"""One pipeline for /ask and /chat: understand → tools → answer → check.

`run(...)` is an async generator of (event, data) pairs in the /chat order:
understood, (tool_call, tool_result)*, check?, answer | error. /ask collects it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from aura import answer as A
from aura import tools
from aura.agent.loop import MAX_ITERATIONS, AgentResult, describe_call, run_agent
from aura.budget import Budget, BudgetError
from aura.cache import AnswerCache, CheckCache, answer_key
from aura.config import Settings, models_enabled
from aura.contract import BOX_SUBJECTS, AskRequest, AskResponse, ChatRequest
from aura.gemini.check import check_key, default_anchors, run_check, slice_check
from aura.gemini.client import GeminiClient, GeminiError, Meter
from aura.gemini.understand import MIN_CONFIDENCE, understand
from aura.provenance import allowed_numbers, filter_prose
from aura.router import FIRE_PATTERN, route
from aura.store import Store
from aura.writer import Writer

log = logging.getLogger("aura.pipeline")

MODEL_STEP_TIMEOUT_S = 8.0  # one model step (choice or check); the agent has AGENT_TIMEOUT_S
CHECK_RETRY_AFTER_S = 600.0  # a failed check of one layer is not re-sent for 10 minutes


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
    """Last subject, intent and scope of a conversation, for short follow-ups."""
    for t in reversed(turns):
        resp = t.get("response") or {}
        ans = resp.get("answer") or {}
        if ans.get("kind") in ("count", "area"):
            return {"intent": ans["kind"], "subject": ans.get("subject"), "scope": resp.get("scope")}
    return None


FOLLOW_UP_CUE = re.compile(
    r"^\s*(and|what about|how about|same|et|und|ja|entä|e|ed|plus|also|then)\b", re.IGNORECASE)
WHOLE_FLOOR = re.compile(r"\b(in total|total|whole|entire|all|altogether|insgesamt|gesamt|au total|"
                         r"en tout|yhteensä|kaikki|in tutto)\b", re.IGNORECASE)


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


async def _decide(svc: Services, req: AskRequest, d: tools.LoadedDrawing, caller: Caller,
                  stats: dict, context: dict | None = None,
                  history: list[dict] | None = None) -> A.Built:
    question = req.question.strip()
    r = route(question, d.ctx)
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
    lang = r.lang if r.lang_signal else (req.lang_hint or "en")
    overrides = [o.model_dump() for o in req.overrides or []]
    req_scope = req.scope.model_dump(exclude_none=True) if req.scope else None
    intent, subject, text_scope, conf = r.intent, r.subject, r.scope, None
    tag = "router · follow-up" if "follow-up" in r.matched else "router"

    if intent == "unknown":
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

    if intent == "find_text":
        pattern = r.pattern or FIRE_PATTERN
        tr = await asyncio.to_thread(tools.find_text, d, pattern, subject or "label")
        if tr.text:
            return A.text(question, lang, d, tr, subject or "label", intent_tag=tag)
        n_texts = len(d.drawing.get("texts") or [])
        look = (f"Searched the printed text on this drawing for fire ratings (EI30, 30 MIN, T30): "
                f"none found{f' among {n_texts} texts' if n_texts else ''}.")
        return A.abstain_topic(question, lang, r.topic or "fire", intent_tag=tag, results=[tr],
                               look=look)

    if intent == "abstain":
        return A.abstain_topic(question, lang, r.topic or "material", intent_tag=tag)

    if intent == "quantity":
        tr = await asyncio.to_thread(tools.measure, d, subject or "",
                                     req_scope or text_scope or {"kind": "floor"})
        if not tr.supported or tr.kind == "none":
            return A.abstain(question, lang, tr.args["scope"], "not_supported_yet",
                             "(an IFC quantity)", tr.summary, intent_tag=tag, results=[tr])
        return A.quantity(question, lang, d, tr, tr.args["scope"], intent_tag=tag)

    scope = req_scope or text_scope or {"kind": "floor"}

    if intent == "count":
        subj = subject or "room"
        tr = await asyncio.to_thread(tools.count_objects, d, subj, scope)
        if not tr.supported or tr.meta.get("abstain") == "not_supported_yet":
            return A.not_supported(question, lang, scope, tr, intent_tag=tag)
        if tr.kind == "none":
            return A.unclear(question, lang, d, scope, intent_tag=tag)
        return A.count(question, lang, d, tr, scope, overrides, intent_tag=tag, intent_conf=conf)

    if not d.engine.supports("area"):
        return A.abstain(question, lang, scope, "not_supported_yet", "What is the net floor area",
                         "No rooms with areas are extracted from this drawing yet.",
                         intent_tag=tag)
    tr = await asyncio.to_thread(tools.room_areas, d, scope, overrides or None)
    if tr.kind == "none":
        return A.unclear(question, lang, d, scope, intent_tag=tag)
    return A.area(question, lang, d, tr, scope, overrides, intent_tag=tag, intent_conf=conf)


async def _check(svc: Services, d: tools.LoadedDrawing, built: A.Built, caller: Caller,
                 stats: dict) -> dict | None:
    subject = built.subject
    if not built.check_ids or subject not in BOX_SUBJECTS:
        return None
    layer = await asyncio.to_thread(tools.count_objects, d, subject, {"kind": "floor"})
    boxes = [b.model_dump() for b in layer.boxes]
    ids = [b["id"] for b in boxes]
    key = check_key(d.id, subject, ids)
    full = svc.checks.get(key, d.dir, subject)
    cached = full is not None
    if full is None and d.image_path() is None and (d.source == "upload"
                                                     or d.drawing.get("kind") == "ifc"):
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
        lock = svc.check_locks.setdefault(key, asyncio.Lock())
        async with lock:  # concurrent first asks share one request
            full = svc.checks.get(key, d.dir, subject)
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
    vision = d.drawing.get("kind") == "raster"
    sliced = slice_check(full, built.check_ids, image="upload" if vision else "dataset_png")
    if sliced is not None and vision:
        # On raster the check only ranks boxes for the user; it never filters or "agrees"
        # (the CubiCasa eval showed filtering removed real boxes).
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


async def run(svc: Services, req: AskRequest, caller: Caller, context: dict | None = None,
              history: list[dict] | None = None,
              meters: dict[str, Meter] | None = None) -> AsyncIterator[tuple[str, dict]]:
    """`meters` ({'answer', 'check'}) lets a caller read what was spent even if this
    generator is cancelled at the turn deadline."""
    meters = meters if meters is not None else {"answer": Meter(), "check": Meter()}
    t0 = time.perf_counter()
    ensure_ready(svc, req.drawing_id)
    d = svc.registry.get(req.drawing_id)
    if d is None:
        raise PipelineError("unknown_drawing", f"Unknown drawing '{req.drawing_id}'", 404)
    scope_dump = req.scope.model_dump(exclude_none=True) if req.scope else None
    ov_dump = [o.model_dump() for o in req.overrides] if req.overrides else None
    key = answer_key(d.id, req.question, scope_dump, ov_dump, req.lang_hint, models_enabled())
    if context:
        key += json.dumps(context, sort_keys=True)
    hit = svc.answers.get(key)
    if hit is not None:
        # A fresh answer id, persisted: its CSV link and /stats see this answer too.
        body = dict(hit) | {"route": "cache", "calls": 0, "cost_usd": 0.0,
                            "answer_id": "a-" + uuid.uuid4().hex,
                            "latency_ms": int((time.perf_counter() - t0) * 1000)}
        _persist(svc, d.id, req.question, body, {"check_calls": 0, "check_cost_usd": 0.0})
        yield "understood", {"detected_lang": body["detected_lang"],
                             "canonical_en": body["canonical_en"], "route": "cache"}
        if "check" in body:
            yield "check", {"check": body["check"]}
        yield "answer", {"response": body}
        return

    stats = {"route": "template", "check_calls": 0, "check_cost_usd": 0.0, "check_tokens": 0,
             "meter": meters["answer"], "check_meter": meters["check"]}
    built = await _decide(svc, req, d, caller, stats, context, history)
    yield "understood", {"detected_lang": built.body["detected_lang"],
                         "canonical_en": built.body["canonical_en"], "route": stats["route"]}
    for ev in _tool_events(built):
        yield ev

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
    body["route"] = stats["route"]
    body["answer_id"] = "a-" + uuid.uuid4().hex
    body["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    resp = AskResponse.model_validate(body)  # contract gate: fails loudly on a bad body
    out = with_unknown_areas(resp.model_dump(exclude_none=True))
    _persist(svc, d.id, req.question, out, stats)
    # The agent reads the conversation history, which is not in the cache key: a turn with
    # history is not cached on that route.
    if not stats.get("no_cache") and not (stats["route"] == "agent" and history):
        cached_body = out
        if check is not None and "ranking" in check and stats["check_calls"]:
            node = _check_node(check, True, stats, built.subject or "")
            cached_body = out | {
                "trace": [node if n["id"] == "check" else n for n in out["trace"]],
                "check": out["check"] | {"calls": 0, "cost_usd": 0.0, "tokens": 0, "cached": True}}
        svc.answers.put(key, cached_body)
    yield "answer", {"response": out}


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


async def ask(svc: Services, req: AskRequest, caller: Caller,
              meters: dict[str, Meter] | None = None) -> dict:
    async for event, data in run(svc, req, caller, meters=meters):
        if event == "answer":
            return data["response"]
    raise PipelineError("internal", "The pipeline ended without an answer.", 500)


async def chat_events(svc: Services, body: ChatRequest, caller: Caller,
                      deadline_s: float) -> AsyncIterator[tuple[str, dict]]:
    """/chat: turn, understood, (tool_call, tool_result)*, check?, answer | error, done.

    The pipeline runs in its own task so the turn deadline is exact; on timeout the task is
    cancelled and the stream ends with error {code: 'timeout'} then done.
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
            async for item in run(svc, req, caller, context, history, meters):
                await queue.put(item)
        except PipelineError as e:
            await queue.put(("error", {"error": str(e), "code": e.code}))
        except Exception:
            log.exception("chat turn failed")
            await queue.put(("error", {"error": "Internal error.", "code": "internal"}))
        await queue.put(None)

    task = asyncio.create_task(produce(), name=f"chat-{turn_id}")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_s
    response: dict | None = None
    error: dict | None = None
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), max(0.0, deadline - loop.time()))
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)  # settle the meters
                spent = meters["answer"].calls + meters["check"].calls
                cost = round(meters["answer"].cost_usd + meters["check"].cost_usd, 8)
                # Every request already sent is reported, even without an answer.
                error = {"error": "The answer took too long.", "code": "timeout",
                         "calls": spent, "cost_usd": cost}
                yield "error", error
                break
            if item is None:
                break
            event, data = item
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
