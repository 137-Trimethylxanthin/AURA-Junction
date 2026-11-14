"""Tests for `aura/agent_client.py` & `aura/pipeline.py` hybrid/agent routing, live candidate
sweeps, qualifier-aware semantic caching, deictic reference-region visual search, multi-turn
color/filter follow-ups, multi-plan `ScopeProject` links, and self-deadline cancellation.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aura import tools
from aura.agent_client import MAX_AGENT_SLOTS, cad_bbox_to_pct, px_bbox_to_pct
from aura.config import Settings
from aura.contract import AskResponse
from aura.main import create_app
from tests.conftest import FRONTEND_DRAWING, SWISS_IMAGE, no_nulls
from tests.test_pdf_upload import make_pdf, upload_pdf, wait_status

SWISS = "swiss-46639"


def parse_sse_raw(raw_text: str) -> tuple[list[tuple[str, dict]], list[str]]:
    """Return `(events, comments)` from an SSE HTTP response body."""
    events: list[tuple[str, dict]] = []
    comments: list[str] = []
    cur_event: str | None = None
    cur_data: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith(":"):
            comments.append(line)
            continue
        if line == "":
            if cur_event is not None and cur_data:
                events.append((cur_event, json.loads("\n".join(cur_data))))
            cur_event = None
            cur_data = []
        elif line.startswith("event:"):
            cur_event = line[6:].strip()
        elif line.startswith("data:"):
            cur_data.append(line[5:].strip())
    return events, comments


class MockGeminiDecisionsTransport:
    """Deterministic `Transport` for `GeminiClient` (`decide_hybrid_route`, `pick_agent_subject`, `run_check`)."""

    def __init__(self, handler=None):
        self.calls: list[tuple[str, dict]] = []
        self._handler = handler

    async def post(self, path: str, body: dict) -> tuple[int, dict]:
        self.calls.append((path, body))
        if self._handler is not None:
            res = self._handler(path, body)
            if res is not None:
                return res
        questions = body.get("questions") or {}
        answers: dict[str, dict] = {}
        if "on_drawing" in questions or "cache_match" in questions or "exact_sufficient" in questions:
            # Default: on drawing, exact solver insufficient -> escalate to deep agent
            answers["on_drawing"] = {"noul": 0.96}
            answers["exact_sufficient"] = {"noul": 0.06}
            answers["intent"] = {
                "choice": "other",
                "confidence": 0.90,
                "probabilities": {"other": 0.90},
            }
            answers["subject"] = {
                "choice": "other",
                "confidence": 0.90,
                "probabilities": {"other": 0.90},
            }
            answers["scope"] = {
                "choice": "floor",
                "confidence": 0.95,
                "probabilities": {"floor": 0.95},
            }
            if "cache_match" in questions:
                answers["cache_match"] = {
                    "choice": "none",
                    "confidence": 0.95,
                    "probabilities": {"none": 0.95},
                }
        elif "subject" in questions and len(questions) == 1:
            # pick_agent_subject
            prompt_txt = json.dumps(body.get("input") or []).lower()
            subj = "door" if "door" in prompt_txt else "window" if "window" in prompt_txt else "other"
            conf = 0.92 if subj != "other" else 0.88
            answers["subject"] = {
                "choice": subj,
                "confidence": conf,
                "probabilities": {subj: conf},
            }
        else:
            # Decisions tile check (`run_check`): score all tiles high, anchors low
            for qk in questions:
                val = 0.12 if "neg" in qk or "anchor" in qk else 0.91
                answers[qk] = {"noul": val}
        return 200, {"answers": answers, "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 10}}


class MockSidecarState:
    """Programmable mock `agent-service` supporting `/health`, `/files`, `/runs`, `/runs/{id}/events`, `/cancel`."""

    def __init__(self, handler=None, *, stream_delay_s: float = 0.0, hang_forever: bool = False):
        self.files: dict[str, str] = {}
        self.runs: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.deleted_files: list[str] = []
        self.handler = handler
        self.stream_delay_s = stream_delay_s
        self.hang_forever = hang_forever
        self.health_ok = True

    def build_transport(self) -> httpx.MockTransport:
        async def _dispatch(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            method = request.method.upper()
            if method == "GET" and path == "/health":
                if not self.health_ok:
                    return httpx.Response(503, json={"ok": False})
                return httpx.Response(200, json={"ok": True})
            if method == "POST" and path in ("/files", "/files/register"):
                fid = f"f-{len(self.files) + 1}"
                self.files[fid] = request.read().decode("latin-1", errors="ignore")[:120]
                return httpx.Response(200, json={"file_id": fid})
            if method == "DELETE" and path.startswith("/files/"):
                self.deleted_files.append(path.rsplit("/", 1)[-1])
                return httpx.Response(204)
            if method == "POST" and path == "/runs":
                payload = json.loads(request.content.decode("utf-8"))
                rid = f"run-{len(self.runs) + 1}"
                rec = {"run_id": rid, **payload}
                self.runs.append(rec)
                return httpx.Response(202, json={"run_id": rid, "status": "running"})
            if method == "POST" and path.endswith("/cancel"):
                rid = path.split("/")[2]
                self.cancelled.append(rid)
                return httpx.Response(200, json={"status": "cancelled"})
            if method == "GET" and path.endswith("/events"):
                rid = path.split("/")[2]
                run_rec = next(r for r in self.runs if r["run_id"] == rid)
                if self.hang_forever:
                    await asyncio.sleep(30.0)
                    return httpx.Response(504)
                if self.stream_delay_s > 0:
                    await asyncio.sleep(self.stream_delay_s)
                events = (
                    self.handler(run_rec)
                    if self.handler is not None
                    else default_sidecar_events(run_rec)
                )
                sse_body = "".join(f"data: {json.dumps(ev)}\n\n" for ev in events)
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=sse_body.encode("utf-8"),
                )
            return httpx.Response(404, json={"error": "not found"})

        return httpx.MockTransport(_dispatch)


def default_sidecar_events(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "run_started", "model": "gemini-4-pro", "files": [{"name": "drawing.png"}]},
        {
            "type": "answers",
            "answers": [
                {
                    "question": run_rec["questions"][0],
                    "answer": 2,
                    "unit": "count",
                    "confidence": 0.88,
                    "method": "Counted double-leaf door blocks in CAD/image.",
                    "check": "Verified each double swing arc.",
                    "evidence": [
                        {"kind": "door", "bbox_px": [100, 120, 140, 160]},
                        {"kind": "door", "bbox_px": [300, 220, 345, 265]},
                    ],
                }
            ],
        },
        {
            "type": "run_finished",
            "status": "done",
            "usage": {"requests": 4, "input_tokens": 2500, "output_tokens": 400},
        },
    ]


def make_hybrid_app(
    tmp_path: Path,
    *,
    sidecar: MockSidecarState | None = None,
    gemini: MockGeminiDecisionsTransport | None = None,
    answerer: str = "hybrid",
    **extra_settings: Any,
):
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copy(SWISS_IMAGE, assets / "original.png")
    d = tools.load_file(FRONTEND_DRAWING, assets_dir=assets)
    gem = gemini or MockGeminiDecisionsTransport()
    sc = sidecar or MockSidecarState()
    s = Settings(
        DATA_DIR=tmp_path / "data",
        AURA_BUILD_DIR=tmp_path / "nobuild",
        GEMINI_API_KEY="test-dummy-key",
        AURA_SIDECAR_URL="http://sidecar.test",
        AURA_ANSWERER=answerer,
        **extra_settings,
    )
    return create_app(
        s,
        transport=gem,
        sidecar_transport=sc.build_transport(),
        extra_drawings=[d],
        use_gemini=True,
    )


# ---- 1. Coordinate transforms (DXF vs IFC vs Raster/PDF) ------------------------------------


def test_coordinate_transforms_dxf_vs_ifc_margin_and_clamping():
    dxf_drawing = {
        "kind": "cad_dxf",
        "frame": {"dxf_min": [0.0, 0.0], "dxf_max": [8.0, 8.0], "margin_m": 1.0, "y_flip": True, "px": [1000, 1000]},
    }
    # Effective span with margin_m=1.0 is [-1..9] = 10m; [1..3, 5..7] -> x=[20..40], y=[20..40]
    b_dxf = cad_bbox_to_pct(dxf_drawing, [1.0, 5.0, 3.0, 7.0])
    assert b_dxf == {"x": 20.0, "y": 20.0, "w": 20.0, "h": 20.0}

    # IFC frame ALREADY includes ±MARGIN in dxf_min/dxf_max -> margin_m must NOT be applied again!
    ifc_drawing = {
        "kind": "ifc",
        "frame": {"dxf_min": [-1.0, -1.0], "dxf_max": [9.0, 9.0], "margin_m": 1.0, "y_flip": True, "px": [1000, 1000]},
    }
    b_ifc = cad_bbox_to_pct(ifc_drawing, [1.0, 5.0, 3.0, 7.0])
    assert b_ifc == {"x": 20.0, "y": 20.0, "w": 20.0, "h": 20.0}

    # Point-like INSERT block (x0==x1, y0==y1) expands to clickable 1.2% box; out-of-bounds clamps to [0, 100]
    pt_box = cad_bbox_to_pct(dxf_drawing, [4.0, 4.0, 4.0, 4.0])
    assert pt_box is not None and pt_box["w"] == pytest.approx(1.2) and pt_box["h"] == pytest.approx(1.2)
    clamped = px_bbox_to_pct(dxf_drawing, [-50, -20, 500, 1200], image_px=[1000, 1000])
    assert clamped == {"x": 0.0, "y": 0.0, "w": 50.0, "h": 100.0}
    assert px_bbox_to_pct(dxf_drawing, [200, 200, 200, 300], image_px=[1000, 1000]) is None


# ---- 2. Hybrid exact `<2ms` fast-path & instant `<2ms` apartment color-coding ---------------


def test_hybrid_exact_fast_path_and_instant_apartment_color_coding(tmp_path):
    gem = MockGeminiDecisionsTransport()
    sc = MockSidecarState()
    app = make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)
    with TestClient(app) as c:
        # Exact window count bypasses both Gemini Decisions and sidecar (check call disabled by empty client? check runs 1 tile call; verify sidecar runs == 0!)
        r_win = c.post("/ask", json={"drawing_id": SWISS, "question": "How many windows are drawn on this floor plan?"})
        assert r_win.status_code == 200
        body_win = r_win.json()
        AskResponse.model_validate(body_win)
        no_nulls(body_win)
        assert body_win["route"] == "template"
        assert body_win["calls"] == 0
        assert body_win["answer"] == {"kind": "count", "value": 12, "subject": "window"}
        assert len(sc.runs) == 0

        # Instant `<2ms` apartment count + categorical color groups (`c1`, `c2`) with 0 Gemini calls & 0 sidecar runs
        gem_before = len(gem.calls)
        r_apt = c.post("/ask", json={"drawing_id": SWISS, "question": "How many apartments are on this floor plan?"})
        assert r_apt.status_code == 200
        body_apt = r_apt.json()
        AskResponse.model_validate(body_apt)
        no_nulls(body_apt)
        assert body_apt["answer"] == {"kind": "count", "value": 2, "subject": "apartment"}
        assert body_apt["calls"] == 0 and len(gem.calls) == gem_before and len(sc.runs) == 0
        groups = body_apt["evidence"]["groups"]
        assert [g["id"] for g in groups] == ["unit-A", "unit-B"]
        assert [g["color"] for g in groups] == ["c1", "c2"]
        room_groups = {rm["group_id"] for rm in body_apt["evidence"]["rooms"] if rm.get("group_id")}
        assert room_groups == {"unit-A", "unit-B"}
        assert sum(1 for rm in body_apt["evidence"]["rooms"] if rm.get("group_id")) >= 10


# ---- 3. North-window qualifier guard, candidate scan, dropped_ids & smart semantic cache -----


def test_north_windows_streams_candidates_drops_non_north_and_caches_qualifier_safely(tmp_path):
    """ Guardrail #1:
    1) Pre-cached 'How many windows are drawn on this floor plan?' (12 windows) MUST NOT poison
       'Show all north windows' (even if the model proposes cache_match='c_00'!).
    2) 'Show all north windows' streams all 12 candidates in `scan-candidates`, streams live
       `focus_rect` from `view_image`, emits `dropped_ids` (8 dropped, 4 kept), and returns ONLY
       the 4 confirmed north windows in `AskResponse.evidence.boxes`.
    3) Subsequent 'Highlight the north-facing windows' DOES hit semantic cache (`route == 'cache'`,
       0 new sidecar runs) because canonical qualifiers `{'dir:north'}` match!
    """
    swiss_data = json.loads(FRONTEND_DRAWING.read_text())
    # Grab the first 4 actual window bboxes in swiss-46639 so scan-candidates IDs match!
    d_loaded = tools.load_file(FRONTEND_DRAWING)
    all_win_boxes = tools.locate(d_loaded, "window", {"kind": "floor"}).boxes
    assert len(all_win_boxes) == 12
    north_four = all_win_boxes[:4]
    dropped_eight = all_win_boxes[4:]

    # Convert the 4 north window Pct boxes to pixel bboxes on swiss_data["frame"]["px"]
    w_px, h_px = swiss_data["frame"]["px"]
    north_ev = [
        {
            "kind": "window",
            "bbox_px": [
                round(b.x / 100.0 * w_px, 1),
                round(b.y / 100.0 * h_px, 1),
                round((b.x + b.w) / 100.0 * w_px, 1),
                round((b.y + b.h) / 100.0 * h_px, 1),
            ],
        }
        for b in north_four
    ]

    def sidecar_handler(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"type": "run_started", "model": "gemini-4-pro", "files": [{"name": "drawing.png"}]},
            {
                "type": "tool_started",
                "call_id": "v1",
                "seq": 2,
                "tool": "view_image",
                "path": f"crops/crop_0_0_{w_px}_{int(h_px * 0.3)}.png",
            },
            {"type": "tool_finished", "call_id": "v1", "tool": "view_image", "seconds": 0.25},
            {
                "type": "answers",
                "answers": [
                    {
                        "question": run_rec["questions"][0],
                        "answer": 4,
                        "unit": "count",
                        "confidence": 0.93,
                        "method": "Inspected north facade (top edge) and confirmed 4 north-facing windows.",
                        "check": "Dropped 8 south/east/west windows.",
                        "evidence": north_ev,
                    }
                ],
            },
            {
                "type": "run_finished",
                "status": "done",
                "usage": {"requests": 5, "input_tokens": 3200, "output_tokens": 510},
            },
        ]

    def gemini_handler(path: str, body: dict):
        questions = body.get("questions") or {}
        if "on_drawing" in questions or "cache_match" in questions or "exact_sufficient" in questions:
            # Always propose c00 with high confidence (0.95) to prove canonical_qualifiers
            # blocks unqualified c00 on Turn 2, and accepts north-qualified c00 on Turn 3!
            ans = {
                "on_drawing": {"noul": 0.98},
                "exact_sufficient": {"noul": 0.05},
                "intent": {"choice": "count", "confidence": 0.92, "probabilities": {"count": 0.92}},
                "subject": {"choice": "window", "confidence": 0.95, "probabilities": {"window": 0.95}},
                "scope": {"choice": "floor", "confidence": 0.95, "probabilities": {"floor": 0.95}},
            }
            if "cache_match" in questions:
                ans["cache_match"] = {"choice": "c00", "confidence": 0.95, "probabilities": {"c00": 0.95}}
            return 200, {"answers": ans, "usageMetadata": {"promptTokenCount": 90}}
        return None

    sc = MockSidecarState(handler=sidecar_handler)
    gem = MockGeminiDecisionsTransport(handler=gemini_handler)
    app = make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)
    with TestClient(app) as c:
        # Step 1: Pre-cache all 12 windows
        r_all = c.post("/ask", json={"drawing_id": SWISS, "question": "How many windows are drawn on this floor plan?"})
        assert r_all.status_code == 200 and r_all.json()["answer"]["value"] == 12

        # Step 2: Ask "Show all north windows" over /chat
        r_chat = c.post("/chat", json={"drawing_id": SWISS, "message": "Show all north windows"})
        assert r_chat.status_code == 200
        events, _ = parse_sse_raw(r_chat.text)
        ev_names = [name for name, _ in events]
        assert ev_names[0] == "turn" and "understood" in ev_names and ev_names[-2:] == ["answer", "done"]

        # Unqualified 12-window cache was rejected -> sidecar ran once!
        assert len(sc.runs) == 1

        # Verify scan-candidates streamed all 12 windows during pending
        scan_res = next(
            d for name, d in events if name == "tool_result" and d.get("node_id") == "scan-candidates"
        )
        assert len(scan_res["evidence"]["boxes"]) == 12

        # Verify view_image emitted live focus_rect on tool_call
        crop_call = next(
            d["node"]
            for name, d in events
            if name == "tool_call" and d["node"].get("tag") == "Agent · view_image"
        )
        assert crop_call["tool"]["args"]["focus_rect"]["y"] == 0.0
        assert crop_call["tool"]["args"]["focus_rect"]["w"] == 100.0

        # Verify agent-filter emitted kept_ids (4) and dropped_ids (8)
        filt_call = next(
            d["node"]
            for name, d in events
            if name == "tool_call" and d["node"]["id"] == "agent-filter"
        )
        assert set(filt_call["tool"]["args"]["kept_ids"]) == {b.id for b in north_four}
        assert set(filt_call["tool"]["args"]["dropped_ids"]) == {b.id for b in dropped_eight}

        # Verify final AskResponse.evidence.boxes has ONLY the 4 confirmed north windows!
        final_resp = next(d["response"] for name, d in events if name == "answer")
        AskResponse.model_validate(final_resp)
        no_nulls(final_resp)
        assert final_resp["route"] == "agent"
        assert final_resp["method"] == "agent_estimate"
        assert final_resp["answer"] == {"kind": "count", "value": 4, "subject": "window"}
        assert len(final_resp["evidence"]["boxes"]) == 4
        assert {b["id"] for b in final_resp["evidence"]["boxes"]} == {b.id for b in north_four}

        # Step 3: Ask "Highlight the north-facing windows" -> c_00 IS now "Show all north windows"
        # with matching canonical qualifier {'dir:north'}, so semantic cache serves the 4 windows!
        r_sem = c.post("/ask", json={"drawing_id": SWISS, "question": "Highlight the north-facing windows"})
        assert r_sem.status_code == 200
        sem_body = r_sem.json()
        AskResponse.model_validate(sem_body)
        no_nulls(sem_body)
        assert sem_body["route"] == "cache"
        assert sem_body["answer"] == {"kind": "count", "value": 4, "subject": "window"}
        assert len(sem_body["evidence"]["boxes"]) == 4
        assert len(sc.runs) == 1  # 0 new sidecar runs!


# ---- 4. Deictic reference-region visual search ("how many spots like this are there") -------


def test_deictic_spots_like_this_with_region_searches_whole_floor(tmp_path):
    """Guardrail #2: `scope.kind == 'region'` + `'how many spots like this are there'` treats
    `scope.rect` as a VISUAL REFERENCE TEMPLATE (not a spatial filter), instructs agent-service
    to inspect Step 1 crop then search the ENTIRE floor plan in Step 2, and returns `ScopeFloor`."""

    def deictic_handler(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
        q = run_rec["questions"][0]
        assert "REFERENCE REGION VISUAL SEARCH" in q
        assert "Step 1" in q and "Step 2: Search the ENTIRE floor plan" in q
        assert "pixel_bbox=" in q and "cad_bbox=" in q
        spots = [
            {"kind": "spot", "bbox_px": [120 + i * 80, 150 + i * 30, 145 + i * 80, 175 + i * 30], "label": f"Drain {i + 1}"}
            for i in range(6)
        ]
        return [
            {"type": "run_started", "model": "gemini-4-pro", "files": [{"name": "drawing.png"}]},
            {
                "type": "answers",
                "answers": [
                    {
                        "question": q,
                        "answer": 6,
                        "unit": "count",
                        "confidence": 0.89,
                        "method": "Cropped the marked reference spot (floor drain symbol) and matched 6 identical symbols across the entire floor plan.",
                        "check": "Verified each symbol at 2x zoom.",
                        "evidence": spots,
                    }
                ],
            },
            {
                "type": "run_finished",
                "status": "done",
                "usage": {"requests": 6, "input_tokens": 4100, "output_tokens": 600},
            },
        ]

    # Force pick_agent_subject -> "other" because "floor drain spots" is not in fixed Subject enum
    def gem_other(path: str, body: dict):
        if set((body.get("questions") or {}).keys()) == {"subject"}:
            return 200, {
                "answers": {"subject": {"choice": "other", "confidence": 0.93, "probabilities": {"other": 0.93}}},
                "usageMetadata": {"promptTokenCount": 40},
            }
        return None

    sc = MockSidecarState(handler=deictic_handler)
    gem = MockGeminiDecisionsTransport(handler=gem_other)
    app = make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)
    with TestClient(app) as c:
        r = c.post(
            "/ask",
            json={
                "drawing_id": SWISS,
                "question": "how many spots like this are there",
                "scope": {
                    "kind": "region",
                    "rect": {"x": 20.0, "y": 30.0, "w": 10.0, "h": 10.0},
                    "label": "Marked spot",
                },
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        AskResponse.model_validate(body)
        no_nulls(body)
        assert len(sc.runs) == 1
        # Returned scope spans the ENTIRE floor plan (not restricted to the marked reference rect)
        assert body["scope"] == {"kind": "floor"}
        assert body["answer"]["kind"] == "note"
        assert body["answer"]["number"] == 6.0
        assert body["answer"]["unit"] == "count"
        assert len(body["evidence"]["boxes"]) == 6
        assert all(b["subject"] == "label" for b in body["evidence"]["boxes"])


# ---- 5. Multi-turn conversational follow-ups ("color in different colors" & "show only north")


def test_multiturn_chat_color_in_different_colors_and_show_only_north(tmp_path):
    """Guardrail #3:
    Turn 1: 'What is the total room area on this floor plan?' -> exact AnswerArea (11 rooms).
    Turn 2: 'color in different colors' -> `<2ms`, 0 model calls, 0 sidecar runs, attaches `c1..c6`
            `evidence.groups` + `evidence.rooms[*].group_id` while preserving exact area!
    Turn 3: 'show only north' -> injects `Prior conversation context` with Turn 2's active rooms &
            positions, escalates to `agent-service`, and returns only the north rooms & area!
    """
    d_loaded = tools.load_file(FRONTEND_DRAWING)
    north_room_ids = [rm["room_id"] for rm in d_loaded.drawing["rooms"][:3] if rm.get("area_m2")]
    expected_north_area = round(
        sum(float(rm["area_m2"]) for rm in d_loaded.drawing["rooms"] if rm["room_id"] in north_room_ids), 2
    )

    def followup_sidecar(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
        q = run_rec["questions"][0]
        assert "Prior conversation context" in q
        assert north_room_ids[0] in q
        return [
            {"type": "run_started", "model": "gemini-4-pro", "files": [{"name": "drawing.png"}]},
            {
                "type": "answers",
                "answers": [
                    {
                        "question": q,
                        "answer": expected_north_area,
                        "unit": "m2",
                        "confidence": 0.91,
                        "method": "Filtered the prior turn's rooms to the 3 rooms along the north facade.",
                        "check": "Verified room polygon y-positions and summed printed m2.",
                        "evidence": [{"kind": "room", "id": rid} for rid in north_room_ids],
                    }
                ],
            },
            {
                "type": "run_finished",
                "status": "done",
                "usage": {"requests": 3, "input_tokens": 1800, "output_tokens": 310},
            },
        ]

    sc = MockSidecarState(handler=followup_sidecar)
    gem = MockGeminiDecisionsTransport()
    app = make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)
    with TestClient(app) as c:
        # Turn 1: exact floor area
        t1_evs, _ = parse_sse_raw(
            c.post("/chat", json={"drawing_id": SWISS, "message": "What is the total room area on this floor plan?"}).text
        )
        conv_id = t1_evs[0][1]["conversation_id"]
        a1 = next(d["response"] for n, d in t1_evs if n == "answer")
        assert a1["answer"]["kind"] == "area"
        orig_area = a1["answer"]["value"]
        assert len(sc.runs) == 0

        # Turn 2: "color in different colors" -> 0 Gemini calls, 0 sidecar runs, c1..c6 groups!
        gem_before = len(gem.calls)
        t2_evs, _ = parse_sse_raw(
            c.post(
                "/chat",
                json={"drawing_id": SWISS, "conversation_id": conv_id, "message": "color in different colors"},
            ).text
        )
        a2 = next(d["response"] for n, d in t2_evs if n == "answer")
        AskResponse.model_validate(a2)
        no_nulls(a2)
        assert a2["answer"] == a1["answer"] and a2["answer"]["value"] == orig_area
        assert len(gem.calls) == gem_before and len(sc.runs) == 0
        assert len(a2["evidence"]["groups"]) >= 2
        assert all(g["color"] in {"c1", "c2", "c3", "c4", "c5", "c6"} for g in a2["evidence"]["groups"])
        assert all(rm.get("group_id") for rm in a2["evidence"]["rooms"] if rm.get("included"))

        # Turn 3: "show only north" -> escalates to sidecar with Prior conversation context
        t3_evs, _ = parse_sse_raw(
            c.post(
                "/chat",
                json={"drawing_id": SWISS, "conversation_id": conv_id, "message": "show only north"},
            ).text
        )
        a3 = next(d["response"] for n, d in t3_evs if n == "answer")
        AskResponse.model_validate(a3)
        no_nulls(a3)
        assert len(sc.runs) == 1
        assert a3["answer"]["kind"] == "area"
        assert a3["answer"]["value"] == pytest.approx(expected_north_area)
        assert [rm["room_id"] for rm in a3["evidence"]["rooms"]] == north_room_ids


# ---- 6. Multi-plan ScopeProject + stairs/lead-to links + cross-floor conversation -----------


def test_multiplan_scope_project_stairs_color_groups_links_and_floor_switching(tmp_path):
    """Multi-page house PDF + `ScopeProject`: uploads both floor PDFs to sidecar, injects
    `Multi-plan project context`, maps `file`/`target_file`/`group`/`lead_to_px` into
    `Evidence.groups` + `Evidence.links`, and allows floor switching on the same conversation."""

    def stair_project_handler(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
        assert len(run_rec["file_ids"]) == 2
        q = run_rec["questions"][0]
        assert "Multi-plan project context" in q
        assert "drawing.pdf" in q and "drawing_2.pdf" in q
        return [
            {
                "type": "run_started",
                "model": "gemini-4-pro",
                "files": [{"name": "drawing.pdf"}, {"name": "drawing_2.pdf"}],
            },
            {
                "type": "answers",
                "answers": [
                    {
                        "question": q,
                        "answer": 2,
                        "unit": "count",
                        "confidence": 0.92,
                        "method": "Matched main stair S1 and service stair S2 from EG (drawing.pdf) to 1. OG (drawing_2.pdf).",
                        "check": "Aligned stairwell shafts across both floor plans.",
                        "evidence": [
                            {
                                "kind": "stair",
                                "file": "drawing.pdf",
                                "target_file": "drawing_2.pdf",
                                "bbox_px": [200, 300, 320, 420],
                                "image_px": [1000, 1000],
                                "lead_to_px": [260, 180],
                                "group": "stair-S1",
                                "group_label": "Main Stair S1 (EG -> 1. OG)",
                                "label": "Up to 1. OG",
                            },
                            {
                                "kind": "stair",
                                "file": "drawing_2.pdf",
                                "bbox_px": [700, 500, 800, 620],
                                "image_px": [1000, 1000],
                                "lead_to_px": [750, 400],
                                "group": "stair-S2",
                                "group_label": "Service Stair S2",
                                "label": "Landing 1. OG",
                            },
                        ],
                    }
                ],
            },
            {
                "type": "run_finished",
                "status": "done",
                "usage": {"requests": 7, "input_tokens": 5000, "output_tokens": 720},
            },
        ]

    sc = MockSidecarState(handler=stair_project_handler)
    gem = MockGeminiDecisionsTransport()
    app = make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)
    with TestClient(app) as c:
        up = upload_pdf(c, make_pdf(["Erdgeschoss EG", "1. Obergeschoss 1. OG"]), "House.pdf").json()
        d1 = up["drawing_id"]
        d2 = f"{d1}-p2"
        proj_id = f"proj-{d1.removeprefix('up-')}"
        assert wait_status(c, d1)["status"] == "ready"

        chat_r = c.post(
            "/chat",
            json={
                "drawing_id": d1,
                "message": "Show all stairs and where they lead to in different color combinations",
                "scope": {"kind": "project", "project_id": proj_id, "drawing_ids": [d1, d2]},
            },
        )
        assert chat_r.status_code == 200, chat_r.text
        evs, _ = parse_sse_raw(chat_r.text)
        conv_id = evs[0][1]["conversation_id"]
        ans = next(d["response"] for n, d in evs if n == "answer")
        AskResponse.model_validate(ans)
        no_nulls(ans)

        assert ans["answer"]["kind"] == "note"
        assert ans["answer"]["number"] == 2.0
        assert ans["answer"]["unit"] == "count"
        assert len(ans["evidence"]["groups"]) == 2
        g1, g2 = ans["evidence"]["groups"]
        assert g1["id"] == "stair-S1" and g1["color"] == "c1" and g1.get("target_drawing_id") == d2
        assert g2["id"] == "stair-S2" and g2["color"] == "c2"
        assert len(ans["evidence"]["links"]) == 2
        assert ans["evidence"]["links"][0]["drawing_id"] == d1
        assert ans["evidence"]["links"][0]["target_drawing_id"] == d2
        assert {b["drawing_id"] for b in ans["evidence"]["boxes"]} == {d1, d2}

        # Switching active floor to d2 within the same project conversation succeeds (200),
        # whereas switching to an unrelated drawing (swiss-46639) is rejected with 400!
        switch_ok = c.post(
            "/chat",
            json={"drawing_id": d2, "conversation_id": conv_id, "message": "Show all stairs on this floor"},
        )
        assert switch_ok.status_code == 200
        switch_bad = c.post(
            "/chat",
            json={"drawing_id": SWISS, "conversation_id": conv_id, "message": "How many doors?"},
        )
        assert switch_bad.status_code == 400


# ---- 7. Table 3b conversions (0.50 vs 0.49, None -> not_on_drawing, AnswerQuantity + CSV) ---


def test_table_3b_confidence_boundary_not_on_drawing_and_quantity_csv(tmp_path):
    outcomes = [
        # 1) confidence == 0.50 -> accepted AnswerQuantity ("m")
        {"answer": 14.8, "unit": "m", "confidence": 0.50, "method": "Summed corridor segments.", "check": "Checked scale bar.", "evidence": []},
        # 2) confidence == 0.49 -> abstain("low_confidence")
        {"answer": 3, "unit": "count", "confidence": 0.49, "method": "Blurry scan.", "check": "Uncertain.", "evidence": []},
        # 3) answer is None -> abstain("not_on_drawing")
        {"answer": None, "unit": None, "confidence": 0.90, "method": "Checked title block and schedules; acoustic STC rating is not stated.", "check": "Confirmed absent.", "evidence": []},
    ]
    idx = 0

    def seq_handler(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal idx
        item = outcomes[idx]
        idx += 1
        return [
            {"type": "run_started", "model": "gemini-4-pro", "files": [{"name": "drawing.png"}]},
            {"type": "answers", "answers": [{"question": run_rec["questions"][0], **item}]},
            {"type": "run_finished", "status": "done", "usage": {"requests": 2, "input_tokens": 800, "output_tokens": 150}},
        ]

    sc = MockSidecarState(handler=seq_handler)
    app = make_hybrid_app(tmp_path, sidecar=sc)
    with TestClient(app) as c:
        # 1) AnswerQuantity ("m") at exact 0.50 boundary + UTF-8 BOM CSV export
        q1 = c.post("/ask", json={"drawing_id": SWISS, "question": "What is the travel distance to the exit?"}).json()
        AskResponse.model_validate(q1)
        no_nulls(q1)
        assert q1["answer"] == {"kind": "quantity", "value": 14.8, "unit": "m"}
        c.portal.call(c.app.state.svc.writer.flush)
        csv_r = c.get(f"/answers/{q1['answer_id']}/evidence.csv")
        assert csv_r.status_code == 200 and csv_r.content.startswith(b"\xef\xbb\xbf")
        assert "answer," in csv_r.text and "14.8 m" in csv_r.text

        # 2) 0.49 -> abstain("low_confidence")
        q2 = c.post("/ask", json={"drawing_id": SWISS, "question": "How many pocket doors are drawn?"}).json()
        AskResponse.model_validate(q2)
        assert q2["answer"] == {"kind": "abstain", "reason": "low_confidence"}

        # 3) None -> abstain("not_on_drawing")
        q3 = c.post("/ask", json={"drawing_id": SWISS, "question": "What is the STC acoustic rating of wall W1?"}).json()
        AskResponse.model_validate(q3)
        assert q3["answer"]["kind"] == "abstain" and q3["answer"]["reason"] == "not_on_drawing"


# ---- 8. Heartbeat `: progress`, Semaphore(2) queue, self-deadline cancel & exact mode -------


def test_sse_heartbeat_self_deadline_cancel_exact_mode_and_health(tmp_path):
    # A) Slow sidecar run emits `: progress {s}` SSE heartbeat comment before `answer`
    sc_slow = MockSidecarState(stream_delay_s=0.12)
    app_slow = make_hybrid_app(tmp_path / "hb", sidecar=sc_slow)
    with TestClient(app_slow) as c:
        c.app.state.svc.heartbeat_s = 0.03
        r = c.post("/chat", json={"drawing_id": SWISS, "message": "How many double doors are there?"})
        evs, comments = parse_sse_raw(r.text)
        assert any(cm.startswith(": progress ") for cm in comments)
        assert [n for n, _ in evs][-2:] == ["answer", "done"]
        h = c.get("/health").json()
        assert h["answerer"] == "hybrid" and h["agent_ok"] is True

    # B) Self-deadline (`turn_deadline_s=6` -> `self_budget_s=1.0s`) cancels sidecar run via
    #    `POST /runs/{id}/cancel`, books worst-case 40 slots, and returns `abstain('low_confidence')`!
    sc_hang = MockSidecarState(hang_forever=True)
    app_hang = make_hybrid_app(tmp_path / "hang", sidecar=sc_hang, AURA_TURN_DEADLINE_S=6)
    with TestClient(app_hang) as c:
        r_to = c.post("/ask", json={"drawing_id": SWISS, "question": "How many double doors are there?"})
        assert r_to.status_code == 200
        b_to = r_to.json()
        AskResponse.model_validate(b_to)
        assert b_to["answer"] == {"kind": "abstain", "reason": "low_confidence"}
        assert sc_hang.cancelled == ["run-1"]
        assert b_to["calls"] >= MAX_AGENT_SLOTS

    # C) `AURA_ANSWERER=exact` makes 0 Gemini/sidecar calls on open-ended questions & reports `exact`
    sc_exact = MockSidecarState()
    gem_exact = MockGeminiDecisionsTransport()
    app_exact = make_hybrid_app(tmp_path / "ex", sidecar=sc_exact, gemini=gem_exact, answerer="exact")
    with TestClient(app_exact) as c:
        assert c.get("/health").json()["answerer"] == "exact"
        r_ex = c.post("/ask", json={"drawing_id": SWISS, "question": "How many double doors are there?"}).json()
        assert r_ex["answer"] == {"kind": "abstain", "reason": "not_supported_yet"}
        assert r_ex["calls"] == 0 and len(gem_exact.calls) == 0 and len(sc_exact.runs) == 0


@pytest.mark.anyio
async def test_concurrency_semaphore_emits_queue_position_and_caps_waiting(tmp_path):
    gate = asyncio.Event()

    async def gated_dispatch(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/files":
            return httpx.Response(200, json={"file_id": "f-1"})
        if request.url.path == "/runs":
            return httpx.Response(202, json={"run_id": "run-q", "status": "running"})
        if request.url.path.endswith("/events"):
            await gate.wait()
            sse_body = "".join(
                f"data: {json.dumps(ev)}\n\n"
                for ev in default_sidecar_events({"questions": ["double doors?"]})
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body.encode())
        return httpx.Response(200, json={})

    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copy(SWISS_IMAGE, assets / "original.png")
    d = tools.load_file(FRONTEND_DRAWING, assets_dir=assets)
    s = Settings(
        DATA_DIR=tmp_path / "data",
        AURA_BUILD_DIR=tmp_path / "nobuild",
        GEMINI_API_KEY="test-dummy-key",
        AURA_SIDECAR_URL="http://sidecar.test",
        AURA_ANSWERER="agent",
        AURA_AGENT_RUNS_PER_IP_HOUR=50,
        AURA_AGENT_RUNS_PER_DAY=100,
    )
    app = create_app(
        s,
        transport=MockGeminiDecisionsTransport(),
        sidecar_transport=httpx.MockTransport(gated_dispatch),
        extra_drawings=[d],
        use_gemini=True,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://aura.test") as ac:
            t1 = asyncio.create_task(
                ac.post("/chat", json={"drawing_id": SWISS, "message": "double doors 1?"})
            )
            t2 = asyncio.create_task(
                ac.post("/chat", json={"drawing_id": SWISS, "message": "double doors 2?"})
            )
            await asyncio.sleep(0.08)  # let t1 & t2 occupy both Semaphore(2) slots
            t3 = asyncio.create_task(
                ac.post("/chat", json={"drawing_id": SWISS, "message": "double doors 3?"})
            )
            await asyncio.sleep(0.05)
            gate.set()
            r1, r2, r3 = await asyncio.gather(t1, t2, t3)
            assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200
            evs3, _ = parse_sse_raw(r3.text)
            prog = [d["queue_position"] for n, d in evs3 if n == "progress"]
            assert prog == [1, 0]


# ---- 9. Natural-language area threshold ("can you only show things with a 30m² area or higher")


def test_area_threshold_30m2_or_higher_never_misfires_keyword_area(tmp_path):
    """Guardrail #4: Unicode `²` + threshold/filter prompt `'can you only show things with a 30m²
    area or higher'` MUST NOT misfire `router.py`'s keyword `'area'` (`total_area`) or match a
    cached floor-area response; it escalates to `agent-service` with pre-indexed rooms and
    highlights ONLY the `>= 30 m²` spaces (never small rooms like `7.83 m²` Cuisine)."""
    from aura.gemini.understand import canonical_qualifiers
    from aura.router import route

    d_loaded = tools.load_file(FRONTEND_DRAWING)
    prompt = "can you only show things with a 30m² area or higher"

    # 1) Router guard: must NEVER collapse into `intent == 'area'`!
    routed = route(prompt, d_loaded.ctx)
    assert routed.intent == "unknown" and "qualifier_guard" in routed.matched

    # 2) Canonical qualifier tags capture restriction + 30 m² area + >= polarity (no Unicode ² \b bug!)
    quals = canonical_qualifiers(prompt)
    assert {"flt:restrict", "area:30m2", "cmp:gt"} <= quals
    assert canonical_qualifiers("What is the total room area on this floor plan?") == frozenset()

    big_rooms = [
        rm for rm in d_loaded.drawing["rooms"]
        if rm.get("area_m2") is not None and float(rm["area_m2"]) >= 30.0
    ]
    assert len(big_rooms) >= 2
    big_ids = [rm["room_id"] for rm in big_rooms]
    big_sum = round(sum(float(rm["area_m2"]) for rm in big_rooms), 2)

    def threshold_sidecar(run_rec: dict[str, Any]) -> list[dict[str, Any]]:
        q = run_rec["questions"][0]
        assert "Pre-indexed rooms on this floor plan" in q
        for bid in big_ids:
            assert bid in q
        return [
            {"type": "run_started", "model": "gemini-4-pro", "files": [{"name": "drawing.png"}]},
            {
                "type": "answers",
                "answers": [
                    {
                        "question": prompt,
                        "answer": len(big_ids),
                        "unit": "count",
                        "confidence": 0.96,
                        "method": f"Filtered pre-indexed room areas to spaces >= 30.0 m² ({', '.join(big_ids)}; {big_sum} m² combined).",
                        "check": "Excluded all rooms under 30 m² (such as 7.83 m² Cuisine).",
                        "evidence": [{"kind": "room", "id": rid} for rid in big_ids],
                    }
                ],
            },
            {
                "type": "run_finished",
                "status": "done",
                "usage": {"requests": 2, "input_tokens": 1600, "output_tokens": 240},
            },
        ]

    # Even if Gemini Decisions mischievously proposes cache_match='c00' (total floor area) AND
    # exact_sufficient=0.99, BOTH hard_guard AND canonical_qualifiers block it!
    def adversarial_gemini(path: str, body: dict):
        questions = body.get("questions") or {}
        if "on_drawing" in questions or "cache_match" in questions:
            ans: dict[str, dict] = {"on_drawing": {"noul": 0.99}}
            if "cache_match" in questions:
                ans["cache_match"] = {"choice": "c00", "confidence": 0.98, "probabilities": {"c00": 0.98}}
            if "exact_sufficient" in questions:
                ans["exact_sufficient"] = {"noul": 0.99}
            return 200, {"answers": ans, "usageMetadata": {"promptTokenCount": 70}}
        if set(questions.keys()) == {"subject"}:
            return 200, {
                "answers": {"subject": {"choice": "room", "confidence": 0.94, "probabilities": {"room": 0.94}}},
                "usageMetadata": {"promptTokenCount": 40},
            }
        return None

    sc = MockSidecarState(handler=threshold_sidecar)
    gem = MockGeminiDecisionsTransport(handler=adversarial_gemini)
    app = make_hybrid_app(tmp_path, sidecar=sc, gemini=gem)
    with TestClient(app) as c:
        # Pre-cache total floor area first
        r_total = c.post("/ask", json={"drawing_id": SWISS, "question": "What is the total room area on this floor plan?"})
        assert r_total.status_code == 200 and r_total.json()["answer"]["kind"] == "area"

        # Now ask the natural-language >= 30m² prompt
        r_thr = c.post("/ask", json={"drawing_id": SWISS, "question": prompt})
        assert r_thr.status_code == 200, r_thr.text
        body = r_thr.json()
        AskResponse.model_validate(body)
        no_nulls(body)
        assert len(sc.runs) == 1
        assert body["route"] == "agent"
        assert body["method"] == "agent_estimate"
        # Highlights ONLY the >= 30m² rooms (never 7.83 m² Cuisine!)
        returned_ids = [rm["room_id"] for rm in body["evidence"]["rooms"] if rm.get("included")]
        assert returned_ids == big_ids
        assert all(float(rm["area_m2"]) >= 30.0 for rm in body["evidence"]["rooms"] if rm.get("included"))

