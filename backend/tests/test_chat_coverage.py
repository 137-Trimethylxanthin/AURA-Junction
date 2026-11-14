"""Tests for the AURA chat coverage evaluation tool (`eval/chat_coverage*.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval.chat_coverage import (
    StreamCapture,
    build_report_payload,
    build_turn_result,
    check_no_nulls,
    classify_response,
    filter_suite,
    main,
    parse_sse_text,
    render_markdown_report,
    run_mode,
    validate_chat_capture,
    validate_response_invariants,
)
from eval.chat_coverage_cases import (
    CATEGORY_DESCRIPTIONS,
    SWISS_46639_CONVERSATIONS,
    SWISS_46639_PROBES,
    SWISS_DRAWING_ID,
    ProbeExpectation,
    build_dynamic_probes,
    suite_for_drawing,
)
from tests.conftest import FRONTEND_DRAWING, make_app


def _valid_count_response(
    value: int = 2,
    subject: str = "bathtub",
    scope: dict | None = None,
) -> dict:
    boxes = [
        {
            "id": f"{subject}-{i:02d}",
            "x": float(10 + i),
            "y": 20.0,
            "w": 2.0,
            "h": 2.0,
            "subject": subject,
        }
        for i in range(value)
    ]
    return {
        "contract_version": "1.2",
        "answer": {"kind": "count", "value": value, "subject": subject},
        "evidence": {"boxes": boxes, "rooms": []},
        "detected_lang": "en",
        "canonical_en": f"How many {subject}s",
        "scope": scope or {"kind": "floor"},
        "method": "cad_geometry",
        "trace": [
            {
                "id": "answer",
                "title": "Answer",
                "tag": "exact",
                "body": f"{value} {subject}s.",
                "kind": "exact",
                **({"evidence_ids": [b["id"] for b in boxes]} if boxes else {}),
            }
        ],
        "calls": 0,
        "cost_usd": 0.0,
        "latency_ms": 1,
        "source": "api",
        "route": "template",
        "conversation_id": "c-1234567890abcdef1234567890abcdef",
        "turn_id": "t-1234567890abcdef",
    }


def test_swiss_probe_catalog_integrity():
    single_ids = [p.id for p in SWISS_46639_PROBES]
    conv_turn_ids = [t.id for c in SWISS_46639_CONVERSATIONS for t in c.turns]
    all_ids = single_ids + conv_turn_ids
    assert len(all_ids) == len(set(all_ids)), "Duplicate probe IDs in swiss-46639 suite"
    assert len(all_ids) >= 100

    cats_used = {p.category for p in SWISS_46639_PROBES} | {
        c.category for c in SWISS_46639_CONVERSATIONS
    }
    assert cats_used == set(CATEGORY_DESCRIPTIONS.keys())

    # User's explicit examples must be covered: all doors, stairs, living room floor size.
    questions_lower = " | ".join(p.question.lower() for p in SWISS_46639_PROBES)
    assert "all doors" in questions_lower
    assert "how many stairs" in questions_lower
    assert "floor size of the living room" in questions_lower


def test_dynamic_probe_builder_matches_swiss_ground_truth():
    data = json.loads(FRONTEND_DRAWING.read_text(encoding="utf-8"))
    probes, convs = build_dynamic_probes(data)
    by_id = {p.id: p for p in probes}

    assert by_id["dyn_door_floor"].expectation.expected_value == 22
    assert by_id["dyn_window_floor"].expectation.expected_value == 12
    assert by_id["dyn_washbasin_floor"].expectation.expected_value == 5
    assert by_id["dyn_wc_floor"].expectation.expected_value == 5
    assert by_id["dyn_bathtub_floor"].expectation.expected_value == 2
    assert by_id["dyn_shower_floor"].expectation.expected_value == 2
    assert by_id["dyn_apartments_count"].expectation.expected_value == 2
    assert by_id["dyn_indoor_rooms_count"].expectation.expected_value == 20
    assert by_id["dyn_floor_area"].expectation.expected_value == pytest.approx(261.96)
    assert by_id["dyn_unit_A_area"].expectation.expected_value == pytest.approx(139.34)
    assert by_id["dyn_unit_B_area"].expectation.expected_value == pytest.approx(90.55)
    assert len(convs) == 2
    assert [t.expectation.expected_value for t in convs[0].turns] == [13, 9, 22]
    assert [t.expectation.expected_value for t in convs[1].turns] == [
        pytest.approx(38.57),
        pytest.approx(7.83),
        pytest.approx(261.96),
    ]

    with pytest.raises(ValueError, match="requires drawing_data"):
        suite_for_drawing("custom-plan-99", None)


def test_sse_parser_edge_cases():
    events, errs = parse_sse_text("")
    assert events == [] and errs == ["empty SSE response stream"]

    # CRLF + standalone `: progress 15` heartbeat block + no-space `data:{...}`
    raw = (
        ": progress 15\r\n\r\n"
        ": keepalive\r\n"
        "event: turn\r\n"
        "data:{\"conversation_id\":\"c-1\",\"turn_id\":\"t-1\"}\r\n\r\n"
    )
    events, errs = parse_sse_text(raw)
    assert errs == []
    assert events == [("turn", {"conversation_id": "c-1", "turn_id": "t-1"})]

    # Malformed blocks
    bad = (
        "not_a_kv_line\n\n"
        "data: {\"x\": 1}\n\n"
        "event: understood\ndata: {bad json\n\n"
        "event: done\ndata: [1, 2, 3]\n\n"
    )
    events, errs = parse_sse_text(bad)
    assert events == []
    assert len(errs) == 5
    assert any("malformed SSE line" in e for e in errs)
    assert any("missing 'event:' field" in e for e in errs)
    assert any("invalid JSON data" in e for e in errs)
    assert any("must be a JSON object" in e for e in errs)


def test_contract_and_evidence_validators():
    resp = _valid_count_response(value=2, subject="bathtub")
    assert validate_response_invariants(resp) == []

    # Null check: only `.area_m2` may be None
    with_room_null = dict(resp) | {
        "evidence": {
            "boxes": resp["evidence"]["boxes"],
            "rooms": [{"room_id": "3.99", "included": False, "area_m2": None}],
        }
    }
    assert check_no_nulls(with_room_null) == []
    assert check_no_nulls({"explanation": None}) == ["forbidden null at $.explanation"]

    # Evidence box count mismatch
    bad_box_count = dict(resp) | {"answer": {"kind": "count", "value": 5, "subject": "bathtub"}}
    errs = validate_response_invariants(bad_box_count)
    assert any("count/box mismatch" in e for e in errs)

    # Room count > 0 with empty `evidence.rooms`
    bad_room_empty = dict(resp) | {
        "answer": {"kind": "count", "value": 2, "subject": "room"},
        "evidence": {"boxes": [], "rooms": []},
        "trace": [{"id": "answer", "title": "A", "tag": "t", "body": "b", "kind": "exact"}],
    }
    errs = validate_response_invariants(bad_room_empty)
    assert any("room count mismatch" in e for e in errs)

    # Area sum mismatch
    bad_area = dict(resp) | {
        "answer": {"kind": "area", "value": 50.0, "unit": "m2"},
        "evidence": {
            "boxes": [],
            "rooms": [{"room_id": "3.02", "included": True, "area_m2": 38.57}],
        },
        "trace": [{"id": "answer", "title": "A", "tag": "t", "body": "b", "kind": "exact"}],
    }
    errs = validate_response_invariants(bad_area)
    assert any("area sum mismatch" in e for e in errs)

    # Missing trace evidence_id reference
    bad_trace = dict(resp) | {
        "trace": [
            {
                "id": "answer",
                "title": "A",
                "tag": "t",
                "body": "b",
                "kind": "exact",
                "evidence_ids": ["bathtub-99"],
            }
        ]
    }
    errs = validate_response_invariants(bad_trace)
    assert any("missing evidence_id" in e for e in errs)


def test_chat_stream_sequence_validation():
    resp = _valid_count_response()
    good_cap = StreamCapture(
        http_status=200,
        content_type="text/event-stream; charset=utf-8",
        events=[
            ("turn", {"conversation_id": resp["conversation_id"], "turn_id": resp["turn_id"]}),
            ("understood", {"detected_lang": "en", "canonical_en": resp["canonical_en"], "route": "template"}),
            ("answer", {"response": resp}),
            ("done", {}),
        ],
    )
    parsed, errs = validate_chat_capture(good_cap)
    assert errs == [] and parsed is not None

    # SSE timeout error frame -> flagged as error
    err_cap = StreamCapture(
        http_status=200,
        content_type="text/event-stream",
        events=[
            ("turn", {"conversation_id": "c-1", "turn_id": "t-1"}),
            ("error", {"code": "timeout", "error": "The answer took too long."}),
            ("done", {}),
        ],
    )
    parsed, errs = validate_chat_capture(err_cap)
    assert parsed is None and any("SSE error event (timeout)" in e for e in errs)

    # Dangling tool_call without tool_result
    dangling_cap = StreamCapture(
        http_status=200,
        content_type="text/event-stream",
        events=[
            ("turn", {"conversation_id": resp["conversation_id"], "turn_id": resp["turn_id"]}),
            ("understood", {"detected_lang": "en", "canonical_en": resp["canonical_en"], "route": "template"}),
            ("tool_call", {"node": {"id": "read"}}),
            ("answer", {"response": resp}),
            ("done", {}),
        ],
    )
    _, errs = validate_chat_capture(dangling_cap)
    assert any("dangling tool_call" in e for e in errs)


def test_five_bucket_classification_logic():
    resp_count = _valid_count_response(value=2, subject="bathtub")
    abstain_resp = dict(resp_count) | {
        "answer": {"kind": "abstain", "reason": "unclear_question"},
        "evidence": {"boxes": [], "rooms": []},
        "method": "none",
        "trace": [{"id": "answer", "title": "A", "tag": "t", "body": "b", "kind": "info"}],
    }

    # 1. answered_verified
    v, _ = classify_response(
        resp_count,
        ProbeExpectation(True, "count", expected_value=2, expected_subject="bathtub", expected_scope={"kind": "floor"}),
    )
    assert v == "answered_verified"

    # 2. answered_misrouted: wrong kind (e.g. count instead of area)
    v, diag = classify_response(
        resp_count,
        ProbeExpectation(True, "area", expected_value=69.68),
    )
    assert v == "answered_misrouted" and "Wrong answer kind" in diag

    # 3. answered_misrouted: wrong scope
    v, diag = classify_response(
        resp_count,
        ProbeExpectation(True, "count", expected_value=2, expected_subject="bathtub", expected_scope={"kind": "unit", "unit": "A"}),
    )
    assert v == "answered_misrouted" and "Wrong scope" in diag

    # 4. answered_misrouted: wrong value
    v, diag = classify_response(
        resp_count,
        ProbeExpectation(True, "count", expected_value=5, expected_subject="bathtub"),
    )
    assert v == "answered_misrouted" and "Wrong value" in diag

    # 5. answered_misrouted: answered an off-drawing question
    v, diag = classify_response(
        resp_count,
        ProbeExpectation(False, "abstain"),
    )
    assert v == "answered_misrouted" and "Should have abstained" in diag

    # 6. answered_misrouted: unextracted feature (expected_value=None)
    v, diag = classify_response(
        resp_count,
        ProbeExpectation(True, "count", expected_value=None),
    )
    assert v == "answered_misrouted" and "Unverified feature" in diag

    # 7. abstained_gap vs abstained_expected
    v_gap, _ = classify_response(abstain_resp, ProbeExpectation(True, "count", expected_value=1))
    v_ok, _ = classify_response(abstain_resp, ProbeExpectation(False, "abstain"))
    assert v_gap == "abstained_gap"
    assert v_ok == "abstained_expected"


def test_http_error_paths_empty_message_and_unknown_drawing(tmp_path):
    """Boundary/error cases: empty message (422) and unknown drawing (404) become `error` verdicts."""
    from eval.chat_coverage import _send_turn_http

    with TestClient(make_app(tmp_path)) as client:
        cap_empty = _send_turn_http(
            client, endpoint="chat", drawing_id=SWISS_DRAWING_ID, message=""
        )
        assert cap_empty.http_status == 422
        tr_empty = build_turn_result(
            probe_id="edge_empty",
            category="out_of_scope_and_invalid",
            drawing_id=SWISS_DRAWING_ID,
            lang="en",
            question="",
            expectation=ProbeExpectation(False, "abstain"),
            tags=(),
            mode="deterministic",
            endpoint="chat",
            capture=cap_empty,
        )
        assert tr_empty.verdict == "error" and "HTTP 422" in tr_empty.diagnosis

        cap_404 = _send_turn_http(
            client, endpoint="chat", drawing_id="missing-plan", message="How many doors?"
        )
        assert cap_404.http_status == 404
        tr_404 = build_turn_result(
            probe_id="edge_404",
            category="out_of_scope_and_invalid",
            drawing_id="missing-plan",
            lang="en",
            question="How many doors?",
            expectation=ProbeExpectation(True, "count", expected_value=22),
            tags=(),
            mode="deterministic",
            endpoint="chat",
            capture=cap_404,
        )
        assert tr_404.verdict == "error" and "HTTP 404" in tr_404.diagnosis


def test_full_swiss_coverage_both_modes_and_reports(tmp_path: Path):
    """End-to-end verification of `swiss-46639` across `deterministic` and `fixtures` modes."""
    out_json = tmp_path / "cov.json"
    out_md = tmp_path / "cov.md"
    out_html = tmp_path / "cov.html"
    rc = main([
        "--drawing", SWISS_DRAWING_ID,
        "--mode", "both",
        "--out-json", str(out_json),
        "--out-md", str(out_md),
        "--out-html", str(out_html),
    ])
    assert rc == 0
    assert out_json.is_file() and out_md.is_file() and out_html.is_file()

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    md = out_md.read_text(encoding="utf-8")
    html_doc = out_html.read_text(encoding="utf-8")
    assert "# AURA Chat Coverage Report — `swiss-46639`" in md
    assert "Silent Misroutes" in md and "Coverage Gaps" in md
    assert "Interactive Probe Explorer" in html_doc

    det = payload["summaries"]["deterministic"]
    fx = payload["summaries"]["fixtures"]
    assert det["total_turns"] == fx["total_turns"] == 109
    assert det["verdict_counts"]["error"] == 0
    assert fx["verdict_counts"]["error"] == 0
    assert fx["verdict_counts"]["answered_verified"] == det["verdict_counts"]["answered_verified"] + 3

    # Verify key verdicts, Contract 1.2 AnswerNote ranking, silent misroutes, and gaps
    fx_by_id = {t["probe_id"]: t for t in payload["turns_by_mode"]["fixtures"]}
    assert fx_by_id["doors_all_floor"]["verdict"] == "answered_verified"
    assert fx_by_id["doors_check_all_phrasing"]["verdict"] == "answered_verified"
    assert fx_by_id["largest_room_on_floor"]["verdict"] == "answered_verified"
    assert fx_by_id["stairs_how_many"]["verdict"] == "abstained_gap"
    assert fx_by_id["staircase_area_trap"]["verdict"] == "abstained_gap"
    assert fx_by_id["living_room_floor_size"]["verdict"] == "answered_misrouted"
    assert fx_by_id["living_room_area_explicit"]["verdict"] == "answered_verified"
    assert fx_by_id["living_room_in_apt_a_area"]["verdict"] == "answered_misrouted"
    assert fx_by_id["invalid_unquoted_sauna_area_trap"]["verdict"] == "answered_misrouted"
    assert fx_by_id["off_drawing_ceiling_height"]["verdict"] == "abstained_expected"
    assert fx_by_id["sleeping_places_apt_a_recorded"]["verdict"] == "answered_verified"
    assert fx_by_id["colloquial_ways_in_and_out_flat_b"]["verdict"] == "answered_verified"


def test_ask_endpoint_and_cli_filters():
    probes, convs = filter_suite(
        list(SWISS_46639_PROBES),
        list(SWISS_46639_CONVERSATIONS),
        categories={"spatial_interactive_tools"},
    )
    assert len(probes) == 5 and convs == []
    it_probes, _ = filter_suite(list(SWISS_46639_PROBES), [], lang="it")
    assert [p.id for p in it_probes] == ["it_bathtubs_no_false_bagno_scope"]
    ask_turns = run_mode(
        mode="deterministic",
        drawing_id=SWISS_DRAWING_ID,
        probes=probes,
        conversations=[],
        endpoint="ask",
    )
    assert len(ask_turns) == 5
    assert all(t.verdict == "answered_verified" for t in ask_turns)

    payload = build_report_payload(
        drawing_id=SWISS_DRAWING_ID,
        endpoint="ask",
        api_url=None,
        runs_by_mode={"deterministic": ask_turns},
    )
    assert "POST /ask" in render_markdown_report(payload)

    # Empty filter match returns exit code 2
    assert main(["--probe-id", "definitely_no_such_probe_xyz", "--no-write"]) == 2
    assert main(["--mode", "both", "--api-url", "http://127.0.0.1:9", "--no-write"]) == 2


def test_live_http_api_url_sse_end_to_end(tmp_path: Path):
    """Verify `--api-url` over a real TCP `uvicorn` HTTP SSE server (single + multi-turn)."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    app = make_app(tmp_path / "srv", AURA_IP_AGENT_PER_HOUR=100_000)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)

        out_json = tmp_path / "http_cov.json"
        out_md = tmp_path / "http_cov.md"
        rc = main([
            "--api-url", base_url,
            "--category", "doors_and_openings,multi_turn_conversations",
            "--out-json", str(out_json),
            "--out-md", str(out_md),
        ])
        assert rc == 0
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        summary = payload["summaries"]["live"]
        assert summary["total_turns"] == 23  # 14 door/opening probes + 9 multi-turn turns
        assert summary["verdict_counts"]["error"] == 0
        assert summary["verdict_counts"]["answered_verified"] == 19
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def _create_brand_new_dxf_upload_dir(base_dir: Path, drawing_id: str = "up-newplan9001") -> Path:
    """Create a brand-new 2-apartment German DXF plan (`C` & `D`, rooms `1.01`–`1.05`) via `parse_dxf`."""
    import math

    import ezdxf

    from aura.parse_worker import parse_dxf

    dxf_path = base_dir / "newplan.dxf"
    doc = ezdxf.new()
    m = doc.modelspace()
    # Outer building envelope
    m.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True, dxfattribs={"layer": "WALL"})
    # Unit C (left, 0..11) and Unit D (right, 11..20)
    m.add_lwpolyline([(0, 0), (11, 0), (11, 6), (0, 6)], close=True, dxfattribs={"layer": "UNIT_OUTLINE"})
    m.add_lwpolyline([(11, 0), (20, 0), (20, 6), (11, 6)], close=True, dxfattribs={"layer": "UNIT_OUTLINE"})
    m.add_text("Whg. C", dxfattribs={"layer": "ROOM_TEXT", "insert": (1.0, 5.5)})
    m.add_text("Whg. D", dxfattribs={"layer": "ROOM_TEXT", "insert": (12.0, 5.5)})

    # Rooms:
    # 1.01 Wohnen/Essen (Unit C, 36.00 m²), 1.02 Bad (Unit C, 12.00 m²),
    # 1.03 Wohnen/Essen (Unit D, 24.00 m²), 1.04 Zimmer (Unit D, 18.00 m²),
    # 1.05 Balkon (outdoor, 16.00 m²)
    room_polys = [
        ([(0, 0), (7, 0), (7, 6), (0, 6)], "Wohnen/Essen", "1.01", "36.00 m²", 3.5, 3.0),
        ([(7, 0), (11, 0), (11, 6), (7, 6)], "Bad", "1.02", "12.00 m²", 9.0, 3.0),
        ([(11, 0), (16, 0), (16, 6), (11, 6)], "Wohnen/Essen", "1.03", "24.00 m²", 13.5, 3.0),
        ([(16, 0), (20, 0), (20, 6), (16, 6)], "Zimmer", "1.04", "18.00 m²", 18.0, 3.0),
        ([(2, 6.5), (10, 6.5), (10, 9.5), (2, 9.5)], "Balkon", "1.05", "16.00 m²", 6.0, 8.0),
    ]
    for pts, name, num, area, cx, cy in room_polys:
        m.add_lwpolyline(pts, close=True, dxfattribs={"layer": "ROOM_OUTLINE"})
        m.add_text(name, dxfattribs={"layer": "ROOM_TEXT", "insert": (cx, cy + 0.5)})
        m.add_text(num, dxfattribs={"layer": "ROOM_TEXT", "insert": (cx, cy)})
        m.add_text(area, dxfattribs={"layer": "ROOM_TEXT", "insert": (cx, cy - 0.5)})

    # 2 windows in Unit C (1.01), 1 window in Unit D (1.04) -> 3 windows total
    for wx in (2.0, 4.5, 17.5):
        m.add_lwpolyline(
            [(wx, -0.2), (wx + 1.5, -0.2), (wx + 1.5, 0.0), (wx, 0.0)],
            close=True,
            dxfattribs={"layer": "WINDOW"},
        )
    # 2 doors in Unit C, 1 door in Unit D -> 3 doors total
    for dx, dy in ((6.95, 2.0), (3.0, 5.95), (15.95, 2.0)):
        m.add_lwpolyline(
            [(dx, dy), (dx + 0.1, dy), (dx + 0.1, dy + 0.9), (dx, dy + 0.9)],
            close=True,
            dxfattribs={"layer": "DOOR"},
        )
    # 1 WC in 1.02 (rect + 48-pt ellipse)
    ell = [
        (8.25 + 0.15 * math.cos(t * 2 * math.pi / 48), 1.3 + 0.1 * math.sin(t * 2 * math.pi / 48))
        for t in range(48)
    ]
    m.add_lwpolyline([(8.0, 1.0), (8.5, 1.0), (8.5, 1.6), (8.0, 1.6)], close=True, dxfattribs={"layer": "FIXTURE"})
    m.add_lwpolyline(ell, close=True, dxfattribs={"layer": "FIXTURE"})
    doc.saveas(dxf_path)

    out_dir = base_dir / drawing_id
    out_dir.mkdir(parents=True, exist_ok=True)
    parse_dxf(dxf_path, out_dir, drawing_id, {"title": "Brand New Synthetic Plan"})
    # Strip inline `evidence` from drawing.json so we explicitly prove `SwissEngine(model.json)` works!
    dj = json.loads((out_dir / "drawing.json").read_text(encoding="utf-8"))
    dj.pop("evidence", None)
    (out_dir / "drawing.json").write_text(json.dumps(dj, ensure_ascii=False), encoding="utf-8")
    return out_dir


def test_brand_new_synthetic_dxf_plan_swiss_engine_end_to_end(tmp_path: Path):
    """Prove newly added/uploaded plans (`model.json` + `SwissEngine`, zero `swiss-46639` overlap) work."""
    plan_dir = _create_brand_new_dxf_upload_dir(tmp_path, "up-newplan9001")
    out_json = tmp_path / "newplan_cov.json"
    out_md = tmp_path / "newplan_cov.md"
    out_html = tmp_path / "newplan_cov.html"

    rc = main([
        "--drawing-path", str(plan_dir),
        "--mode", "deterministic",
        "--out-json", str(out_json),
        "--out-md", str(out_md),
        "--out-html", str(out_html),
    ])
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["drawing_id"] == "up-newplan9001"

    summary = payload["summaries"]["deterministic"]
    assert summary["verdict_counts"]["error"] == 0
    assert summary["total_turns"] >= 35
    assert summary["verdict_counts"]["answered_verified"] >= 25

    by_id = {t["probe_id"]: t for t in payload["turns_by_mode"]["deterministic"]}
    # Exact counts & areas derived purely dynamically from the new plan's SwissEngine:
    assert by_id["dyn_door_floor"]["verdict"] == "answered_verified"
    assert by_id["dyn_door_floor"]["actual_value"] == 3
    assert by_id["dyn_window_floor"]["verdict"] == "answered_verified"
    assert by_id["dyn_window_floor"]["actual_value"] == 3
    assert by_id["dyn_apartments_count"]["verdict"] == "answered_verified"
    assert by_id["dyn_apartments_count"]["actual_value"] == 2
    assert by_id["dyn_floor_area"]["verdict"] == "answered_verified"
    assert by_id["dyn_floor_area"]["actual_value"] == 90.0  # 36 + 12 + 24 + 18 (1.05 Balkon excluded)
    assert by_id["dyn_unit_C_area"]["verdict"] == "answered_verified"
    assert by_id["dyn_unit_C_area"]["actual_value"] == 48.0
    assert by_id["dyn_largest_room_on_floor"]["verdict"] == "answered_verified"
    assert by_id["dyn_region_mark_area"]["verdict"] == "answered_verified"
    assert by_id["dyn_conv_doors_t3"]["verdict"] == "answered_verified"

    # Zero-hardcoding dynamic suite STILL catches the platform's silent misroutes on the new plan!
    assert by_id["dyn_living_area_explicit"]["verdict"] == "answered_verified"
    assert by_id["dyn_living_area_explicit"]["actual_value"] == 60.0  # 36 + 24 Wohnen/Essen
    assert by_id["dyn_living_floor_size_phrasing"]["verdict"] == "answered_misrouted"
    assert by_id["dyn_living_in_unit_C_area"]["verdict"] == "answered_misrouted"


def test_new_raster_plan_and_dynamic_only_flag(tmp_path: Path):
    """Prove `RasterEngine` plans (`raster.json`) and `--dynamic-only` on `swiss-46639` work cleanly."""
    from aura.solvers import raster

    rdir = tmp_path / "up-raster42"
    rdir.mkdir(parents=True, exist_ok=True)
    drawing = raster.build_raster_drawing("up-raster42", "New Raster Plan", 1000, 800, "/uploads/up-raster42/image.png")
    raster_data = {
        "counts": {"door": 3, "window": 4},
        "boxes": {
            "door": [
                {"id": f"door-0{i}", "x": 10.0 * i, "y": 20.0, "w": 4.0, "h": 4.0, "subject": "door"}
                for i in range(1, 4)
            ],
            "window": [
                {"id": f"win-0{i}", "x": 15.0 * i, "y": 5.0, "w": 6.0, "h": 2.0, "subject": "window"}
                for i in range(1, 5)
            ],
        },
    }
    (rdir / "drawing.json").write_text(json.dumps(drawing), encoding="utf-8")
    (rdir / "raster.json").write_text(json.dumps(raster_data), encoding="utf-8")

    out_json = tmp_path / "raster_cov.json"
    rc = main(["--drawing-path", str(rdir), "--mode", "deterministic", "--out-json", str(out_json)])
    assert rc == 0
    r_payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert r_payload["summaries"]["deterministic"]["verdict_counts"]["error"] == 0
    r_by_id = {t["probe_id"]: t for t in r_payload["turns_by_mode"]["deterministic"]}
    assert r_by_id["dyn_door_floor"]["verdict"] == "answered_verified"
    assert r_by_id["dyn_door_floor"]["actual_value"] == 3
    assert r_by_id["dyn_window_floor"]["verdict"] == "answered_verified"
    assert r_by_id["dyn_window_floor"]["actual_value"] == 4
    assert r_by_id["dyn_raster_area_gap"]["verdict"] == "abstained_gap"

    # Also prove `--dynamic-only` evaluates swiss-46639 using 0 hardcoded expectations:
    dyn_swiss_json = tmp_path / "dyn_swiss.json"
    rc_dyn = main([
        "--drawing", SWISS_DRAWING_ID,
        "--dynamic-only",
        "--mode", "deterministic",
        "--out-json", str(dyn_swiss_json),
    ])
    assert rc_dyn == 0
    ds = json.loads(dyn_swiss_json.read_text(encoding="utf-8"))
    assert ds["summaries"]["deterministic"]["verdict_counts"]["error"] == 0
    assert ds["summaries"]["deterministic"]["total_turns"] >= 45
