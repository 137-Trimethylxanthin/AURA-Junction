"""Whole-building answers for multi-plan projects.

A drawing that belongs to a project (several sheets / floors of one building) is asked about the
WHOLE building by default. The question is limited to one floor only when it says so ("this floor",
"on the ground floor", "level 2", "EG", "rez-de-chaussée", "kerros 2" ...), when the request selects
sheets (`sheet_ids`) or when the scope is drawn on one sheet (room / region).

Answering reuses the existing per-drawing path: every sheet in scope is answered by the normal
pipeline run (concurrently, bounded), then the results are combined in code. Counts, areas and
quantities are summed; text and lists are concatenated with the sheet label; evidence items keep
their sheet in `drawing_id`; the method is the weakest of the sheets. Nothing here calls a model.

Hook: `pipeline.ask()` / `pipeline.chat_events()` take an optional `run_fn`; `main.py` passes
`functools.partial(building.run, plan=plan, base_run=pipeline.run)` when `plan_for()` found a plan.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from aura.budget import BudgetError
from aura.contract import CONTRACT_VERSION, AskRequest, ProjectBundle, ScopeFloor
from aura.gemini.client import Meter
from aura.pipeline import PipelineError, ensure_ready, with_unknown_areas
from aura.router import WHOLE_MODEL

log = logging.getLogger("aura.building")

MAX_PARALLEL = 3
MAX_SHEETS = 12  # cap on sheets answered in a single question
MAX_CALLS = 24  # one shared model-call budget across all sheets of one question

DECIDER_WORDS = "explicit words"
DECIDER_SELECTION = "user selection"
DECIDER_DEFAULT = "default whole building"

# Weakest method wins; exact-style methods rank 0.
_STRENGTH = {"cad_geometry": 0, "pdf_vector": 0, "store_sql": 0, "ocr": 1, "detector": 2,
             "vision": 3, "agent_estimate": 4}


@dataclass(frozen=True)
class Sheet:
    id: str
    label: str
    order: float


@dataclass
class Plan:
    project_id: str
    title: str
    all_sheets: list[Sheet]
    sheets: list[Sheet]  # in scope
    active: str
    decider: str
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def whole(self) -> bool:
        return len(self.sheets) == len(self.all_sheets)


# ---- scope resolution -------------------------------------------------------------------

_W = r"(?<![\w])"
_E = r"(?![\w])"

# "this floor", "the current sheet", "diese Etage", "cet étage", "tällä kerroksella" ...
THIS_FLOOR = re.compile(
    _W + r"(?:this|the\s+current|current|diese[rsmn]?|dieses|aktuelle[rnms]?|cette|cet|ce|le\s+présent|tämä|tässä|tällä|nykyi\w+)"
    r"\s+(?:floor|sheet|plan|level|storey|story|drawing|geschoss|etage|stockwerk|grundriss|blatt|"
    r"[ée]tage|niveau|feuille|kerros\w*|piirustu\w*|taso\w*)" + _E,
    re.IGNORECASE,
)

_BASEMENT = re.compile(
    _W + r"(?:basement|cellar|lower\s+ground|untergeschoss|kellergeschoss|keller|sous-sol|cave|kellari\w*)" + _E,
    re.IGNORECASE,
)
# Upper-case abbreviations only ("kg" is a weight, "eg" is a Latin abbreviation).
_ABBR = {"EG": 0, "UG": -1, "KG": -1, "DG": 99}
_ABBR_RE = re.compile(_W + r"(EG|UG|KG|DG)" + _E)
_GROUND = re.compile(
    _W + r"(?:ground\s+(?:floor|level)|erdgeschoss|rez-de-chauss[ée]e|rdc|pohjakerros\w*|maantaso\w*)" + _E,
    re.IGNORECASE,
)
_TOP = re.compile(
    _W + r"(?:top\s+floor|attic|roof\s+floor|dachgeschoss|combles|ullakko\w*|ylin\s+kerros)" + _E,
    re.IGNORECASE,
)
_ORD_EN = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
           "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "7th": 7, "eighth": 8, "8th": 8}
_ORD_EN_RE = re.compile(
    _W + r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|seventh|7th|eighth|8th)"
    r"\s+(?:floor|storey|story|level)" + _E, re.IGNORECASE)
_ORD_FR_RE = re.compile(
    _W + r"(?:(premier|1er|deuxi[eè]me|second|2e|troisi[eè]me|3e|quatri[eè]me|4e|cinqui[eè]me|5e)\s+[ée]tage)" + _E,
    re.IGNORECASE)
_ORD_FR = {"premier": 1, "1er": 1, "deuxième": 2, "deuxieme": 2, "second": 2, "2e": 2, "troisième": 3,
           "troisieme": 3, "3e": 3, "quatrième": 4, "quatrieme": 4, "4e": 4, "cinquième": 5, "cinquieme": 5, "5e": 5}
# German "1. OG", "1.OG", "OG 1", "erstes Obergeschoss", "2. Stock"
_OG_RE = re.compile(_W + r"(?:(\d)\s*\.?\s*(?:OG|obergeschoss|stock|stockwerk)|(?:OG)\s*(\d))" + _E, re.IGNORECASE)
_ORD_DE_RE = re.compile(
    _W + r"(erste[sn]?|zweite[sn]?|dritte[sn]?|vierte[sn]?|f[üu]nfte[sn]?)\s+(?:obergeschoss|stock|stockwerk|etage)" + _E,
    re.IGNORECASE)
_ORD_DE = (("erst", 1), ("zweit", 2), ("dritt", 3), ("viert", 4), ("fünf", 5), ("funf", 5))
# "level 2", "floor 2", "storey 2", "étage 2", "Etage 2"
_LEVEL_RE = re.compile(_W + r"(?:level|floor|storey|story|[ée]tage|niveau|stockwerk)\s*(-?\d{1,2})" + _E, re.IGNORECASE)
# Finnish: "2. kerros" / "kerros 2" (1. kerros is the ground floor)
_FI_RE = re.compile(_W + r"(?:(\d{1,2})\s*\.?\s*kerro\w*|kerros\w*\s+(\d{1,2}))" + _E, re.IGNORECASE)


def _orders_named(q: str) -> set[float]:
    out: set[float] = set()
    for m in _ORD_EN_RE.finditer(q):
        out.add(float(_ORD_EN[m.group(1).lower()]))
    for m in _ORD_FR_RE.finditer(q):
        out.add(float(_ORD_FR[m.group(1).lower()]))
    for m in _ORD_DE_RE.finditer(q):
        word = m.group(1).lower()
        out.update(float(n) for stem, n in _ORD_DE if word.startswith(stem))
    for m in _OG_RE.finditer(q):
        out.add(float(m.group(1) or m.group(2)))
    for m in _LEVEL_RE.finditer(q):
        out.add(float(m.group(1)))
    for m in _FI_RE.finditer(q):
        out.add(float(int(m.group(1) or m.group(2)) - 1))
    return out


def named_sheets(question: str, sheets: list[Sheet]) -> list[Sheet]:
    """Sheets the question names by floor words or by their own label ("EG", "1. OG")."""
    hit: dict[str, Sheet] = {}
    orders = _orders_named(question)
    top = max((s.order for s in sheets), default=None)
    for s in sheets:
        if s.order in orders:
            hit[s.id] = s
        if _GROUND.search(question) and s.order == 0:
            hit[s.id] = s
        if _BASEMENT.search(question) and s.order < 0:
            hit[s.id] = s
        if _TOP.search(question) and s.order == top:
            hit[s.id] = s
        for ab in _ABBR_RE.findall(question):
            if s.order == _ABBR[ab] or (ab == "DG" and s.order == top):
                hit[s.id] = s
        lbl = s.label.strip()
        flags = 0 if len(lbl) <= 3 else re.IGNORECASE  # short labels ("EG", "B1") match upper-case only
        if len(lbl) >= 2 and re.search(_W + re.escape(lbl) + _E, question, flags):
            hit[s.id] = s
    return [s for s in sheets if s.id in hit]


def _dedup_floors(floors: list[Any]) -> list[Sheet]:
    """Deduplicate project floors by `drawing_id` (first wins), then sort vertically."""
    seen: set[str] = set()
    unique: list[Sheet] = []
    for f in floors:
        if f.drawing_id in seen:
            continue
        seen.add(f.drawing_id)
        unique.append(Sheet(f.drawing_id, f.label, float(f.order)))
    return sorted(unique, key=lambda s: (s.order, s.label, s.id))


def plan_for(
    bundles: list[ProjectBundle],
    drawing_id: str,
    question: str,
    *,
    project_id: str | None = None,
    sheet_ids: list[str] | None = None,
    scope: dict | None = None,
    saved_ids: set[str] | None = None,
) -> Plan | None:
    """The plan for this question, or None when the drawing is not a floor of a multi-sheet project.

    User-saved bundles (`saved_ids`) are applied ONLY when `project_id` explicitly names them (and
    `bundles` already filtered to projects the caller owns); server-built bundles apply by default.
    """
    if (scope or {}).get("kind") == "project":
        return None  # an explicit project scope keeps the deep-agent multi-plan path (pipeline.run)
    saved = saved_ids or set()
    proj = None
    if project_id:
        proj = next((p for p in bundles if p.id == project_id), None)
    else:
        proj = next(
            (
                p for p in bundles
                if p.id not in saved
                and len({f.drawing_id for f in p.floors}) >= 2
                and drawing_id in {f.drawing_id for f in p.floors}
            ),
            None,
        )
    if proj is None:
        return None
    all_sheets = _dedup_floors(proj.floors)
    by_id = {s.id: s for s in all_sheets}
    if len(all_sheets) < 2 or drawing_id not in by_id:
        return None
    active = by_id[drawing_id]
    kind = (scope or {}).get("kind")

    def plan(sheets: list[Sheet], decider: str, note: str | None = None) -> Plan:
        return Plan(proj.id, proj.title, all_sheets, sheets, drawing_id, decider, note)

    if THIS_FLOOR.search(question):
        return plan([active], DECIDER_WORDS, "this floor")
    named = named_sheets(question, all_sheets)
    if named:
        return plan(named, DECIDER_WORDS, ", ".join(s.label for s in named))
    if sheet_ids:
        picked = [by_id[i] for i in dict.fromkeys(sheet_ids) if i in by_id]
        if picked:
            return plan(picked, DECIDER_SELECTION if len(picked) < len(all_sheets) else DECIDER_DEFAULT)
    if kind in ("room", "region"):
        return plan([active], DECIDER_SELECTION, f"{kind} drawn on this sheet")
    return plan(list(all_sheets), DECIDER_DEFAULT)


def validate_plan(plan: Plan, *, max_sheets: int = MAX_SHEETS) -> None:
    """Refuse a question whose resolved sheet set exceeds `max_sheets`."""
    if max_sheets > 0 and len(plan.sheets) > max_sheets:
        raise PipelineError(
            "bad_request",
            f"Too many sheets in one question ({len(plan.sheets)}; maximum {max_sheets}). "
            f"Select up to {max_sheets} sheets or ask about a specific floor.",
            400,
        )


class SheetUnavailable(Exception):
    """A sheet in the building plan is missing (deleted/expired) or not ready."""

    def __init__(self, reason: str, kind: str = "missing"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind  # "missing" | "not_ready"


def check_sheet(svc: Any, drawing_id: str) -> SheetUnavailable | None:
    """Check existence and readiness of one sheet; return `SheetUnavailable` if unusable."""
    store = getattr(svc, "store", None)
    reg = getattr(svc, "registry", None)
    if drawing_id.startswith("up-") and store is not None:
        row = store.drawing_row(drawing_id)
        if row is None:
            if reg is not None:
                reg.evict(drawing_id)
            return SheetUnavailable("this sheet was deleted or is missing", "missing")
        if row.get("status") != "ready":
            return SheetUnavailable(f"this sheet is {row.get('status')} (not ready)", "not_ready")
    if reg is not None:
        d = reg.get(drawing_id)
        if d is None:
            return SheetUnavailable("this sheet is missing", "missing")
        st = d.drawing.get("status", "ready")
        if st != "ready":
            return SheetUnavailable(f"this sheet is {st} (not ready)", "not_ready")
    return None


# ---- combining --------------------------------------------------------------------------


def _weakest(methods: list[str]) -> str:
    return max(methods, key=lambda m: _STRENGTH.get(m, 5)) if methods else "none"


def _round(v: float) -> float:
    return round(v, 2)


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else _round(v)


def _text_of(a: dict) -> str:
    k = a.get("kind")
    if k == "count":
        return f"{a['value']} {a.get('subject', '')}".strip()
    if k == "area":
        return f"{_num(a['value'])} m²"
    if k == "quantity":
        return f"{_num(a['value'])} {a.get('unit', '')}".strip()
    return str(a.get("value", ""))


def _scope_body(plan: Plan) -> dict:
    return {"kind": "project", "project_id": plan.project_id, "drawing_ids": [s.id for s in plan.sheets]}


def _scope_node(plan: Plan) -> dict:
    labels = ", ".join(s.label for s in plan.sheets)
    body = (f"Whole building: {len(plan.sheets)} sheets ({labels})." if plan.whole
            else f"{len(plan.sheets)} of {len(plan.all_sheets)} sheets: {labels}.")
    if plan.note and plan.decider == DECIDER_WORDS:
        body += f" The question says: {plan.note}."
    return {"id": "scope", "parent": "q", "title": "Scope", "tag": plan.decider, "body": body, "kind": "info",
            "stage": "scope",
            "detail": {"decider": plan.decider, "project_id": plan.project_id,
                       "sheets": [{"id": s.id, "label": s.label} for s in plan.sheets]}}


def _remap_ids(resp: dict, sheet: Sheet, seen_boxes: set[str], seen_rooms: set[str]) -> dict[str, str]:
    """Items of a later sheet whose ids collide with an earlier sheet get `<sheet>:<id>`; returns old -> new."""
    mapping: dict[str, str] = {}
    ev = resp.get("evidence") or {}
    for b in ev.get("boxes") or []:
        b["drawing_id"] = b.get("drawing_id") or sheet.id
        old = b["id"]
        if old in seen_boxes:
            b["id"] = f"{sheet.id}:{old}"
            mapping[old] = b["id"]
        seen_boxes.add(b["id"])
    for r in ev.get("rooms") or []:
        r["drawing_id"] = r.get("drawing_id") or sheet.id
        old = r["room_id"]
        if old in seen_rooms:
            r["room_id"] = f"{sheet.id}:{old}"
            mapping[f"room-{old}"] = f"room-{r['room_id']}"
        seen_rooms.add(r["room_id"])
    for ln in ev.get("links") or []:
        ln["drawing_id"] = ln.get("drawing_id") or sheet.id
    return mapping


def _branch(i: int, sheet: Sheet, resp: dict | None, status: str, mapping: dict[str, str], why: str = "") -> list[dict]:
    a = (resp or {}).get("answer") or {}
    if status == "ok":
        body = _text_of(a)
    elif status == "abstain":
        body = "Could not answer from this sheet's data."
    else:
        body = f"Failed: {why}" if why else "Failed."
    nodes = [{"id": f"sheet-{i}", "parent": "scope", "title": sheet.label, "tag": sheet.id, "body": body,
              "kind": "info" if status == "ok" else "warn"}]
    for n in (resp or {}).get("trace") or []:
        n = dict(n)
        n["id"] = f"{i}:{n['id']}"
        n["parent"] = f"{i}:{n['parent']}" if n.get("parent") else f"sheet-{i}"
        if n.get("evidence_ids"):
            n["evidence_ids"] = [mapping.get(e, e) for e in n["evidence_ids"]]
        nodes.append(n)
    return nodes


def combine(plan: Plan, question: str, results: list[tuple[Sheet, dict | BaseException]], elapsed_ms: int) -> dict:
    """One whole-building response from the per-sheet results (never calls a model)."""
    results = [(sh, copy.deepcopy(r) if isinstance(r, dict) else r) for sh, r in results]
    oks: list[tuple[int, Sheet, dict]] = []
    others: list[tuple[int, Sheet, dict | BaseException]] = []
    for i, (sh, r) in enumerate(results):
        if isinstance(r, BaseException) or (r.get("answer") or {}).get("kind") == "abstain":
            others.append((i, sh, r))
        else:
            oks.append((i, sh, r))
    responses = [r for _, r in results if isinstance(r, dict)]
    if not responses:
        non_sheet = next((r for _, r in results if isinstance(r, BaseException)
                          and not isinstance(r, SheetUnavailable)), None)
        if non_sheet is not None:
            raise non_sheet
        template = {
            "answer": {"kind": "abstain", "reason": "not_answerable"},
            "detected_lang": "en",
            "canonical_en": question,
            "route": "template",
        }
    else:
        template = (oks[0][2] if oks else next(r for _, _, r in others if isinstance(r, dict)))

    seen_boxes: set[str] = set()
    seen_rooms: set[str] = set()
    maps: dict[int, dict[str, str]] = {}
    for i, sh, r in sorted(oks + [(i, s, r) for i, s, r in others if isinstance(r, dict)], key=lambda t: t[0]):
        maps[i] = _remap_ids(r, sh, seen_boxes, seen_rooms)

    boxes, rooms, rows, groups, links = [], [], [], {}, []
    for i, sh, r in sorted(oks, key=lambda t: t[0]):
        ev = r.get("evidence") or {}
        boxes += ev.get("boxes") or []
        rooms += ev.get("rooms") or []
        rows += ev.get("rows") or []
        for g in ev.get("groups") or []:
            groups.setdefault(g["id"], g)
        links += ev.get("links") or []
    evidence: dict[str, Any] = {"boxes": boxes, "rooms": rooms}
    if rows:
        evidence["rows"] = rows
    if groups:
        evidence["groups"] = list(groups.values())
    if links:
        evidence["links"] = links

    breakdown: list[dict] = []
    for i, sh, r in sorted(oks, key=lambda t: t[0]):
        a = r["answer"]
        item: dict[str, Any] = {"drawing_id": sh.id, "label": sh.label, "status": "ok"}
        if a["kind"] in ("count", "area", "quantity"):
            item["value"] = a["value"]
            item["unit"] = {"count": a.get("subject"), "area": "m2"}.get(a["kind"], a.get("unit"))
        breakdown.append(item)
    for i, sh, r in others:
        breakdown.append({"drawing_id": sh.id, "label": sh.label,
                          "status": "error" if isinstance(r, BaseException) else "abstain"})
    order = {s.id: n for n, s in enumerate(plan.sheets)}
    breakdown.sort(key=lambda b: order.get(b["drawing_id"], 0))

    failed = [sh.label for _, sh, _ in others]
    method = _weakest([r["method"] for _, _, r in oks]) if oks else "none"
    kinds = {r["answer"]["kind"] for _, _, r in oks}
    subjects = {r["answer"].get("subject") for _, _, r in oks}
    units = {r["answer"].get("unit") for _, _, r in oks}
    if not oks:
        answer = dict(template["answer"])
        where = f" Sheets that could not answer: {', '.join(failed)}." if failed else ""
        if answer.get("where_to_find"):
            answer["where_to_find"] = answer["where_to_find"] + where
        elif where:
            answer["where_to_find"] = where.strip()
    elif kinds == {"count"} and len(subjects) == 1:
        answer = {"kind": "count", "value": sum(r["answer"]["value"] for _, _, r in oks),
                  "subject": subjects.pop()}
    elif kinds == {"area"}:
        answer = {"kind": "area", "value": _round(sum(r["answer"]["value"] for _, _, r in oks)), "unit": "m2"}
    elif kinds == {"quantity"} and len(units) == 1:
        answer = {"kind": "quantity", "value": _round(sum(r["answer"]["value"] for _, _, r in oks)),
                  "unit": units.pop()}
    else:
        answer = {"kind": "note",
                  "value": "\n".join(f"{sh.label}: {_text_of(r['answer'])}" for _, sh, r in sorted(oks, key=lambda t: t[0]))}
    parts = [f"{b['label']} {_num(b['value'])}" for b in breakdown if b["status"] == "ok" and b.get("value") is not None]
    total_txt = _text_of(answer) if answer["kind"] != "abstain" else "no answer"
    sentence = f"{total_txt}: " + ", ".join(parts) if parts else total_txt
    if failed:
        sentence += f". Could not answer: {', '.join(failed)}"
    lang = template.get("detected_lang", "en")
    trace: list[dict] = [
        {"id": "q", "title": "Question", "tag": f"{lang} · detected", "body": f"“{question}”", "kind": "info"},
        _scope_node(plan),
    ]
    for i, sh, r in sorted(
        [(i, sh, r) for i, (sh, r) in enumerate(results)], key=lambda t: t[0]
    ):
        status = ("error" if isinstance(r, BaseException)
                  else "abstain" if (r.get("answer") or {}).get("kind") == "abstain" else "ok")
        why = ""
        if isinstance(r, SheetUnavailable):
            why = r.reason
        elif isinstance(r, BaseException):  # the reason stays in the server log, never in the response
            log.warning("building: sheet %s failed: %s: %s", getattr(sh, "id", "?"), type(r).__name__, r)
            why = "this sheet could not be answered"
        trace += _branch(i, sh, r if isinstance(r, dict) else None, status, maps.get(i, {}), why)
    calls = sum(r.get("calls", 0) for r in responses)
    cost = round(sum(r.get("cost_usd", 0.0) for r in responses), 8)
    exact = method in ("cad_geometry", "pdf_vector", "store_sql")
    trace.append({"id": "answer", "parent": "q", "title": "Answer",
                  "tag": "sum over sheets" if answer["kind"] in ("count", "area", "quantity") else "by sheet",
                  "body": sentence, "kind": ("warn" if not oks else "exact" if exact else "model"),
                  "stage": "answer", "detail": {"source": "building", "calls": calls, "cost_usd": cost}})
    routes = [r.get("route") for r in responses]
    body: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "answer": answer,
        "evidence": evidence,
        "detected_lang": lang,
        "canonical_en": template.get("canonical_en", question),
        "scope": _scope_body(plan),
        "method": method,
        "trace": trace,
        "calls": calls,
        "cost_usd": cost,
        "latency_ms": elapsed_ms,
        "source": "api",
        "answer_id": "a-" + uuid.uuid4().hex,
        "route": "agent" if "agent" in routes else (template.get("route") or "template"),
        "breakdown": breakdown,
    }
    return with_unknown_areas(body)


# ---- shared call budget & per-sheet meters ----------------------------------------------


class _TeeMeter(Meter):
    """Per-sheet meter whose own counters stay isolated while forwarding every request to `outer`."""

    def __init__(self, outer: Meter | None = None) -> None:
        super().__init__()
        self._outer = outer

    def add(self, rec: Any) -> None:
        super().add(rec)
        if self._outer is not None:
            self._outer.add(rec)


class SharedCallBudget:
    """One shared model-call cap across all sheets of one whole-building question."""

    def __init__(self, cap: int = MAX_CALLS) -> None:
        self.cap = cap
        self._lock = threading.Lock()
        self._used = 0

    def acquire(self) -> None:
        if self.cap <= 0:
            return
        with self._lock:
            if self._used + 1 > self.cap:
                raise BudgetError(
                    "budget_exhausted",
                    f"Whole-building question reached its {self.cap}-call budget.",
                )
            self._used += 1

    def release(self) -> None:
        if self.cap <= 0:
            return
        with self._lock:
            self._used = max(0, self._used - 1)

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


class _CappedClient:
    """Proxy around `GeminiClient` enforcing one shared `SharedCallBudget` across sheets."""

    def __init__(self, inner: Any, gate: SharedCallBudget) -> None:
        self._inner = inner
        self._gate = gate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def _guarded(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self._gate.acquire()
        probe = _TeeMeter(kwargs.get("meter"))
        kwargs["meter"] = probe
        try:
            return await fn(*args, **kwargs)
        except BaseException:
            if probe.calls == 0:
                self._gate.release()
            raise

    async def decisions(self, *args: Any, **kwargs: Any) -> Any:
        return await self._guarded(self._inner.decisions, *args, **kwargs)

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        return await self._guarded(self._inner.generate, *args, **kwargs)


def _with_shared_budget(svc: Any, max_calls: int) -> tuple[Any, SharedCallBudget]:
    gate = SharedCallBudget(max_calls)
    if svc is None or getattr(svc, "client", None) is None or max_calls <= 0:
        return svc, gate
    capped_client = _CappedClient(svc.client, gate)
    if dataclasses.is_dataclass(svc):
        return dataclasses.replace(svc, client=capped_client), gate
    return svc, gate


# ---- sheet tracking & persistence -------------------------------------------------------


def sheet_ids_of(resp: dict, plan: Plan | None = None, *, extra_ids: Iterable[str] = ()) -> list[str]:
    """Ordered distinct drawing ids participating in `resp` (and `plan`, when provided)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(did: Any) -> None:
        if isinstance(did, str) and did and did not in seen:
            seen.add(did)
            out.append(did)

    for did in extra_ids:
        add(did)
    if plan is not None:
        for s in plan.sheets:
            add(s.id)
    add(resp.get("drawing_id"))
    scope = resp.get("scope")
    if isinstance(scope, dict):
        add(scope.get("drawing_id"))
        for did in scope.get("drawing_ids") or []:
            add(did)
    for b in resp.get("breakdown") or []:
        if isinstance(b, dict):
            add(b.get("drawing_id"))
    ev = resp.get("evidence")
    if isinstance(ev, dict):
        for key in ("boxes", "rooms", "links"):
            for it in ev.get(key) or []:
                if isinstance(it, dict):
                    add(it.get("drawing_id"))
                    add(it.get("target_drawing_id"))
        for grp in ev.get("groups") or []:
            if isinstance(grp, dict):
                add(grp.get("target_drawing_id"))
    for n in resp.get("trace") or []:
        if not isinstance(n, dict):
            continue
        if n.get("parent") == "scope":
            add(n.get("tag"))
        det = n.get("detail")
        if isinstance(det, dict) and isinstance(det.get("sheets"), list):
            for sh in det["sheets"]:
                if isinstance(sh, dict):
                    add(sh.get("id"))
    return out


def response_contains_sheet(
    resp: Any, drawing_id: str, sheet_ids_raw: str | list[str] | None = None
) -> bool:
    """True when `resp` (dict or JSON string) or `sheet_ids_raw` includes `drawing_id` or its items."""
    if not drawing_id:
        return False
    if isinstance(sheet_ids_raw, list) and drawing_id in sheet_ids_raw:
        return True
    if isinstance(sheet_ids_raw, str) and sheet_ids_raw:
        try:
            parsed_ids = json.loads(sheet_ids_raw)
            if isinstance(parsed_ids, list) and drawing_id in parsed_ids:
                return True
        except ValueError:
            pass
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except ValueError:
            return False
    if not isinstance(resp, dict):
        return False
    return drawing_id in sheet_ids_of(resp)


def turn_contains_sheet(turn: Any, drawing_id: str) -> bool:
    """True when a mirrored browser session turn references `drawing_id`."""
    if not isinstance(turn, dict) or not drawing_id:
        return False
    if turn.get("drawing_id") == drawing_id:
        return True
    if isinstance(turn.get("response"), dict) and response_contains_sheet(turn["response"], drawing_id):
        return True
    return response_contains_sheet(turn, drawing_id)


_ANSWERS_SHEET_COL_DBS: set[str] = set()


def _answers_has_sheet_ids(svc: Any) -> bool:
    st = getattr(svc, "store", None)
    if st is None or not hasattr(st, "_q"):
        return False
    db_key = str(getattr(st, "path", ""))
    if db_key and db_key in _ANSWERS_SHEET_COL_DBS:
        return True
    has_col = "sheet_ids" in {r["name"] for r in st._q("PRAGMA table_info(answers)")}
    if has_col and db_key:
        _ANSWERS_SHEET_COL_DBS.add(db_key)
    return has_col


def _sheet_was_deleted(svc: Any, drawing_id: str) -> bool:
    """True when `drawing_id` is an upload whose row has already been deleted from SQLite."""
    st = getattr(svc, "store", None)
    return bool(drawing_id.startswith("up-") and st is not None and st.drawing_row(drawing_id) is None)


def _purge_late_sheet(svc: Any, drawing_id: str) -> None:
    """Purge any in-memory or queued DB artefacts written by an in-flight sub-run for a just-deleted upload."""
    if svc is None:
        return
    if getattr(svc, "registry", None) is not None:
        svc.registry.evict(drawing_id)
    answers = getattr(svc, "answers", None)
    if answers is not None:
        answers.drop_drawing(drawing_id)
        for k, v in list(answers._d.items()):
            d_id, _q, k_scope, *_ = answers._parse_key(k)
            in_scope = isinstance(k_scope, dict) and drawing_id in (k_scope.get("drawing_ids") or [])
            if d_id == drawing_id or in_scope or response_contains_sheet(v, drawing_id):
                answers._d.pop(k, None)
                answers._t.pop(k, None)
                answers._q.pop(k, None)
    checks = getattr(svc, "checks", None)
    if checks is not None:
        checks.forget_drawing(drawing_id)
        for ck in [k for k in list(checks._mem) if k.startswith(f"{drawing_id}:") or k == f"score:{drawing_id}"]:
            checks._mem.pop(ck, None)
    writer = getattr(svc, "writer", None)
    if writer is not None:
        writer.submit("DELETE FROM answers WHERE drawing_id=?", (drawing_id,))
        writer.submit("DELETE FROM answer_cache WHERE drawing_id=?", (drawing_id,))
        writer.submit("DELETE FROM check_cache WHERE drawing_id=?", (drawing_id,))


def _tag_sheet_evidence(resp: dict, sheet_id: str) -> None:
    """Ensure every evidence item from a non-active sheet carries its `drawing_id`."""
    ev = resp.get("evidence")
    if not isinstance(ev, dict):
        return
    for key in ("boxes", "rooms", "links"):
        for it in ev.get(key) or []:
            if isinstance(it, dict) and not it.get("drawing_id"):
                it["drawing_id"] = sheet_id


def persist_answer(
    svc: Any,
    drawing_id: str,
    question: str,
    body: dict,
    stats: dict,
    *,
    plan: Plan | None = None,
) -> None:
    """Persist a combined whole-building answer together with its participating `sheet_ids`."""
    if svc is None or getattr(svc, "writer", None) is None:
        return
    if _sheet_was_deleted(svc, drawing_id):
        return
    sids = sheet_ids_of(body, plan, extra_ids=(drawing_id,))
    errored = {
        b.get("drawing_id")
        for b in (body.get("breakdown") or [])
        if isinstance(b, dict) and b.get("status") == "error"
    }
    if any(_sheet_was_deleted(svc, did) for did in sids if did not in errored):
        return
    now = time.time()
    resp_json = json.dumps(body, ensure_ascii=False)
    if _answers_has_sheet_ids(svc):
        svc.writer.submit(
            "INSERT INTO answers(id, t, day, drawing_id, question, route, method, calls, cost_usd, "
            "check_calls, check_cost_usd, response, sheet_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                body["answer_id"],
                now,
                svc.budget.day(now),
                drawing_id,
                question,
                body.get("route"),
                body["method"],
                body["calls"],
                body["cost_usd"],
                stats["check_calls"],
                stats["check_cost_usd"],
                resp_json,
                json.dumps(sids, ensure_ascii=False),
            ),
        )
        return
    svc.writer.submit(
        "INSERT INTO answers(id, t, day, drawing_id, question, route, method, calls, cost_usd, "
        "check_calls, check_cost_usd, response) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            body["answer_id"],
            now,
            svc.budget.day(now),
            drawing_id,
            question,
            body.get("route"),
            body["method"],
            body["calls"],
            body["cost_usd"],
            stats["check_calls"],
            stats["check_cost_usd"],
            resp_json,
        ),
    )


# ---- running ----------------------------------------------------------------------------

BaseRun = Callable[..., AsyncIterator[tuple[str, dict]]]


def wrap_project_scope_run(
    base_run: BaseRun, project_ids: list[str], *, max_calls: int = MAX_CALLS
) -> BaseRun:
    """Wrap explicit `scope.kind == 'project'` runs to share `max_calls`, record `answers.sheet_ids`,
    and abort persistence if an upload sheet in scope was deleted mid-flight."""

    async def _wrapped(
        svc: Any,
        req: AskRequest,
        caller: Any,
        context: dict | None = None,
        history: list[dict] | None = None,
        meters: dict | None = None,
        deadline_s: float | None = None,
        agent_deadline_s: float | None = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        capped_svc, _gate = _with_shared_budget(svc, max_calls)
        involved = list(dict.fromkeys([req.drawing_id, *project_ids]))
        async for ev, data in base_run(
            capped_svc, req, caller, context, history, meters, deadline_s, agent_deadline_s
        ):
            if ev == "answer":
                resp = data["response"]
                deleted_ids = [did for did in involved if _sheet_was_deleted(svc, did)]
                if deleted_ids:
                    if getattr(svc, "writer", None) is not None and resp.get("answer_id"):
                        svc.writer.submit("DELETE FROM answers WHERE id=?", (resp["answer_id"],))
                    for did in deleted_ids:
                        _purge_late_sheet(svc, did)
                    raise PipelineError("unknown_drawing", f"Unknown drawing '{deleted_ids[0]}'", 404)
                if svc is not None and getattr(svc, "writer", None) is not None and _answers_has_sheet_ids(svc):
                    sids = sheet_ids_of(resp, extra_ids=involved)
                    svc.writer.submit(
                        "UPDATE answers SET sheet_ids=? WHERE id=?",
                        (json.dumps(sids, ensure_ascii=False), resp["answer_id"]),
                    )
            yield ev, data

    return _wrapped


def _sub_request(req: AskRequest, sheet: Sheet, plan: Plan) -> AskRequest:
    scope = req.scope if req.scope is not None and req.scope.kind != "project" else None
    if len(plan.sheets) > 1 and (
        (scope is None and WHOLE_MODEL.search(req.question))
        or (scope is not None and scope.kind == "model")
    ):
        # "in the whole building" is what the plan already does by asking every sheet: each sheet
        # answers for its own storey, or an IFC storey would answer for the whole model and the
        # combined total would count the model once per sheet.
        scope = ScopeFloor()
    own = sheet.id == req.drawing_id
    return AskRequest(drawing_id=sheet.id, question=req.question, scope=scope,
                      overrides=req.overrides if own else None, lang_hint=req.lang_hint)


async def run(svc: Any, req: AskRequest, caller: Any, context: dict | None = None,
              history: list[dict] | None = None, meters: dict | None = None,
              deadline_s: float | None = None, agent_deadline_s: float | None = None, *,
              plan: Plan, base_run: BaseRun,
              persist: Callable[[Any, str, str, dict, dict], None] | None = None,
              max_sheets: int = MAX_SHEETS,
              max_calls: int = MAX_CALLS) -> AsyncIterator[tuple[str, dict]]:
    """Drop-in for `pipeline.run`: the same events, answered for the sheets of `plan`."""
    validate_plan(plan, max_sheets=max_sheets)
    if svc is not None and getattr(svc, "store", None) is not None:
        ensure_ready(svc, req.drawing_id)
    if (
        svc is not None
        and getattr(svc, "registry", None) is not None
        and svc.registry.get(req.drawing_id) is None
    ):
        raise PipelineError("unknown_drawing", f"Unknown drawing '{req.drawing_id}'", 404)

    t0 = time.perf_counter()
    scope_node = _scope_node(plan)
    meters = meters if meters is not None else {"answer": Meter(), "check": Meter()}
    capped_svc, _gate = _with_shared_budget(svc, max_calls)
    emitted_understood = False

    if len(plan.sheets) == 1:
        only = plan.sheets[0]
        prob = check_sheet(capped_svc, only.id)
        if prob is None:
            # One ready sheet: the normal answer, with the scope node added to its trace.
            sub = _sub_request(req, only, plan) if only.id != req.drawing_id else req
            results = []
            async for ev, data in base_run(
                capped_svc, sub, caller, context, history, meters, deadline_s, agent_deadline_s
            ):
                if ev == "understood":
                    emitted_understood = True
                if ev == "answer":
                    if _sheet_was_deleted(svc, only.id):
                        _purge_late_sheet(svc, only.id)
                        results = [(only, SheetUnavailable("this sheet was deleted or is missing", "missing"))]
                        break
                    resp = copy.deepcopy(data["response"])
                    if only.id != req.drawing_id:
                        _tag_sheet_evidence(resp, only.id)
                    trace = list(resp.get("trace") or [])
                    resp["trace"] = [*trace[:1], scope_node | {"parent": (trace[0]["id"] if trace else "q")}, *trace[1:]]
                    if svc is not None and getattr(svc, "writer", None) is not None and _answers_has_sheet_ids(svc):
                        sids = sheet_ids_of(resp, plan, extra_ids=(only.id,))
                        svc.writer.submit(
                            "UPDATE answers SET sheet_ids=?, response=? WHERE id=?",
                            (
                                json.dumps(sids, ensure_ascii=False),
                                json.dumps(resp, ensure_ascii=False),
                                resp["answer_id"],
                            ),
                        )
                    data = {"response": resp}
                yield ev, data
            if not results:
                return
        else:
            results = [(only, prob)]
    else:
        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def one(sheet: Sheet) -> dict | BaseException:
            prob = check_sheet(capped_svc, sheet.id)
            if prob is not None:
                return prob
            sub = _sub_request(req, sheet, plan)
            sheet_meters = {
                "answer": _TeeMeter(meters.get("answer")),
                "check": _TeeMeter(meters.get("check")),
            }
            async with sem:
                async for ev, data in base_run(capped_svc, sub, caller, context, history,
                                               sheet_meters, deadline_s, agent_deadline_s):
                    if ev == "answer":
                        return dict(data["response"])
            raise RuntimeError("the pipeline ended without an answer")

        raw = await asyncio.gather(*(one(s) for s in plan.sheets), return_exceptions=True)
        results = []
        for s, r in zip(plan.sheets, raw):
            if isinstance(r, asyncio.CancelledError):
                raise r
            if _sheet_was_deleted(svc, s.id):
                _purge_late_sheet(svc, s.id)
                r = SheetUnavailable("this sheet was deleted or is missing", "missing")
            results.append((s, r))

    body = combine(plan, req.question, results, int((time.perf_counter() - t0) * 1000))
    if not emitted_understood:
        yield "understood", {
            "detected_lang": body["detected_lang"],
            "canonical_en": body["canonical_en"],
            "route": body["route"] if body["route"] in ("template", "agent", "cache", "choice") else "template",
        }
    if persist is not None:
        persist(svc, req.drawing_id, req.question, body, {"check_calls": 0, "check_cost_usd": 0.0})
    yield "answer", {"response": body}
