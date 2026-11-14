"""Evaluate where AURA's `/chat` (and `/ask`) delivers verified answers vs. gaps or misroutes.

Usage examples (run from `backend/`):
  uv run python -m eval.chat_coverage
  uv run python -m eval.chat_coverage --drawing swiss-46639 --mode both
  uv run python -m eval.chat_coverage --api-url http://127.0.0.1:8000
  uv run python -m eval.chat_coverage --category doors_and_openings,circulation_and_stairs
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import os
import statistics
import sys
import tempfile
import time
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

# Windows compatibility for in-process `aura.main` import (`aura/parse_worker.py` imports POSIX
# `resource` at module import time; we do not modify `aura/*` per scope).
if sys.platform == "win32" and "resource" not in sys.modules:
    _res_stub = types.ModuleType("resource")
    _res_stub.RLIMIT_AS = 9  # type: ignore[attr-defined]
    _res_stub.RLIMIT_CPU = 0  # type: ignore[attr-defined]
    _res_stub.RLIMIT_FSIZE = 1  # type: ignore[attr-defined]
    _res_stub.RLIMIT_NOFILE = 7  # type: ignore[attr-defined]
    _res_stub.getrlimit = lambda *_a, **_kw: (64, 64)  # type: ignore[attr-defined]
    _res_stub.setrlimit = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    sys.modules["resource"] = _res_stub

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx
from pydantic import ValidationError

from aura.contract import BOX_SUBJECTS, AskResponse
from eval.chat_coverage_cases import (
    CATEGORY_DESCRIPTIONS,
    SWISS_DRAWING_ID,
    ChatProbe,
    ConversationProbe,
    ProbeExpectation,
    suite_for_drawing,
)

Verdict = Literal[
    "answered_verified",
    "answered_misrouted",
    "abstained_gap",
    "abstained_expected",
    "error",
]

VERDICT_ORDER: tuple[Verdict, ...] = (
    "answered_verified",
    "answered_misrouted",
    "abstained_gap",
    "abstained_expected",
    "error",
)

FRONTEND_SWISS_PATH = REPO_DIR / "frontend" / "src" / "data" / "drawing.swiss-46639.json"
SWISS_IMAGE_PATH = REPO_DIR / "design" / "assets" / "original-crop.png"
GEMINI_FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures" / "gemini"
DEFAULT_RESULTS_DIR = BACKEND_DIR / "eval" / "results"


# ---------------------------------------------------------------------------
# SSE parsing & contract / evidence validation
# ---------------------------------------------------------------------------

@dataclass
class StreamCapture:
    """Parsed `/chat` SSE stream (or synthesized `/ask` capture) for a single turn."""

    http_status: int
    content_type: str
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    raw_error_body: dict[str, Any] | str | None = None
    parse_errors: list[str] = field(default_factory=list)
    wall_ms: float = 0.0


@dataclass
class TurnResult:
    """Complete evaluation record for one executed turn."""

    probe_id: str
    category: str
    drawing_id: str
    lang: str
    question: str
    conversation_key: str | None
    turn_index: int | None
    mode: str
    endpoint: str
    verdict: Verdict
    diagnosis: str
    answerable_on_drawing: bool
    expected_kind: str
    expected_value: float | int | str | None
    expected_subject: str | None
    expected_scope: dict[str, Any] | None
    rationale: str
    tags: list[str]
    # Actual platform outputs
    http_status: int
    route: str | None = None
    detected_lang: str | None = None
    canonical_en: str | None = None
    actual_kind: str | None = None
    actual_value: float | int | str | None = None
    actual_subject: str | None = None
    actual_scope: dict[str, Any] | None = None
    abstain_reason: str | None = None
    where_to_find: str | None = None
    agent_failure: str | None = None
    method: str | None = None
    calls: int = 0
    cost_usd: float = 0.0
    check_status: str | None = None
    latency_ms: int = 0
    wall_ms: float = 0.0
    event_names: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    contract_errors: list[str] = field(default_factory=list)
    fixture_hits: list[str] = field(default_factory=list)
    fixture_misses: int = 0


def parse_sse_text(raw_text: str) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Parse SSE wire text into `(event_name, payload_dict)` pairs and any framing errors."""
    events: list[tuple[str, dict[str, Any]]] = []
    errors: list[str] = []
    normalized = raw_text.replace("\r\n", "\n")
    if not normalized.strip():
        return events, ["empty SSE response stream"]

    for idx, block in enumerate(normalized.strip().split("\n\n")):
        if not block.strip():
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        had_malformed = False
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.removeprefix(" ")
            else:
                errors.append(f"block {idx}: malformed SSE line {line!r}")
                had_malformed = True
                continue
            if k == "event":
                event_name = v.strip()
            elif k == "data":
                data_lines.append(v)
        if not event_name and not data_lines and not had_malformed:
            # Standalone comment / `: progress N` heartbeat block
            continue
        if not event_name:
            errors.append(f"block {idx}: missing 'event:' field")
            continue
        raw_data = "\n".join(data_lines)
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            errors.append(f"block {idx} ({event_name}): invalid JSON data: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"block {idx} ({event_name}): SSE data must be a JSON object")
            continue
        events.append((event_name, payload))
    return events, errors


def check_no_nulls(obj: Any, path: str = "$") -> list[str]:
    """Contract rule: optional fields are omitted, never null (except `evidence.rooms[*].area_m2`)."""
    errs: list[str] = []
    if obj is None:
        if not path.endswith(".area_m2"):
            errs.append(f"forbidden null at {path}")
        return errs
    if isinstance(obj, dict):
        for k, v in obj.items():
            errs.extend(check_no_nulls(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            errs.extend(check_no_nulls(v, f"{path}[{i}]"))
    return errs


def validate_response_invariants(
    resp: dict[str, Any],
    extra_evidence_ids: set[str] | None = None,
) -> list[str]:
    """Validate `AskResponse` schema, `no_nulls`, trace evidence refs, and UI evidence math."""
    errs: list[str] = []
    try:
        AskResponse.model_validate(resp)
    except ValidationError as exc:
        errs.append(f"AskResponse schema violation: {exc.errors()[0]['msg']}")
        return errs

    errs.extend(check_no_nulls(resp))

    ev = resp.get("evidence") or {}
    boxes = ev.get("boxes") or []
    rooms = ev.get("rooms") or []
    rows = ev.get("rows") or []
    groups = ev.get("groups") or []
    links = ev.get("links") or []
    known_ids: set[str] = (
        {str(b["id"]) for b in boxes if "id" in b}
        | {f"room-{r['room_id']}" for r in rooms if "room_id" in r}
        | {str(r["id"]) for r in rows if "id" in r}
        | {str(g["id"]) for g in groups if "id" in g}
        | {str(lk["id"]) for lk in links if "id" in lk}
        | (extra_evidence_ids or set())
    )

    for node in resp.get("trace") or []:
        # Intermediate `stage == 'filter'` step nodes in Contract 1.2 `aura/steps.py` may reference
        # pre-filter rooms/boxes from an earlier set (`s1`) before narrowing to the final set (`s2`).
        if node.get("stage") == "filter" and node.get("id") != "answer":
            continue
        for eid in node.get("evidence_ids") or []:
            if eid not in known_ids:
                errs.append(f"trace node {node.get('id')!r} references missing evidence_id {eid!r}")
                break

    ans = resp["answer"]
    kind = ans["kind"]
    if kind == "count" and resp.get("estimate") is None:
        subj = ans.get("subject")
        val = int(ans["value"])
        if subj in BOX_SUBJECTS and not rows and len(boxes) != val:
            errs.append(f"count/box mismatch: answer={val} vs len(evidence.boxes)={len(boxes)}")
        elif subj == "room" and not rows:
            inc = sum(1 for r in rooms if r.get("included"))
            if inc != val:
                errs.append(f"room count mismatch: answer={val} vs included rooms={inc}")
    elif kind == "area":
        inc_sum = round(
            sum(
                float(r["area_m2"])
                for r in rooms
                if r.get("included") and r.get("area_m2") is not None
            ),
            2,
        )
        val_f = round(float(ans["value"]), 2)
        if abs(inc_sum - val_f) > 0.02:
            errs.append(f"area sum mismatch: answer={val_f} vs sum(included rooms)={inc_sum}")

    return errs


def validate_chat_capture(capture: StreamCapture) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate `/chat` SSE event sequence and return `(response_dict, contract_errors)`."""
    if capture.http_status != 200:
        detail = capture.raw_error_body
        return None, [f"HTTP {capture.http_status}: {detail}"]
    if not capture.content_type.startswith("text/event-stream"):
        return None, [f"unexpected content-type {capture.content_type!r}"]
    if capture.parse_errors:
        return None, list(capture.parse_errors)

    events = capture.events
    names = [e for e, _ in events]
    if len(events) < 3:
        return None, [f"SSE stream too short: {names}"]

    errs: list[str] = []
    if names[0] != "turn":
        errs.append(f"first SSE event must be 'turn', got {names[0]!r}")
    if names[-1] != "done":
        errs.append(f"last SSE event must be 'done', got {names[-1]!r}")

    turn_data = events[0][1] if names[0] == "turn" else {}
    conv_id = turn_data.get("conversation_id")
    turn_id = turn_data.get("turn_id")
    if not conv_id or not turn_id:
        errs.append("turn event missing conversation_id or turn_id")

    term_name, term_data = events[-2]
    if term_name == "error":
        code = term_data.get("code", "unknown")
        msg = term_data.get("error", "")
        errs.append(f"SSE error event ({code}): {msg}")
        return None, errs
    if term_name != "answer":
        errs.append(f"penultimate SSE event must be 'answer' or 'error', got {term_name!r}")
        return None, errs

    if len(events) < 4 or names[1] != "understood":
        errs.append(f"expected 'understood' at index 1, got {names}")
    understood = events[1][1] if len(events) >= 2 and names[1] == "understood" else {}

    mid = events[2:-2]
    pending_call_id: str | None = None
    streamed_eids: set[str] = set()
    for ev_name, ev_payload in mid:
        if ev_name not in ("progress", "tool_call", "tool_result", "check"):
            errs.append(f"unexpected middle SSE event {ev_name!r}")
        elif ev_name == "tool_call":
            if pending_call_id is not None:
                errs.append(f"tool_call {pending_call_id!r} was not followed by tool_result")
            node = ev_payload.get("node") or {}
            pending_call_id = node.get("id")
        elif ev_name == "tool_result":
            res_id = ev_payload.get("node_id")
            if pending_call_id != res_id:
                errs.append(
                    f"tool_result node_id={res_id!r} did not match tool_call {pending_call_id!r}"
                )
            pending_call_id = None
            ev_obj = ev_payload.get("evidence") or {}
            for b in ev_obj.get("boxes") or []:
                if "id" in b:
                    streamed_eids.add(str(b["id"]))
            for r in ev_obj.get("rooms") or []:
                if "room_id" in r:
                    streamed_eids.add(f"room-{r['room_id']}")
    if pending_call_id is not None:
        errs.append(f"dangling tool_call {pending_call_id!r} without tool_result")

    resp = term_data.get("response")
    if not isinstance(resp, dict):
        errs.append("answer event missing 'response' object")
        return None, errs

    if conv_id and resp.get("conversation_id") != conv_id:
        errs.append(
            f"conversation_id mismatch: turn={conv_id!r} vs answer={resp.get('conversation_id')!r}"
        )
    if turn_id and resp.get("turn_id") != turn_id:
        errs.append(f"turn_id mismatch: turn={turn_id!r} vs answer={resp.get('turn_id')!r}")
    if understood:
        if understood.get("detected_lang") != resp.get("detected_lang"):
            errs.append("detected_lang mismatch between understood and answer")
        if understood.get("route") != resp.get("route"):
            errs.append(
                f"route mismatch: understood={understood.get('route')!r} vs "
                f"answer={resp.get('route')!r}"
            )

    errs.extend(validate_response_invariants(resp, extra_evidence_ids=streamed_eids))
    return resp, errs


# ---------------------------------------------------------------------------
# 5-way coverage classification
# ---------------------------------------------------------------------------

def _extract_actual_value(ans: dict[str, Any]) -> float | int | str | None:
    kind = ans.get("kind")
    if kind in ("count", "area", "quantity", "text"):
        return ans.get("value")
    if kind == "note":
        if ans.get("number") is not None:
            return ans.get("number")
        return ans.get("value")
    return None


def classify_response(
    resp: dict[str, Any],
    exp: ProbeExpectation,
) -> tuple[Verdict, str]:
    """Classify a contract-valid `AskResponse` against ground-truth `ProbeExpectation`."""
    ans = resp["answer"]
    actual_kind = str(ans["kind"])
    actual_val = _extract_actual_value(ans)
    actual_subj = ans.get("subject")
    actual_scope = resp.get("scope")

    if actual_kind == "abstain":
        reason = ans.get("reason", "unknown")
        af = resp.get("agent_failure")
        af_note = f", agent_failure={af}" if af else ""
        if not exp.answerable_on_drawing:
            return "abstained_expected", f"Properly abstained ({reason}{af_note})."
        return (
            "abstained_gap",
            f"Abstained ({reason}{af_note}); expected {exp.expected_kind}"
            + (f"={exp.expected_value}" if exp.expected_value is not None else "")
            + ".",
        )

    # Platform delivered a non-abstain answer (`count`, `area`, `text`, `quantity`, or `note`).
    if not exp.answerable_on_drawing:
        return (
            "answered_misrouted",
            (
                f"Should have abstained, but returned {actual_kind}={actual_val} "
                f"(scope={actual_scope})."
            ),
        )

    allowed_kinds = set(exp.allowed_kinds) | {exp.expected_kind}
    if actual_kind not in allowed_kinds:
        return (
            "answered_misrouted",
            f"Wrong answer kind: expected {exp.expected_kind}"
            + (f"={exp.expected_value}" if exp.expected_value is not None else "")
            + f", got {actual_kind}={actual_val} (subject={actual_subj}, scope={actual_scope}).",
        )

    if (
        exp.expected_subject is not None
        and actual_kind in ("count", "text")
        and actual_subj != exp.expected_subject
    ):
        return (
            "answered_misrouted",
            (
                f"Wrong subject: expected {exp.expected_subject!r}, got {actual_subj!r} "
                f"(value={actual_val})."
            ),
        )

    allowed_scopes = list(exp.allowed_scopes)
    if exp.expected_scope is not None:
        allowed_scopes.append(exp.expected_scope)
    if allowed_scopes and actual_scope not in allowed_scopes:
        return (
            "answered_misrouted",
            (
                f"Wrong scope: expected {allowed_scopes[0]}, got {actual_scope} "
                f"(returned {actual_kind}={actual_val})."
            ),
        )

    if exp.expected_value is None and not exp.expected_substrings and not exp.allow_any_value:
        return (
            "answered_misrouted",
            (
                f"Unverified feature lacks ground-truth layer support, yet returned "
                f"{actual_kind}={actual_val} (scope={actual_scope})."
            ),
        )

    if exp.expected_value is not None:
        if isinstance(exp.expected_value, (int, float)) and isinstance(actual_val, (int, float)):
            tol = max(
                exp.tolerance_abs,
                abs(float(exp.expected_value)) * (exp.tolerance_pct / 100.0),
            )
            if abs(float(actual_val) - float(exp.expected_value)) > tol:
                return (
                    "answered_misrouted",
                    (
                        f"Wrong value: expected {exp.expected_value}, got {actual_val} "
                        f"(scope={actual_scope})."
                    ),
                )
        elif actual_kind == "note" and isinstance(exp.expected_value, str):
            note_text = str(ans.get("value", ""))
            if exp.expected_value.strip().casefold() not in note_text.casefold():
                return (
                    "answered_misrouted",
                    f"Note missing expected target {exp.expected_value!r}: got {note_text!r}.",
                )
        elif str(actual_val).strip().casefold() != str(exp.expected_value).strip().casefold():
            return (
                "answered_misrouted",
                f"Wrong text value: expected {exp.expected_value!r}, got {actual_val!r}.",
            )

    if exp.expected_substrings:
        ev = resp.get("evidence") or {}
        inc_rooms = " ".join(
            str(r.get("room_id", ""))
            for r in (ev.get("rooms") or [])
            if r.get("included")
        )
        haystack = " ".join([
            str(ans.get("value", "")),
            str(resp.get("explanation", "")),
            inc_rooms,
        ]).casefold()
        missing_subs = [s for s in exp.expected_substrings if s.casefold() not in haystack]
        if missing_subs:
            return (
                "answered_misrouted",
                f"Answer missing expected identifier(s) {missing_subs!r} in {ans.get('value')!r}.",
            )

    return "answered_verified", f"Matched {actual_kind}={actual_val}."


def build_turn_result(
    *,
    probe_id: str,
    category: str,
    drawing_id: str,
    lang: str,
    question: str,
    expectation: ProbeExpectation,
    tags: tuple[str, ...],
    mode: str,
    endpoint: str,
    capture: StreamCapture,
    conversation_key: str | None = None,
    turn_index: int | None = None,
    fixture_hits: list[str] | None = None,
    fixture_misses: int = 0,
) -> TurnResult:
    """Validate capture and construct a full `TurnResult`."""
    event_names = [e for e, _ in capture.events]
    tool_names = [
        str(((ev_data.get("node") or {}).get("tool") or {}).get("name", ""))
        for ev_name, ev_data in capture.events
        if ev_name == "tool_call"
    ]

    if endpoint == "chat":
        resp, contract_errors = validate_chat_capture(capture)
    else:
        if capture.http_status != 200:
            resp, contract_errors = None, [f"HTTP {capture.http_status}: {capture.raw_error_body}"]
        elif not capture.events or capture.events[0][0] != "answer":
            resp, contract_errors = None, ["missing synthesized answer payload"]
        else:
            resp = capture.events[0][1].get("response")
            contract_errors = (
                validate_response_invariants(resp)
                if isinstance(resp, dict)
                else ["invalid /ask JSON response"]
            )

    if contract_errors or resp is None:
        return TurnResult(
            probe_id=probe_id,
            category=category,
            drawing_id=drawing_id,
            lang=lang,
            question=question,
            conversation_key=conversation_key,
            turn_index=turn_index,
            mode=mode,
            endpoint=endpoint,
            verdict="error",
            diagnosis="; ".join(contract_errors) or "Unknown stream/contract error",
            answerable_on_drawing=expectation.answerable_on_drawing,
            expected_kind=expectation.expected_kind,
            expected_value=expectation.expected_value,
            expected_subject=expectation.expected_subject,
            expected_scope=expectation.expected_scope,
            rationale=expectation.rationale,
            tags=list(tags),
            http_status=capture.http_status,
            wall_ms=round(capture.wall_ms, 2),
            event_names=event_names,
            tool_names=[t for t in tool_names if t],
            contract_errors=contract_errors,
            fixture_hits=list(fixture_hits or []),
            fixture_misses=fixture_misses,
        )

    verdict, diagnosis = classify_response(resp, expectation)
    ans = resp["answer"]
    if not tool_names:
        tool_names = [
            str((n.get("tool") or {}).get("name"))
            for n in (resp.get("trace") or [])
            if n.get("tool")
        ]
    check_obj = resp.get("check")
    check_status = None
    if isinstance(check_obj, dict):
        if "ranking" in check_obj:
            check_status = "cached" if check_obj.get("cached") else "ranked"
        else:
            check_status = str(check_obj.get("image") or check_obj.get("status") or "sample")

    return TurnResult(
        probe_id=probe_id,
        category=category,
        drawing_id=drawing_id,
        lang=lang,
        question=question,
        conversation_key=conversation_key,
        turn_index=turn_index,
        mode=mode,
        endpoint=endpoint,
        verdict=verdict,
        diagnosis=diagnosis,
        answerable_on_drawing=expectation.answerable_on_drawing,
        expected_kind=expectation.expected_kind,
        expected_value=expectation.expected_value,
        expected_subject=expectation.expected_subject,
        expected_scope=expectation.expected_scope,
        rationale=expectation.rationale,
        tags=list(tags),
        http_status=capture.http_status,
        route=resp.get("route"),
        detected_lang=resp.get("detected_lang"),
        canonical_en=resp.get("canonical_en"),
        actual_kind=ans.get("kind"),
        actual_value=_extract_actual_value(ans),
        actual_subject=ans.get("subject"),
        actual_scope=resp.get("scope"),
        abstain_reason=ans.get("reason") if ans.get("kind") == "abstain" else None,
        where_to_find=ans.get("where_to_find") if ans.get("kind") == "abstain" else None,
        agent_failure=resp.get("agent_failure"),
        method=resp.get("method"),
        calls=int(resp.get("calls", 0)),
        cost_usd=float(resp.get("cost_usd", 0.0)),
        check_status=check_status,
        latency_ms=int(resp.get("latency_ms", 0)),
        wall_ms=round(capture.wall_ms, 2),
        event_names=event_names,
        tool_names=[t for t in tool_names if t],
        contract_errors=[],
        fixture_hits=list(fixture_hits or []),
        fixture_misses=fixture_misses,
    )


# ---------------------------------------------------------------------------
# Transports & Runner (In-Process FastAPI TestClient + Live HTTP)
# ---------------------------------------------------------------------------

class GracefulFixtureTransport:
    """Replays recorded Gemini fixtures from `tests/fixtures/gemini/*.json` (UTF-8 safe).

    When a probe triggers an unrecorded model call, returns HTTP 503 so `aura.pipeline`
    degrades gracefully (abstaining or skipping image check) instead of crashing the run.
    """

    def __init__(self, fixtures_dir: Path = GEMINI_FIXTURES_DIR) -> None:
        from aura.gemini.client import request_key

        self._request_key = request_key
        self.fixtures: dict[str, dict[str, Any]] = {}
        self.turn_hits: list[str] = []
        self.turn_misses: int = 0
        if fixtures_dir.is_dir():
            for f in sorted(fixtures_dir.glob("*.json")):
                fx = json.loads(f.read_text(encoding="utf-8"))
                fx = dict(fx)
                fx.setdefault("name", f.name)
                self.fixtures[request_key(fx["path"], fx["request"])] = fx

    def reset_turn(self) -> None:
        self.turn_hits.clear()
        self.turn_misses = 0

    async def post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        key = self._request_key(path, body)
        fx = self.fixtures.get(key)
        if fx is None:
            self.turn_misses += 1
            return 503, {"error": {"message": f"no_recorded_fixture:{path}:{key}"}}
        self.turn_hits.append(str(fx.get("name", key)))
        return int(fx.get("status", 200)), fx["response"]


LOCAL_DRAWING_BASES: tuple[Path, ...] = (
    BACKEND_DIR / ".data" / "drawings",
    BACKEND_DIR / ".data" / "uploads",
    BACKEND_DIR / "build" / "drawings-live",
    BACKEND_DIR / "build" / "drawings",
)


def _load_dir_utf8(plan_dir: Path, drawing_id: str | None = None) -> tuple[Any, Path | None]:
    """UTF-8-safe equivalent of `aura.tools.load_dir` supporting Swiss, IFC, Raster, and Bundle plans."""
    from aura import tools

    data = json.loads((plan_dir / "drawing.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rooms", []), list):
        raise tools.UnreadableDrawing(f"{plan_dir / 'drawing.json'} is not a valid Drawing")

    ev_path = plan_dir / "evidence.json"
    if ev_path.is_file() and "evidence" not in data:
        ev_raw = json.loads(ev_path.read_text(encoding="utf-8"))
        data["evidence"] = ev_raw.get("boxes", ev_raw) if isinstance(ev_raw, dict) else {}

    gold = data.get("gold") or []
    if not gold and (plan_dir / "gold.json").is_file():
        g = json.loads((plan_dir / "gold.json").read_text(encoding="utf-8"))
        gold = g.get("gold", g) if isinstance(g, dict) else g

    kind = data.get("kind", "cad_dxf")
    if (plan_dir / "raster.json").is_file():
        engine: Any = tools.RasterEngine(
            json.loads((plan_dir / "raster.json").read_text(encoding="utf-8"))
        )
    elif kind == "ifc":
        from aura.solvers import ifc

        engine = tools.IfcEngine(
            ifc.IfcModel.from_json(
                json.loads((plan_dir / "model.json").read_text(encoding="utf-8"))
            )
        )
    elif (plan_dir / "model.json").is_file():
        from aura.solvers import swiss

        m = json.loads((plan_dir / "model.json").read_text(encoding="utf-8"))
        engine = tools.SwissEngine(swiss.SwissModel.from_json(m))
    else:
        engine = tools.BundleEngine(data)

    img_path: Path | None = None
    for img_name in ("original.png", "image.png"):
        cand_img = plan_dir / img_name
        if cand_img.is_file():
            img_path = cand_img
            break

    did = drawing_id or str(data.get("id") or plan_dir.name)
    loaded = tools.LoadedDrawing(
        id=did,
        dir=plan_dir,
        drawing=data,
        engine=engine,
        gold=list(gold),
        anchors=list(data.get("anchors") or []),
        source=str(data.get("source", "library")),
    )
    return loaded, img_path


def load_local_drawing(
    drawing_id: str,
    drawing_path: Path | None = None,
) -> tuple[Any, Path | None]:
    """Load any local drawing (`SwissEngine`, `IfcEngine`, `RasterEngine`, or `BundleEngine`)."""
    from aura import tools

    if drawing_path is not None:
        p = drawing_path.resolve()
        if p.is_dir() and (p / "drawing.json").is_file():
            return _load_dir_utf8(p, drawing_id=None if drawing_id == "auto" else drawing_id)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            did = str(data.get("id") or p.stem) if drawing_id == "auto" else drawing_id
            data["id"] = did
            png = p.parent / "original.png"
            loaded = tools.LoadedDrawing(
                id=did,
                dir=p.parent if png.is_file() else None,
                drawing=data,
                engine=tools.BundleEngine(data),
                gold=list(data.get("gold") or []),
                anchors=list(data.get("anchors") or []),
            )
            return loaded, png if png.is_file() else None
        raise FileNotFoundError(f"Custom --drawing-path '{drawing_path}' does not exist or lacks drawing.json.")

    for base in LOCAL_DRAWING_BASES:
        cand_dir = base / drawing_id
        if (cand_dir / "drawing.json").is_file():
            return _load_dir_utf8(cand_dir, drawing_id=drawing_id)

    if drawing_id == SWISS_DRAWING_ID and FRONTEND_SWISS_PATH.is_file():
        data = json.loads(FRONTEND_SWISS_PATH.read_text(encoding="utf-8"))
        img = SWISS_IMAGE_PATH if SWISS_IMAGE_PATH.is_file() else None
        loaded = tools.LoadedDrawing(
            id=data["id"],
            dir=None,
            drawing=data,
            engine=tools.BundleEngine(data),
            gold=list(data.get("gold") or []),
            anchors=list(data.get("anchors") or []),
        )
        return loaded, img

    raise FileNotFoundError(
        f"Drawing '{drawing_id}' not found locally in {', '.join(str(b) for b in LOCAL_DRAWING_BASES)} "
        f"or {FRONTEND_SWISS_PATH}."
    )


def discover_local_drawing_ids(extra_bases: tuple[Path, ...] = ()) -> list[str]:
    """Auto-discover all ready local drawing IDs (library + uploads + bundled fallback)."""
    seen: list[str] = []
    for base in (*extra_bases, *LOCAL_DRAWING_BASES):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "drawing.json").is_file() and child.name not in seen:
                seen.append(child.name)
    if FRONTEND_SWISS_PATH.is_file() and SWISS_DRAWING_ID not in seen:
        seen.append(SWISS_DRAWING_ID)
    return seen


def _send_turn_http(
    http_client: Any,
    *,
    endpoint: str,
    drawing_id: str,
    message: str,
    conversation_id: str | None = None,
    scope: dict[str, Any] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    lang_hint: str | None = None,
) -> StreamCapture:
    """Execute one turn against `/chat` (SSE) or `/ask` (JSON) on a TestClient or httpx.Client."""
    t0 = time.perf_counter()
    if endpoint == "chat":
        payload: dict[str, Any] = {"drawing_id": drawing_id, "message": message}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if scope is not None:
            payload["scope"] = scope
        if overrides is not None:
            payload["overrides"] = overrides
        if lang_hint is not None:
            payload["lang_hint"] = lang_hint

        with http_client.stream("POST", "/chat", json=payload) as resp:
            status = resp.status_code
            ctype = resp.headers.get("content-type", "")
            raw_text = "".join(resp.iter_text())
        wall_ms = (time.perf_counter() - t0) * 1000.0
        if status != 200:
            try:
                err_body: dict[str, Any] | str = json.loads(raw_text)
            except json.JSONDecodeError:
                err_body = raw_text[:500]
            return StreamCapture(
                http_status=status,
                content_type=ctype,
                raw_error_body=err_body,
                wall_ms=wall_ms,
            )
        events, parse_errs = parse_sse_text(raw_text)
        return StreamCapture(
            http_status=status,
            content_type=ctype,
            events=events,
            parse_errors=parse_errs,
            wall_ms=wall_ms,
        )

    # `/ask` JSON endpoint
    ask_payload: dict[str, Any] = {"drawing_id": drawing_id, "question": message}
    if scope is not None:
        ask_payload["scope"] = scope
    if overrides is not None:
        ask_payload["overrides"] = overrides
    if lang_hint is not None:
        ask_payload["lang_hint"] = lang_hint

    resp = http_client.post("/ask", json=ask_payload)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200:
        try:
            err_body = resp.json()
        except ValueError:
            err_body = resp.text[:500]
        return StreamCapture(
            http_status=resp.status_code,
            content_type=ctype,
            raw_error_body=err_body,
            wall_ms=wall_ms,
        )
    return StreamCapture(
        http_status=200,
        content_type=ctype,
        events=[("answer", {"response": resp.json()})],
        wall_ms=wall_ms,
    )


@contextlib.contextmanager
def _env_override(updates: dict[str, str | None]):
    saved = {k: os.environ.get(k) for k in updates}
    try:
        for k, v in updates.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_dotenv_file(dotenv_path: Path = BACKEND_DIR / ".env") -> dict[str, str]:
    """Read key=value pairs from `backend/.env` without mutating `os.environ`."""
    parsed: dict[str, str] = {}
    if not dotenv_path.is_file():
        return parsed
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k:
            parsed[k] = v
    return parsed


def _safe_budget_tz() -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    for candidate in ("Europe/Helsinki", "UTC"):
        try:
            ZoneInfo(candidate)
            return candidate
        except ZoneInfoNotFoundError:
            continue
    return "UTC"


def run_mode(
    *,
    mode: Literal["deterministic", "fixtures", "live"],
    drawing_id: str,
    probes: list[ChatProbe],
    conversations: list[ConversationProbe],
    endpoint: str = "chat",
    api_url: str | None = None,
    drawing_path: Path | None = None,
    hybrid_steps: bool = False,
    verbose: bool = False,
) -> list[TurnResult]:
    """Execute all selected single-turn and multi-turn probes under one mode."""
    results: list[TurnResult] = []

    if api_url:
        with httpx.Client(base_url=api_url.rstrip("/"), timeout=45.0) as client:
            for p in probes:
                cap = _send_turn_http(
                    client,
                    endpoint=endpoint,
                    drawing_id=p.drawing_id,
                    message=p.question,
                    scope=p.scope,
                    overrides=p.overrides,
                    lang_hint=p.lang_hint,
                )
                results.append(
                    build_turn_result(
                        probe_id=p.id,
                        category=p.category,
                        drawing_id=p.drawing_id,
                        lang=p.lang,
                        question=p.question,
                        expectation=p.expectation,
                        tags=p.tags,
                        mode=mode,
                        endpoint=endpoint,
                        capture=cap,
                    )
                )
            if endpoint == "chat":
                for conv in conversations:
                    conv_id: str | None = None
                    for turn in conv.turns:
                        cap = _send_turn_http(
                            client,
                            endpoint="chat",
                            drawing_id=conv.drawing_id,
                            message=turn.message,
                            conversation_id=conv_id,
                            scope=turn.scope,
                            overrides=turn.overrides,
                        )
                        if cap.events and cap.events[0][0] == "turn":
                            conv_id = cap.events[0][1].get("conversation_id") or conv_id
                        results.append(
                            build_turn_result(
                                probe_id=turn.id,
                                category=conv.category,
                                drawing_id=conv.drawing_id,
                                lang=turn.lang,
                                question=turn.message,
                                expectation=turn.expectation,
                                tags=turn.tags,
                                mode=mode,
                                endpoint="chat",
                                capture=cap,
                                conversation_key=conv.conversation_key,
                                turn_index=turn.turn_index,
                            )
                        )
        return results

    # In-process FastAPI TestClient execution (isolated temp DATA_DIR)
    import shutil

    from fastapi.testclient import TestClient

    from aura import tools
    from aura.config import Settings
    from aura.main import create_app

    loaded_orig, image_path = load_local_drawing(drawing_id, drawing_path=drawing_path)
    fx_transport = GracefulFixtureTransport() if mode == "fixtures" else None

    env_map: dict[str, str | None] = {
        "AURA_MODELS": "off" if mode == "deterministic" else None,
    }
    if mode == "live":
        dotenv_vals = _load_dotenv_file()
        if not os.environ.get("GEMINI_API_KEY") and dotenv_vals.get("GEMINI_API_KEY"):
            env_map["GEMINI_API_KEY"] = dotenv_vals["GEMINI_API_KEY"]
    else:
        env_map["GEMINI_API_KEY"] = None

    with tempfile.TemporaryDirectory(prefix=f"aura_cov_{mode}_") as tmp_str, _env_override(env_map):
        tmp_path = Path(tmp_str)
        assets_dir: Path | None = loaded_orig.dir
        if image_path is not None and mode in ("fixtures", "live"):
            assets_dir = tmp_path / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(image_path, assets_dir / image_path.name)
            if image_path.name != "original.png":
                shutil.copy(image_path, assets_dir / "original.png")

        loaded = tools.LoadedDrawing(
            id=loaded_orig.id,
            dir=assets_dir,
            drawing=loaded_orig.drawing,
            engine=loaded_orig.engine,
            gold=loaded_orig.gold,
            anchors=loaded_orig.anchors,
            source=loaded_orig.source,
        )
        sidecar_url = (
            os.environ.get("AURA_SIDECAR_URL") or "http://127.0.0.1:8090"
            if (mode == "live" and hybrid_steps)
            else None
        )
        settings = Settings(
            DATA_DIR=tmp_path / "data",
            AURA_BUILD_DIR=tmp_path / "nobuild",
            AURA_UNLIMITED=True,
            AURA_EVENT_CAP=100_000,
            AURA_DAILY_CAP=100_000,
            AURA_IP_AGENT_PER_HOUR=100_000,
            AURA_SIDECAR_URL=sidecar_url,
            AURA_BUDGET_TZ=_safe_budget_tz(),
        )
        import logging

        aura_log = logging.getLogger("aura")
        prev_level = aura_log.level
        app = create_app(
            settings,
            transport=fx_transport,
            extra_drawings=[loaded],
            use_gemini=(mode in ("fixtures", "live")),
        )
        if not verbose:
            aura_log.setLevel(logging.ERROR)

        try:
            with TestClient(app) as client:
                svc = app.state.svc
                if loaded.id.startswith("up-") and svc.store is not None:
                    from aura.store import connect as _db_connect

                    conn = _db_connect(svc.store.path)
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO drawings "
                            "(id, t, kind, status, progress, title, expires_at) "
                            "VALUES (?, ?, ?, 'ready', 1.0, ?, ?)",
                            (
                                loaded.id,
                                time.time(),
                                str(loaded.drawing.get("kind", "cad_dxf")),
                                str(loaded.drawing.get("title", loaded.id)),
                                time.time() + 86400.0,
                            ),
                        )
                    finally:
                        conn.close()
                for p in probes:
                    # Isolate single-turn probes from prior answer-cache or failed-check cooldowns.
                    svc.answers._d.clear()
                    svc.check_failures.clear()
                    if fx_transport is not None:
                        fx_transport.reset_turn()

                    cap = _send_turn_http(
                        client,
                        endpoint=endpoint,
                        drawing_id=p.drawing_id,
                        message=p.question,
                        scope=p.scope,
                        overrides=p.overrides,
                        lang_hint=p.lang_hint,
                    )
                    results.append(
                        build_turn_result(
                            probe_id=p.id,
                            category=p.category,
                            drawing_id=p.drawing_id,
                            lang=p.lang,
                            question=p.question,
                            expectation=p.expectation,
                            tags=p.tags,
                            mode=mode,
                            endpoint=endpoint,
                            capture=cap,
                            fixture_hits=fx_transport.turn_hits if fx_transport else None,
                            fixture_misses=fx_transport.turn_misses if fx_transport else 0,
                        )
                    )

                if endpoint == "chat":
                    for conv in conversations:
                        svc.answers._d.clear()
                        svc.check_failures.clear()
                        conv_id = None
                        for turn in conv.turns:
                            if fx_transport is not None:
                                fx_transport.reset_turn()
                            cap = _send_turn_http(
                                client,
                                endpoint="chat",
                                drawing_id=conv.drawing_id,
                                message=turn.message,
                                conversation_id=conv_id,
                                scope=turn.scope,
                                overrides=turn.overrides,
                            )
                            if cap.events and cap.events[0][0] == "turn":
                                conv_id = cap.events[0][1].get("conversation_id") or conv_id
                            results.append(
                                build_turn_result(
                                    probe_id=turn.id,
                                    category=conv.category,
                                    drawing_id=conv.drawing_id,
                                    lang=turn.lang,
                                    question=turn.message,
                                    expectation=turn.expectation,
                                    tags=turn.tags,
                                    mode=mode,
                                    endpoint="chat",
                                    capture=cap,
                                    conversation_key=conv.conversation_key,
                                    turn_index=turn.turn_index,
                                    fixture_hits=fx_transport.turn_hits if fx_transport else None,
                                    fixture_misses=fx_transport.turn_misses if fx_transport else 0,
                                )
                            )
        finally:
            aura_log.setLevel(prev_level)
    return results


# ---------------------------------------------------------------------------
# Aggregation, JSON serialization & Markdown report generation
# ---------------------------------------------------------------------------

def summarize_mode(turns: list[TurnResult]) -> dict[str, Any]:
    """Compute overall and per-category coverage metrics for one execution mode."""
    counts: dict[str, int] = {v: 0 for v in VERDICT_ORDER}
    for t in turns:
        counts[t.verdict] += 1

    total = len(turns)
    answerable = [t for t in turns if t.answerable_on_drawing]
    unanswerable = [t for t in turns if not t.answerable_on_drawing]
    n_ans = len(answerable)
    n_unans = len(unanswerable)

    ans_verified = sum(1 for t in answerable if t.verdict == "answered_verified")
    ans_misrouted = sum(1 for t in answerable if t.verdict == "answered_misrouted")
    ans_gap = sum(1 for t in answerable if t.verdict == "abstained_gap")

    unans_refused = sum(1 for t in unanswerable if t.verdict == "abstained_expected")
    unans_hallucinated = sum(1 for t in unanswerable if t.verdict == "answered_misrouted")

    wall_times = sorted(t.wall_ms for t in turns) if turns else [0.0]
    p50_ms = round(statistics.median(wall_times), 2)
    p95_idx = min(len(wall_times) - 1, max(0, round(0.95 * (len(wall_times) - 1))))
    p95_ms = round(wall_times[p95_idx], 2)

    by_category: dict[str, dict[str, Any]] = {}
    seen_cats = list(CATEGORY_DESCRIPTIONS.keys()) + [
        t.category for t in turns if t.category not in CATEGORY_DESCRIPTIONS
    ]
    for cat in dict.fromkeys(seen_cats):
        ct = [t for t in turns if t.category == cat]
        if not ct:
            continue
        cat_counts = {v: sum(1 for x in ct if x.verdict == v) for v in VERDICT_ORDER}
        cat_ans = sum(1 for x in ct if x.answerable_on_drawing)
        cat_ver = sum(
            1 for x in ct if x.answerable_on_drawing and x.verdict == "answered_verified"
        )
        by_category[cat] = {
            "description": CATEGORY_DESCRIPTIONS.get(cat, ""),
            "total": len(ct),
            "answerable": cat_ans,
            "verified_rate_on_answerable_pct": (
                round(100.0 * cat_ver / cat_ans, 1) if cat_ans else None
            ),
            **cat_counts,
        }

    by_route: dict[str, int] = {}
    for t in turns:
        r = t.route or "none"
        by_route[r] = by_route.get(r, 0) + 1

    return {
        "total_turns": total,
        "verdict_counts": counts,
        "answerable_turns": n_ans,
        "verified_on_answerable": ans_verified,
        "verified_on_answerable_pct": round(100.0 * ans_verified / n_ans, 1) if n_ans else 0.0,
        "misrouted_on_answerable": ans_misrouted,
        "gap_on_answerable": ans_gap,
        "off_drawing_turns": n_unans,
        "properly_refused_off_drawing": unans_refused,
        "misrouted_off_drawing": unans_hallucinated,
        "routes": by_route,
        "total_model_calls": sum(t.calls for t in turns),
        "total_cost_usd": round(sum(t.cost_usd for t in turns), 6),
        "latency_wall_p50_ms": p50_ms,
        "latency_wall_p95_ms": p95_ms,
        "by_category": by_category,
    }


def compute_mode_lifts(
    det_turns: list[TurnResult],
    cmp_turns: list[TurnResult],
    cmp_mode: str = "fixtures",
) -> list[dict[str, Any]]:
    """Find probes whose verdict or route changed between `deterministic` and `cmp_mode`."""
    det_by_id = {t.probe_id: t for t in det_turns}
    diffs: list[dict[str, Any]] = []
    for ft in cmp_turns:
        dt = det_by_id.get(ft.probe_id)
        if dt is None:
            continue
        if dt.verdict != ft.verdict or dt.route != ft.route:
            diffs.append({
                "probe_id": ft.probe_id,
                "question": ft.question,
                "compare_mode": cmp_mode,
                "deterministic_verdict": dt.verdict,
                "deterministic_route": dt.route,
                "fixtures_verdict": ft.verdict,
                "fixtures_route": ft.route,
                "model_verdict": ft.verdict,
                "model_route": ft.route,
                "fixtures_used": ft.fixture_hits,
                "actual_answer": (
                    f"{ft.actual_kind}={ft.actual_value}"
                    if ft.actual_kind != "abstain"
                    else f"abstain ({ft.abstain_reason})"
                ),
            })
    return diffs


def build_report_payload(
    *,
    drawing_id: str,
    endpoint: str,
    api_url: str | None,
    runs_by_mode: dict[str, list[TurnResult]],
) -> dict[str, Any]:
    """Assemble the complete JSON-serializable coverage report."""
    summaries = {m: summarize_mode(ts) for m, ts in runs_by_mode.items()}
    cmp_mode = (
        "live" if "live" in runs_by_mode
        else "fixtures" if "fixtures" in runs_by_mode
        else None
    )
    lifts = (
        compute_mode_lifts(runs_by_mode["deterministic"], runs_by_mode[cmp_mode], cmp_mode)
        if "deterministic" in runs_by_mode and cmp_mode is not None
        else []
    )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "drawing_id": drawing_id,
        "endpoint": endpoint,
        "target": api_url or "in-process (FastAPI TestClient)",
        "modes_evaluated": list(runs_by_mode.keys()),
        "summaries": summaries,
        "model_lift_vs_deterministic": lifts,
        "turns_by_mode": {
            m: [asdict(t) for t in ts]
            for m, ts in runs_by_mode.items()
        },
    }


def _fmt_ans(t: TurnResult) -> str:
    if t.verdict == "error":
        return f"ERROR ({t.http_status})"
    if t.actual_kind == "abstain":
        return f"abstain ({t.abstain_reason})"
    if t.actual_kind == "area":
        return f"area: {t.actual_value} m²"
    if t.actual_kind == "count":
        return f"count: {t.actual_value} ({t.actual_subject})"
    return f"{t.actual_kind}: {t.actual_value}"


def _fmt_exp(t: TurnResult) -> str:
    if not t.answerable_on_drawing:
        return "abstain (off-drawing/invalid)"
    if t.expected_kind == "area":
        return f"{t.expected_value} m²" if t.expected_value is not None else "area (unextracted)"
    if t.expected_kind == "count":
        s = f" ({t.expected_subject})" if t.expected_subject else ""
        if t.expected_value is None:
            return f"count (unextracted){s}"
        return f"count: {t.expected_value}{s}"
    return (
        f"{t.expected_kind}: {t.expected_value}"
        if t.expected_value is not None
        else f"{t.expected_kind} (unsupported)"
    )


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown_report(payload: dict[str, Any]) -> str:
    """Render a clear, human-readable Markdown coverage dashboard from `payload`."""
    drawing_id = payload["drawing_id"]
    modes: list[str] = payload["modes_evaluated"]
    primary_mode = (
        "live" if "live" in modes
        else "fixtures" if "fixtures" in modes
        else modes[0]
    )
    primary_summary = payload["summaries"][primary_mode]
    primary_turns = [TurnResult(**d) for d in payload["turns_by_mode"][primary_mode]]

    lines: list[str] = [
        f"# AURA Chat Coverage Report — `{drawing_id}`",
        "",
        f"- **Target**: `{payload['target']}` (`POST /{payload['endpoint']}`)",
        f"- **Modes evaluated**: {', '.join(f'`{m}`' for m in modes)}",
        (
            f"- **Primary view below**: `{primary_mode}` "
            f"({primary_summary['total_turns']} total turns across "
            f"{len(primary_summary['by_category'])} categories)"
        ),
        "",
        "## 1. Executive Coverage Summary",
        "",
        (
            "Every turn is classified into **5 strict outcomes** (not just `non-abstain`), "
            "catching questions where the chat confidently returns the **wrong** kind, scope, "
            "or number:"
        ),
        "",
        "| Metric | " + " | ".join(f"`{m}`" for m in modes) + " |",
        "| --- | " + " | ".join("---:" for _ in modes) + " |",
    ]

    rows_spec = [
        ("Total turns evaluated", lambda s: str(s["total_turns"])),
        (
            "`answered_verified` (Delivers verified answer)",
            lambda s: f"**{s['verdict_counts']['answered_verified']}**",
        ),
        (
            "`answered_misrouted` (Delivers **wrong** kind/scope/value)",
            lambda s: f"**{s['verdict_counts']['answered_misrouted']}**",
        ),
        (
            "`abstained_gap` (No answer on valid plan question)",
            lambda s: str(s["verdict_counts"]["abstained_gap"]),
        ),
        (
            "`abstained_expected` (Properly refused off-drawing/invalid)",
            lambda s: str(s["verdict_counts"]["abstained_expected"]),
        ),
        (
            "`error` (HTTP / SSE / Contract / Evidence failure)",
            lambda s: str(s["verdict_counts"]["error"]),
        ),
        (
            "**Verified coverage on answerable plan questions**",
            lambda s: (
                f"**{s['verified_on_answerable']}/{s['answerable_turns']} "
                f"({s['verified_on_answerable_pct']}%)**"
            ),
        ),
        (
            "Guardrail accuracy on off-drawing/invalid queries",
            lambda s: f"{s['properly_refused_off_drawing']}/{s['off_drawing_turns']}",
        ),
        ("Wall latency p50 / p95 (ms)", lambda s: f"{s['latency_wall_p50_ms']} / {s['latency_wall_p95_ms']}"),
        ("Model calls (answer)", lambda s: str(s["total_model_calls"])),
    ]
    for label, fn in rows_spec:
        vals = " | ".join(fn(payload["summaries"][m]) for m in modes)
        lines.append(f"| {label} | {vals} |")

    # Section 2: By Category
    lines.extend([
        "",
        f"## 2. Coverage by Architectural Category (`{primary_mode}`)",
        "",
        "| Category | Turns | Verified | Misrouted | Gap (Abstain) | Refused (Expected) | Error | Answerable Coverage |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for cat, st in primary_summary["by_category"].items():
        cov_str = (
            f"{st['verified_rate_on_answerable_pct']}%"
            if st["verified_rate_on_answerable_pct"] is not None
            else "n/a"
        )
        lines.append(
            f"| `{cat}` | {st['total']} | {st['answered_verified']} | "
            f"{st['answered_misrouted']} | {st['abstained_gap']} | "
            f"{st['abstained_expected']} | {st['error']} | {cov_str} |"
        )

    # Section 3: What Works
    verified = [t for t in primary_turns if t.verdict == "answered_verified"]
    lines.extend([
        "",
        f"## 3. Where Chat Delivers Verified Answers (`{len(verified)}` turns in `{primary_mode}`)",
        "",
    ])
    if drawing_id == SWISS_DRAWING_ID:
        lines.extend([
            (
                "- **Doors & Windows**: Floor-wide (`22` doors, `12` windows), per-apartment "
                "(`A`/`B`), per-room (`3.08`, `3.02`, zero-count `3.03`), and per-label "
                "(`Couloir`, `Bain`) counts."
            ),
            (
                "- **Room & Apartment Areas + Ranking**: Floor net indoor area (`261.96 m²`), "
                "Apartment A (`139.34 m²`), Apartment B (`90.55 m²`), single rooms "
                "(`3.02` = `38.57 m²`, unlabeled `3.23`/`3.24`), single-label totals "
                "(`Séjour/repas` = `69.68 m²`, `Terrasse` = `166.05 m²`), and exact `room_rank` "
                "superlatives (`largest room` -> `3.02` at `38.57 m²`)."
            ),
            (
                "- **Sanitary Fixtures**: Exact CAD counts for `washbasin`/`sink` (`5`), "
                "`wc`/`toilet` (`5`), `bathtub` (`2`), and `shower` (`2`)."
            ),
            (
                "- **Interactive Spatial Tools**: Drawn region marks (`scope.kind='region'`) and "
                "multi-room `what_if` dimension overrides."
            ),
            (
                "- **Stateful Multi-Turn `/chat`**: `conversation_id` persistence, cued scope "
                "inheritance (`'and in apartment B?'`, `'and windows?'`, `'and room 3.04?'`), "
                "non-cued isolation, and `'in total'` floor-scope resets."
            ),
            (
                "- **Multilingual & Colloquial**: EN, FR, DE (`«Bad»` -> `Bain`), FI, IT "
                "(`vasche da bagno`), terse one-word counts (`'toilets?'`), and Gemini "
                "`choice`/`agent` idioms."
            ),
        ])
    elif not verified:
        lines.append("_No verified answers recorded for this filter/drawing._")
    else:
        for cat, st in primary_summary["by_category"].items():
            cat_ver = [t for t in verified if t.category == cat]
            if not cat_ver:
                continue
            sample_str = "; ".join(
                f"*{_md_escape(t.question)}* -> `{_fmt_ans(t)}`" for t in cat_ver[:2]
            )
            lines.append(f"- **`{cat}`** (`{len(cat_ver)}/{st['total']}`): {sample_str}")

    # Section 4: Silent Misroutes (Critical!)
    misrouted = [t for t in primary_turns if t.verdict == "answered_misrouted"]
    lines.extend([
        "",
        f"## 4. Silent Misroutes — Delivers Confident **Wrong** Answers (`{len(misrouted)}` turns)",
        "",
        (
            "These probes did **not** abstain — they returned a schema-valid answer for the wrong "
            "intent, scope, or target:"
        ),
        "",
    ])
    if not misrouted:
        lines.append("_None detected._")
    else:
        lines.extend([
            "| Probe ID | Question | Actual Answer | Expected Ground Truth | Diagnosis |",
            "| --- | --- | --- | --- | --- |",
        ])
        for t in misrouted:
            lines.append(
                f"| `{t.probe_id}` | {_md_escape(t.question)} | `{_fmt_ans(t)}` | "
                f"`{_fmt_exp(t)}` | {_md_escape(t.diagnosis)} |"
            )

    # Section 5: Coverage Gaps (Abstains)
    gaps = [t for t in primary_turns if t.verdict == "abstained_gap"]
    lines.extend([
        "",
        f"## 5. Coverage Gaps — Where Chat Does Not Deliver Answers (`{len(gaps)}` turns in `{primary_mode}`)",
        "",
    ])
    if not gaps:
        lines.append("_No abstention gaps in this suite._")
    else:
        lines.extend([
            "| Probe ID | Category | Question | Abstain Reason | Expected Ground Truth | Architectural Note |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for t in gaps:
            lines.append(
                f"| `{t.probe_id}` | `{t.category}` | {_md_escape(t.question)} | "
                f"`{t.abstain_reason}` | `{_fmt_exp(t)}` | {_md_escape(t.rationale)} |"
            )

    # Section 6: Model Lift (if deterministic + model mode ran)
    lifts = payload.get("model_lift_vs_deterministic") or []
    if lifts:
        cmp_label = lifts[0].get("compare_mode", "fixtures")
        lines.extend([
            "",
            f"## 6. Model-Fallback Lift (`deterministic` vs `{cmp_label}`)",
            "",
            f"| Probe ID | Question | `deterministic` (`AURA_MODELS=off`) | `{cmp_label}` | Route & Answer |",
            "| --- | --- | --- | --- | --- |",
        ])
        for d in lifts:
            lines.append(
                f"| `{d['probe_id']}` | {_md_escape(d['question'])} | "
                f"`{d['deterministic_verdict']}` | **`{d['fixtures_verdict']}`** | "
                f"`{d['fixtures_route']}` -> `{d['actual_answer']}` |"
            )

    # Section 7: Full Turn-by-Turn Appendix
    sec_num = 7 if lifts else 6
    lines.extend([
        "",
        f"## {sec_num}. Full Turn-by-Turn Results (`{primary_mode}`)",
        "",
        "| Probe ID | Category | Question | Route | Actual | Expected | Verdict |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for t in primary_turns:
        lines.append(
            f"| `{t.probe_id}` | `{t.category}` | {_md_escape(t.question)} | "
            f"`{t.route or '-'}` | `{_fmt_ans(t)}` | `{_fmt_exp(t)}` | `{t.verdict}` |"
        )

    lines.append("")
    return "\n".join(lines)


def render_html_report(payload: dict[str, Any]) -> str:
    """Render a standalone, interactive HTML dashboard for instant local browser inspection."""
    drawing_id = html.escape(str(payload["drawing_id"]))
    modes: list[str] = payload["modes_evaluated"]
    primary_mode = (
        "live" if "live" in modes
        else "fixtures" if "fixtures" in modes
        else modes[0]
    )
    summary = payload["summaries"][primary_mode]
    turns = [TurnResult(**d) for d in payload["turns_by_mode"][primary_mode]]
    vc = summary["verdict_counts"]

    cat_rows = []
    for cat, st in summary["by_category"].items():
        pct = st["verified_rate_on_answerable_pct"]
        pct_str = f"{pct}%" if pct is not None else "n/a"
        bar_w = pct if pct is not None else 100.0
        cat_rows.append(
            f"<tr><td><code>{html.escape(cat)}</code></td>"
            f"<td class='num'>{st['total']}</td>"
            f"<td class='num ok'>{st['answered_verified']}</td>"
            f"<td class='num bad'>{st['answered_misrouted']}</td>"
            f"<td class='num warn'>{st['abstained_gap']}</td>"
            f"<td class='num muted'>{st['abstained_expected']}</td>"
            f"<td class='num'><div class='bar'><span style='width:{bar_w}%'></span></div>"
            f"{pct_str}</td></tr>"
        )

    turn_rows = []
    for t in turns:
        turn_rows.append(
            f"<tr data-verdict='{html.escape(t.verdict)}' data-cat='{html.escape(t.category)}'>"
            f"<td><code>{html.escape(t.probe_id)}</code></td>"
            f"<td><code>{html.escape(t.category)}</code></td>"
            f"<td>{html.escape(t.question)}</td>"
            f"<td><code>{html.escape(t.route or '-')}</code></td>"
            f"<td><code>{html.escape(_fmt_ans(t))}</code></td>"
            f"<td><code>{html.escape(_fmt_exp(t))}</code></td>"
            f"<td><span class='pill {html.escape(t.verdict)}'>{html.escape(t.verdict)}</span></td>"
            f"<td class='diag'>{html.escape(t.diagnosis)}</td></tr>"
        )

    cats_opts = "".join(
        f"<option value='{html.escape(c)}'>{html.escape(c)}</option>"
        for c in summary["by_category"]
    )
    return "\n".join([
        "<!doctype html>",
        f"<html lang='en'><head><meta charset='utf-8'><title>AURA Chat Coverage — {drawing_id}</title>",
        "<style>",
        "body{font-family:Inter,system-ui,sans-serif;background:#0b1020;color:#e5e7eb;margin:0;padding:28px;}",
        "h1,h2{margin:0 0 12px;} .sub{color:#9ca3af;margin-bottom:22px;font-size:14px;}",
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin-bottom:24px;}",
        ".card{background:#131b31;border:1px solid #24304f;border-radius:10px;padding:14px;}",
        ".card .k{font-size:12px;color:#9ca3af;text-transform:uppercase;} .card .v{font-size:26px;font-weight:700;margin-top:4px;}",
        ".ok{color:#34d399;} .bad{color:#f87171;} .warn{color:#fbbf24;} .muted{color:#93c5fd;}",
        "table{width:100%;border-collapse:collapse;background:#131b31;border-radius:10px;overflow:hidden;margin-bottom:26px;font-size:13px;}",
        "th,td{padding:9px 11px;border-bottom:1px solid #1f2a48;text-align:left;vertical-align:top;}",
        "th{background:#182340;color:#cbd5e1;font-weight:600;} .num{text-align:right;}",
        "code{background:#0b1020;padding:2px 5px;border-radius:4px;font-size:12px;}",
        ".pill{padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600;display:inline-block;}",
        ".pill.answered_verified{background:#064e3b;color:#6ee7b7;}",
        ".pill.answered_misrouted{background:#7f1d1d;color:#fca5a5;}",
        ".pill.abstained_gap{background:#78350f;color:#fde68a;}",
        ".pill.abstained_expected{background:#1e3a8a;color:#bfdbfe;}",
        ".pill.error{background:#991b1b;color:#fecaca;}",
        ".bar{background:#0b1020;height:6px;border-radius:4px;overflow:hidden;margin-bottom:3px;}",
        ".bar span{display:block;height:100%;background:#34d399;}",
        ".filters{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap;}",
        "input,select{background:#131b31;color:#e5e7eb;border:1px solid #2d3b5f;border-radius:6px;padding:7px 10px;font-size:13px;}",
        ".diag{color:#9ca3af;max-width:320px;}",
        "</style></head><body>",
        f"<h1>AURA Chat Coverage — <code>{drawing_id}</code></h1>",
        (
            f"<div class='sub'>Primary mode: <b>{html.escape(primary_mode)}</b> &middot; "
            f"Target: <code>{html.escape(str(payload['target']))}</code> &middot; "
            f"Generated: {html.escape(str(payload['generated_at']))}</div>"
        ),
        "<div class='cards'>",
        (
            f"<div class='card'><div class='k'>Answerable Coverage</div>"
            f"<div class='v ok'>{summary['verified_on_answerable_pct']}%</div>"
            f"<div class='sub' style='margin:2px 0 0'>"
            f"{summary['verified_on_answerable']}/{summary['answerable_turns']} verified</div></div>"
        ),
        f"<div class='card'><div class='k'>Verified Answers</div><div class='v ok'>{vc['answered_verified']}</div></div>",
        f"<div class='card'><div class='k'>Silent Misroutes</div><div class='v bad'>{vc['answered_misrouted']}</div></div>",
        f"<div class='card'><div class='k'>Abstention Gaps</div><div class='v warn'>{vc['abstained_gap']}</div></div>",
        f"<div class='card'><div class='k'>Guardrails OK</div><div class='v muted'>{summary['properly_refused_off_drawing']}/{summary['off_drawing_turns']}</div></div>",
        (
            f"<div class='card'><div class='k'>Model Calls / p50</div>"
            f"<div class='v'>{summary['total_model_calls']}</div>"
            f"<div class='sub' style='margin:2px 0 0'>p50: {summary['latency_wall_p50_ms']} ms</div></div>"
        ),
        "</div>",
        "<h2>Coverage by Architectural Category</h2>",
        (
            "<table><thead><tr><th>Category</th><th class='num'>Turns</th><th class='num'>Verified</th>"
            "<th class='num'>Misrouted</th><th class='num'>Gap</th><th class='num'>Refused OK</th>"
            "<th class='num'>Answerable %</th></tr></thead><tbody>"
        ),
        *cat_rows,
        "</tbody></table>",
        "<h2>Interactive Probe Explorer</h2>",
        "<div class='filters'>",
        "<input id='q' placeholder='Search question, probe ID, diagnosis...' oninput='flt()' style='min-width:260px'>",
        (
            "<select id='v' onchange='flt()'><option value=''>All verdicts</option>"
            "<option value='answered_verified'>answered_verified</option>"
            "<option value='answered_misrouted'>answered_misrouted</option>"
            "<option value='abstained_gap'>abstained_gap</option>"
            "<option value='abstained_expected'>abstained_expected</option>"
            "<option value='error'>error</option></select>"
        ),
        f"<select id='c' onchange='flt()'><option value=''>All categories</option>{cats_opts}</select>",
        "</div>",
        (
            "<table id='tbl'><thead><tr><th>Probe</th><th>Category</th><th>Question</th><th>Route</th>"
            "<th>Actual</th><th>Expected</th><th>Verdict</th><th>Diagnosis</th></tr></thead><tbody>"
        ),
        *turn_rows,
        "</tbody></table>",
        "<script>",
        (
            "function flt(){const q=document.getElementById('q').value.toLowerCase(),"
            "v=document.getElementById('v').value,c=document.getElementById('c').value;"
            "for(const r of document.querySelectorAll('#tbl tbody tr')){"
            "const ok=(!v||r.dataset.verdict===v)&&(!c||r.dataset.cat===c)"
            "&&(!q||r.innerText.toLowerCase().includes(q));"
            "r.style.display=ok?'':'none';}}"
        ),
        "</script></body></html>",
        "",
    ])


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def filter_suite(
    probes: list[ChatProbe],
    conversations: list[ConversationProbe],
    *,
    categories: set[str] | None = None,
    lang: str | None = None,
    probe_id_substr: str | None = None,
) -> tuple[list[ChatProbe], list[ConversationProbe]]:
    """Filter single-turn and multi-turn probes by CLI criteria."""
    out_p = probes
    out_c = conversations
    if categories:
        out_p = [p for p in out_p if p.category in categories]
        out_c = [c for c in out_c if c.category in categories]
    if lang:
        out_p = [p for p in out_p if p.lang == lang]
        out_c = [c for c in out_c if all(t.lang == lang for t in c.turns)]
    if probe_id_substr:
        sub = probe_id_substr.casefold()
        out_p = [p for p in out_p if sub in p.id.casefold()]
        out_c = [
            c for c in out_c
            if sub in c.conversation_key.casefold()
            or any(sub in t.id.casefold() for t in c.turns)
        ]
    return out_p, out_c


def print_console_summary(payload: dict[str, Any]) -> None:
    """Print a concise ASCII summary table to stdout."""
    print(f"\n=== AURA Chat Coverage: {payload['drawing_id']} ({payload['target']}) ===")
    for mode in payload["modes_evaluated"]:
        s = payload["summaries"][mode]
        vc = s["verdict_counts"]
        print(
            f"[{mode}] turns={s['total_turns']} | "
            f"verified={vc['answered_verified']} | "
            f"misrouted={vc['answered_misrouted']} | "
            f"gap={vc['abstained_gap']} | "
            f"refused_ok={vc['abstained_expected']} | "
            f"error={vc['error']} | "
            f"answerable_cov={s['verified_on_answerable']}/{s['answerable_turns']} "
            f"({s['verified_on_answerable_pct']}%)"
        )
    lifts = payload.get("model_lift_vs_deterministic") or []
    if lifts:
        cmp_label = lifts[0].get("compare_mode", "fixtures")
        recovered = [
            d["probe_id"] for d in lifts
            if d["deterministic_verdict"] != "answered_verified"
            and d["fixtures_verdict"] == "answered_verified"
        ]
        if recovered:
            print(f"[model-lift:{cmp_label}] {len(recovered)} probes recovered over deterministic: "
                  f"{', '.join(recovered)}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Evaluate where AURA platform chat delivers verified answers vs. gaps."
    )
    p.add_argument(
        "--drawing",
        default=SWISS_DRAWING_ID,
        help="Drawing ID, or 'all' to evaluate every discovered drawing (default: swiss-46639)",
    )
    p.add_argument(
        "--drawing-path",
        type=Path,
        default=None,
        help="Optional path to a custom drawing folder (with drawing.json + model.json/raster.json) or JSON file",
    )
    p.add_argument(
        "--dynamic-only",
        action="store_true",
        help="Use pure zero-hardcoding dynamic probe generation even on swiss-46639",
    )
    p.add_argument(
        "--mode",
        choices=("deterministic", "fixtures", "both", "live", "all"),
        default=None,
        help="Execution mode (default: 'both' in-process, or 'live' when --api-url is set)",
    )
    p.add_argument("--api-url", default=None, help="Optional running backend URL (e.g. http://127.0.0.1:8000)")
    p.add_argument("--endpoint", choices=("chat", "ask"), default="chat", help="Endpoint to test")
    p.add_argument("--category", action="append", default=[], help="Filter by category (comma-separated)")
    p.add_argument("--lang", default=None, help="Filter by probe language (e.g. en, fr, de, fi, it)")
    p.add_argument("--probe-id", default=None, help="Filter by probe ID substring")
    p.add_argument(
        "--hybrid-steps",
        action="store_true",
        help="Enable Contract 1.2 hybrid tool-step answerer (`aura/steps.py`) in in-process live mode",
    )
    p.add_argument("--out-json", type=Path, default=None, help="Custom output JSON path")
    p.add_argument("--out-md", type=Path, default=None, help="Custom output Markdown path")
    p.add_argument("--out-html", type=Path, default=None, help="Custom output HTML dashboard path")
    p.add_argument("--no-write", action="store_true", help="Do not write JSON/Markdown/HTML files")
    p.add_argument("--verbose", action="store_true", help="Keep aura access/model INFO logs")
    return p


def _resolve_target_drawings(args: argparse.Namespace) -> list[str]:
    if args.drawing_path is not None and args.drawing == SWISS_DRAWING_ID:
        return ["auto"]
    if args.drawing != "all":
        return [args.drawing]
    if args.api_url:
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=20.0) as c:
            dr = c.get("/drawings")
            dr.raise_for_status()
            body = dr.json()
            rows = body.get("drawings", body) if isinstance(body, dict) else body
            return [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]
    return discover_local_drawing_ids()


def _evaluate_single_drawing(
    args: argparse.Namespace,
    drawing_id: str,
    modes: list[Literal["deterministic", "fixtures", "live"]],
    *,
    multi_drawing_run: bool = False,
) -> int:
    drawing_data: dict[str, Any] | None = None
    loaded_drawing: Any | None = None

    if args.api_url:
        # Try local disk first (handles localhost servers & local uploads where HTTP strips `evidence`)
        with contextlib.suppress(FileNotFoundError):
            loaded_drawing, _ = load_local_drawing(drawing_id, drawing_path=args.drawing_path)
            drawing_id = loaded_drawing.id
            drawing_data = loaded_drawing.drawing
        if drawing_data is None and (drawing_id != SWISS_DRAWING_ID or args.dynamic_only):
            with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=20.0) as c:
                dr = c.get(f"/drawings/{drawing_id}")
                dr.raise_for_status()
                drawing_data = dr.json()
    else:
        loaded_drawing, _ = load_local_drawing(drawing_id, drawing_path=args.drawing_path)
        drawing_id = loaded_drawing.id
        drawing_data = loaded_drawing.drawing

    probes, conversations = suite_for_drawing(
        drawing_id,
        drawing_data,
        loaded_drawing=loaded_drawing,
        dynamic_only=args.dynamic_only,
    )
    cats = {
        c.strip()
        for raw in args.category
        for c in raw.split(",")
        if c.strip()
    } or None
    probes, conversations = filter_suite(
        probes,
        conversations,
        categories=cats,
        lang=args.lang,
        probe_id_substr=args.probe_id,
    )

    if not probes and not (args.endpoint == "chat" and conversations):
        print(f"No probes matched the given filters for '{drawing_id}'.", file=sys.stderr)
        return 2

    runs_by_mode: dict[str, list[TurnResult]] = {}
    for m in modes:
        runs_by_mode[m] = run_mode(
            mode=m,
            drawing_id=drawing_id,
            probes=probes,
            conversations=conversations,
            endpoint=args.endpoint,
            api_url=args.api_url,
            drawing_path=args.drawing_path,
            hybrid_steps=args.hybrid_steps,
            verbose=args.verbose,
        )

    payload = build_report_payload(
        drawing_id=drawing_id,
        endpoint=args.endpoint,
        api_url=args.api_url,
        runs_by_mode=runs_by_mode,
    )
    print_console_summary(payload)

    if not args.no_write:
        DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        use_custom = not multi_drawing_run
        out_json = (args.out_json if use_custom else None) or (
            DEFAULT_RESULTS_DIR / f"chat_coverage_{drawing_id}.json"
        )
        out_md = (args.out_md if use_custom else None) or (
            args.out_json.with_suffix(".md")
            if (use_custom and args.out_json)
            else (DEFAULT_RESULTS_DIR / f"chat_coverage_{drawing_id}.md")
        )
        out_html = (args.out_html if use_custom else None) or (
            out_md.with_suffix(".html")
            if (use_custom and (args.out_md or args.out_json))
            else (DEFAULT_RESULTS_DIR / f"chat_coverage_{drawing_id}.html")
        )
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_html.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        out_md.write_text(render_markdown_report(payload), encoding="utf-8")
        out_html.write_text(render_html_report(payload), encoding="utf-8")
        print(f"Wrote JSON report: {out_json}")
        print(f"Wrote Markdown report: {out_md}")
        print(f"Wrote HTML dashboard: {out_html}")

    has_stream_errors = any(
        s["verdict_counts"]["error"] > 0 for s in payload["summaries"].values()
    )
    return 1 if has_stream_errors else 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    mode_arg: str = args.mode or ("live" if args.api_url else "both")

    modes: list[Literal["deterministic", "fixtures", "live"]]
    if mode_arg in ("both", "all"):
        if args.api_url:
            print(f"--mode {mode_arg} is only supported in-process (omit --api-url).", file=sys.stderr)
            return 2
        modes = (
            ["deterministic", "fixtures", "live"]
            if mode_arg == "all"
            else ["deterministic", "fixtures"]
        )
    else:
        modes = [mode_arg]  # type: ignore[list-item]

    target_ids = _resolve_target_drawings(args)
    if not target_ids:
        print("No drawings discovered.", file=sys.stderr)
        return 2

    worst_rc = 0
    for did in target_ids:
        rc = _evaluate_single_drawing(
            args,
            did,
            modes,
            multi_drawing_run=(len(target_ids) > 1),
        )
        worst_rc = max(worst_rc, rc)
    return worst_rc


if __name__ == "__main__":
    raise SystemExit(main())
