"""One pipeline for /ask and /chat: understand → tools → answer → check.

`run(...)` is an async generator of (event, data) pairs in the /chat order:
understood, (tool_call, tool_result)*, check?, answer | error. /ask collects it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from aura import answer as A
from aura import tools
from aura.budget import Budget, BudgetError
from aura.cache import AnswerCache, CheckCache, answer_key
from aura.config import Settings, models_enabled
from aura.contract import BOX_SUBJECTS, AskRequest, AskResponse, ChatRequest
from aura.gemini.check import check_key, default_anchors, run_check, slice_check
from aura.gemini.client import GeminiClient, GeminiError
from aura.gemini.understand import understand
from aura.router import FIRE_PATTERN, route
from aura.store import Store
from aura.writer import Writer

log = logging.getLogger("aura.pipeline")

MODEL_STEP_TIMEOUT_S = 8.0  # a model step never holds a geometry answer past the 15 s client abort


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

    def models_usable(self) -> bool:
        return (self.client is not None and self.client.available() and models_enabled()
                and self.budget.can_spend())


@dataclass
class Caller:
    ip_hash: str | None = None
    presenter: bool = False


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
                         "rooms": [x.model_dump(exclude_none=True) for x in r.rooms] if r else []},
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


async def _decide(svc: Services, req: AskRequest, d: tools.LoadedDrawing, caller: Caller,
                  stats: dict, context: dict | None = None) -> A.Built:
    question = req.question.strip()
    r = route(question, d.ctx)
    if context:
        short = len(question.split()) <= 5
        if r.intent == "unknown" and (r.scope or req.scope) and context["intent"] in ("count",
                                                                                     "area"):
            # "and in apartment B?" → the previous question, new scope
            r.intent, r.subject = context["intent"], context.get("subject")
            r.matched.append("follow-up")
        elif r.intent in ("count", "area") and r.scope is None and short and context.get("scope"):
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
            try:
                async with asyncio.timeout(MODEL_STEP_TIMEOUT_S):
                    u = await understand(
                        svc.client, question,  # type: ignore[arg-type]
                        units=d.ctx.units, labels=d.ctx.labels, image_path=d.image_path(),
                        ip_hash=caller.ip_hash, presenter=caller.presenter)
            except (BudgetError, GeminiError, TimeoutError) as e:
                stats["no_cache"] = True  # a temporary refusal must not stick
                look = ("The question matched no template, and the model step was skipped: "
                        f"{e or 'timeout'}.")
                return A.unclear(question, lang, d, intent_tag="router", look=look)
            stats["calls"] += u.calls
            stats["cost_usd"] += u.cost_usd
            stats["route"] = "choice"
            tag, conf = f"Decisions API · {u.intent_conf:.2f}", u.intent_conf
            if u.off_drawing:
                return A.abstain(
                    question, lang, {"kind": "floor"}, "not_on_drawing", "(off the drawing)",
                    f"The model judged this information is not shown on the drawing "
                    f"(p = {u.on_drawing:.2f}).", intent_body="abstain · not on drawing",
                    intent_tag=tag)
            if not u.confident:
                return A.unclear(question, lang, d, intent_tag=tag)
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
            else:
                return A.unclear(question, lang, d, intent_tag=tag)
            if text_scope is None and u.scope and u.scope_conf >= 0.6:
                text_scope = u.scope
        else:
            return A.unclear(question, lang, d)

    if intent == "find_text":
        pattern = r.pattern or FIRE_PATTERN
        tr = tools.find_text(d, pattern, subject or "label")
        if tr.text:
            return A.text(question, lang, d, tr, subject or "label", intent_tag=tag)
        n_texts = len(d.drawing.get("texts") or [])
        look = (f"Searched the printed text on this drawing for fire ratings (EI30, 30 MIN, T30): "
                f"none found{f' among {n_texts} texts' if n_texts else ''}.")
        return A.abstain_topic(question, lang, r.topic or "fire", intent_tag=tag, results=[tr],
                               look=look)

    if intent == "abstain":
        return A.abstain_topic(question, lang, r.topic or "material", intent_tag=tag)

    scope = req_scope or text_scope or {"kind": "floor"}

    if intent == "count":
        subj = subject or "room"
        tr = tools.count_objects(d, subj, scope)
        if not tr.supported:
            return A.not_supported(question, lang, scope, tr, intent_tag=tag)
        if tr.kind == "none":
            return A.unclear(question, lang, d, scope, intent_tag=tag)
        return A.count(question, lang, d, tr, scope, overrides, intent_tag=tag, intent_conf=conf)

    if not d.engine.supports("area"):
        return A.abstain(question, lang, scope, "not_supported_yet", "What is the net floor area",
                         "No rooms with areas are extracted from this drawing yet.",
                         intent_tag=tag)
    tr = tools.room_areas(d, scope, overrides or None)
    if tr.kind == "none":
        return A.unclear(question, lang, d, scope, intent_tag=tag)
    return A.area(question, lang, d, tr, scope, overrides, intent_tag=tag, intent_conf=conf)


async def _check(svc: Services, d: tools.LoadedDrawing, built: A.Built, caller: Caller,
                 stats: dict) -> dict | None:
    subject = built.subject
    if not built.check_ids or subject not in BOX_SUBJECTS:
        return None
    layer = tools.count_objects(d, subject, {"kind": "floor"})
    boxes = [b.model_dump() for b in layer.boxes]
    ids = [b["id"] for b in boxes]
    key = check_key(d.id, subject, ids)
    full = svc.checks.get(key, d.dir, subject)
    cached = full is not None
    if full is None and d.image_path() is None and d.source == "upload":
        stats["no_image"] = True
        return None
    if full is None:
        image = d.image_path()
        if image is None or not svc.models_usable():
            return None
        lock = svc.check_locks.setdefault(key, asyncio.Lock())
        async with lock:  # concurrent first asks share one request
            full = svc.checks.get(key, d.dir, subject)
            cached = full is not None
            if full is None:
                anchors = d.anchors or default_anchors(d.drawing.get("rooms", []), boxes)
                try:
                    async with asyncio.timeout(MODEL_STEP_TIMEOUT_S):
                        full = await run_check(
                            svc.client,  # type: ignore[arg-type]
                            drawing_id=d.id, image_path=image, subject=subject, boxes=boxes,
                            anchors=anchors, ip_hash=caller.ip_hash, presenter=caller.presenter)
                except (BudgetError, GeminiError, TimeoutError) as e:
                    log.warning("check skipped: %s", e or "timeout")
                    stats["check_note"] = str(e) or "timeout"
                    stats["no_cache"] = True
                    return None
                full["key"] = key
                svc.checks.put(key, d.id, subject, full)
    sliced = slice_check(full, built.check_ids)
    if sliced is None:
        return None
    stats["check_calls"] = 0 if cached else 1
    stats["check_cost_usd"] = 0.0 if cached else float(full.get("cost_usd") or 0)
    stats["check_tokens"] = 0 if cached else int(full.get("input_tokens") or 0)
    return sliced | _check_cost(stats, cached)


def _check_cost(stats: dict, cached: bool) -> dict:
    return {"calls": stats["check_calls"], "cost_usd": round(stats["check_cost_usd"], 8),
            "tokens": stats["check_tokens"], "cached": cached}


def _check_node(check: dict, cached: bool, stats: dict, subject: str) -> dict:
    lc = check["least_certain_id"]
    p_lc = check["per_box"][lc]
    agreed = check.get("agreed")
    total = check["total"]
    parts = [(f"Gemini Decisions looked at {total} drawn {A.noun(subject, total)} as zoomed tiles "
              "of the dataset image.")]
    if agreed is not None:
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


async def run(svc: Services, req: AskRequest, caller: Caller,
              context: dict | None = None) -> AsyncIterator[tuple[str, dict]]:
    t0 = time.perf_counter()
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
        body = dict(hit) | {"route": "cache", "calls": 0, "cost_usd": 0.0,
                            "latency_ms": int((time.perf_counter() - t0) * 1000)}
        yield "understood", {"detected_lang": body["detected_lang"],
                             "canonical_en": body["canonical_en"], "route": "cache"}
        if "check" in body:
            yield "check", {"check": body["check"]}
        yield "answer", {"response": body}
        return

    stats = {"calls": 0, "cost_usd": 0.0, "route": "template", "check_calls": 0,
             "check_cost_usd": 0.0, "check_tokens": 0}
    built = await _decide(svc, req, d, caller, stats, context)
    yield "understood", {"detected_lang": built.body["detected_lang"],
                         "canonical_en": built.body["canonical_en"], "route": stats["route"]}
    for ev in _tool_events(built):
        yield ev

    check = await _check(svc, d, built, caller, stats)
    body = built.body
    if check is not None:
        body["check"] = check
        node = _check_node(check, stats["check_calls"] == 0, stats, built.subject or "")
        body["trace"] = [node if n["id"] == "check" else n for n in body["trace"]]
        yield "check", {"check": check}
    elif stats.get("no_image"):
        body["check"] = {"method": "decisions_api", "status": "sample", "image": "none"}
        for n in body["trace"]:
            if n["id"] == "check":
                n.update(tag="no image",
                         body="No independent image to check against: an uploaded DXF has only "
                              "its own geometry, and checking our own rendering would be circular.")
                n.pop("confidence", None)
                n["kind"] = "info"
    elif stats.get("check_note"):
        for n in body["trace"]:
            if n["id"] == "check":
                n["body"] = f"The image check was skipped: {stats['check_note']}."
                n["tag"] = "Decisions API · skipped"

    body["calls"] = stats["calls"]
    body["cost_usd"] = round(stats["cost_usd"], 6)
    body["route"] = stats["route"]
    body["answer_id"] = "a-" + uuid.uuid4().hex[:16]
    body["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    resp = AskResponse.model_validate(body)  # contract gate: fails loudly on a bad body
    out = resp.model_dump(exclude_none=True)
    _persist(svc, d.id, req.question, out, stats)
    if not stats.get("no_cache"):
        cached_body = out
        if check is not None and stats["check_calls"]:
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


async def ask(svc: Services, req: AskRequest, caller: Caller) -> dict:
    async for event, data in run(svc, req, caller):
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
    conv_id = body.conversation_id or "c-" + uuid.uuid4().hex[:20]
    turn_id = "t-" + uuid.uuid4().hex[:16]
    existing = store.conversation(conv_id) if store else None
    now = time.time()
    if writer is not None and existing is None:
        await writer.execute("INSERT OR IGNORE INTO conversations(id, t, drawing_id) VALUES (?,?,?)",
                             (conv_id, now, body.drawing_id))
    yield "turn", {"conversation_id": conv_id, "turn_id": turn_id}

    context = context_from(store.recent_turns(conv_id) if store and existing else [])
    req = AskRequest(drawing_id=body.drawing_id, question=body.message, scope=body.scope,
                     overrides=body.overrides, lang_hint=body.lang_hint)
    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        try:
            async for item in run(svc, req, caller, context):
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
                error = {"error": "The answer took too long.", "code": "timeout"}
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
            writer.submit(
                "INSERT INTO turns(id, conversation_id, t, message, response, error) "
                "VALUES (?,?,?,?,?,?)",
                (turn_id, conv_id, time.time(), body.message,
                 json.dumps(response, ensure_ascii=False) if response else None,
                 json.dumps({"contract_version": AskResponse.model_fields["contract_version"].default,
                             **error}) if error else None))
    yield "done", {}
