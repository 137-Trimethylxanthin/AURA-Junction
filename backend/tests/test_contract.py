"""Backend vs the frontend conformance table (frontend/src/api/conformance.fixtures.json)."""

from __future__ import annotations

import json

import pytest

from aura.contract import AskResponse
from tests.conftest import CONFORMANCE, no_nulls

CASES = json.loads(CONFORMANCE.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_conformance_case(client, case):
    req = {"drawing_id": "swiss-46639", "question": case["question"]}
    for k in ("scope", "overrides"):
        if k in case:
            req[k] = case[k]
    r = client.post("/ask", json=req)
    assert r.status_code == 200, r.text
    body = r.json()
    no_nulls(body)
    AskResponse.model_validate(body)
    assert body["contract_version"].split(".")[0] == "1"
    assert body["source"] == "api"
    exp, ans = case["expect"], body["answer"]
    assert ans["kind"] == exp["kind"], (case["name"], ans)
    if exp["kind"] == "abstain":
        assert ans["reason"] == exp["reason"]
        assert body["method"] == "none"
    else:
        assert ans["value"] == pytest.approx(exp["value"], abs=1e-9)
        if "subject" in exp:
            assert ans["subject"] == exp["subject"]
        assert body["calls"] == 0 and body["cost_usd"] == 0
    if "lang" in case:
        assert body["detected_lang"] == case["lang"]
    if "gold" in case:
        if case["gold"] is None:
            assert "gold" not in body
        else:
            g = body["gold"]
            assert g["value"] == pytest.approx(case["gold"]["value"])
            assert g["match"] is case["gold"]["match"]
            if "dataset_qid" in case["gold"]:
                assert g["dataset_qid"] == case["gold"]["dataset_qid"]
    # Obligation 8: trace evidence ids refer to evidence in the same response.
    ids = {b["id"] for b in body["evidence"]["boxes"]} | {
        f"room-{x['room_id']}" for x in body["evidence"]["rooms"]}
    for node in body["trace"]:
        for eid in node.get("evidence_ids", []):
            assert eid in ids, (node["id"], eid)
    # The UI recomputes the number from the evidence: it must add up.
    if ans["kind"] == "count" and ans["subject"] not in ("room", "apartment"):
        assert len(body["evidence"]["boxes"]) == ans["value"]
    if ans["kind"] == "area":
        inc = sum(x["area_m2"] for x in body["evidence"]["rooms"] if x["included"])
        assert round(inc, 2) == ans["value"]


def test_unknown_drawing_is_404_error_body(client):
    r = client.post("/ask", json={"drawing_id": "swiss-1", "question": "How many windows?"})
    assert r.status_code == 404
    assert r.json() == {"contract_version": "1.2", "error": "Unknown drawing 'swiss-1'",
                        "code": "unknown_drawing"}


def test_bad_body_is_422_detail(client):
    r = client.post("/ask", json={"question": "no drawing"})
    assert r.status_code == 422 and "detail" in r.json()


def test_health_and_drawing_shapes(client):
    h = client.get("/health").json()
    assert h["ok"] is True and h["contract_version"] == "1.2"
    d = client.get("/drawings/swiss-46639").json()
    no_nulls(d)
    assert d["id"] == "swiss-46639" and "evidence" not in d and "gold" not in d
    assert d["frame"]["px"] == [1221, 1220] and d["capabilities"]
    lib = client.get("/drawings").json()
    assert lib["contract_version"] == "1.2"
    assert lib["drawings"][0]["id"] == "swiss-46639"
    assert lib["drawings"][0]["status"] == "ready"
    st = client.get("/drawings/swiss-46639/status").json()
    assert st == {"contract_version": "1.2", "id": "swiss-46639", "status": "ready"}


def test_contract_v1_2_additive_models():
    """1.2: AnswerNote, agent_estimate, deep_agent, source_ext, and Health.answerer validate cleanly."""
    from aura.contract import CONTRACT_VERSION, DrawingSummary, Health, ToolResult, TraceTool

    assert CONTRACT_VERSION == "1.2"
    resp = AskResponse.model_validate({
        "contract_version": "1.2",
        "source": "api",
        "detected_lang": "en",
        "canonical_en": "How wide is the corridor?",
        "scope": {"kind": "floor"},
        "route": "agent",
        "answer": {"kind": "note", "value": "Approximately 1.45 m clear width", "number": 1.45, "unit": "m"},
        "method": "agent_estimate",
        "estimate": {"confidence": 0.78},
        "evidence": {"boxes": [], "rooms": []},
        "trace": [{
            "id": "t1",
            "title": "Deep agent",
            "tag": "Agent",
            "body": "Measured corridor lines",
            "kind": "model",
            "tool": {"name": "deep_agent", "args": {"step": "shell", "seq": 1}, "result_id": "r1", "ms": 420},
        }],
        "calls": 4,
        "cost_usd": 0.04,
        "latency_ms": 3500,
    })
    assert resp.answer.kind == "note" and resp.method == "agent_estimate"
    TraceTool.model_validate({"name": "deep_agent", "args": {"step": "shell"}, "result_id": "r1", "ms": 10})
    ToolResult.model_validate({
        "result_id": "r1",
        "tool": "deep_agent",
        "kind": "note",
        "value": 1.45,
        "text": "Approximately 1.45 m clear width",
        "numbers": [1.45],
        "method": "agent_estimate",
    })
    ds = DrawingSummary.model_validate({
        "id": "up-pdf1",
        "title": "Sheet 1",
        "kind": "raster",
        "status": "ready",
        "thumbnail": "/drawings/up-pdf1/plan.png",
        "credit": {"text": "Uploaded by user", "license": "user-supplied"},
        "source_ext": ".pdf",
        "project_id": "proj-house1",
        "floor_label": "EG",
        "floor_order": 0,
    })
    assert ds.source_ext == ".pdf" and ds.project_id == "proj-house1" and ds.floor_order == 0
    h = Health.model_validate({"ok": True, "answerer": "hybrid", "agent_ok": True})
    assert h.contract_version == "1.2" and h.answerer == "hybrid" and h.agent_ok is True


def test_contract_v1_2_groups_links_and_projects():
    """1.2 Wave 0.5: categorical EvidenceGroup, directional EvidenceLink, ScopeProject & ProjectBundle."""
    from pydantic import ValidationError

    from aura.contract import (
        AskRequest,
        Drawing,
        Evidence,
        EvidenceGroup,
        EvidenceLink,
        ProjectBundle,
        ProjectList,
        ToolResult,
    )

    resp = AskResponse.model_validate({
        "contract_version": "1.2",
        "source": "api",
        "detected_lang": "en",
        "canonical_en": "Show all stairs and where they lead to across all floors",
        "scope": {"kind": "project", "project_id": "proj-villa", "drawing_ids": ["d-eg", "d-1og"]},
        "route": "agent",
        "answer": {"kind": "count", "value": 2, "subject": "room"},
        "method": "agent_estimate",
        "evidence": {
            "boxes": [{
                "id": "stair-01",
                "x": 40.0,
                "y": 50.0,
                "w": 8.0,
                "h": 6.0,
                "subject": "label",
                "note": "Main Stair S1",
                "group_id": "g-s1",
                "drawing_id": "d-eg",
            }],
            "rooms": [{
                "room_id": "3.01",
                "included": True,
                "area_m2": 18.4,
                "group_id": "g-s1",
                "drawing_id": "d-eg",
            }],
            "groups": [
                {"id": "g-s1", "label": "Main Stair S1 → 1. OG", "color": "c1", "target_drawing_id": "d-1og"},
                {"id": "g-s2", "label": "Service Stair S2 → KG", "color": "c6", "target_drawing_id": "d-kg"},
            ],
            "links": [{
                "id": "lnk-1",
                "from_pos": [44.0, 53.0],
                "to_pos": [44.0, 38.0],
                "waypoints": [[44.0, 46.0]],
                "group_id": "g-s1",
                "label": "→ 1. OG",
                "drawing_id": "d-eg",
                "target_drawing_id": "d-1og",
            }],
        },
        "trace": [],
        "calls": 2,
        "cost_usd": 0.02,
        "latency_ms": 1200,
    })
    assert resp.scope.kind == "project" and resp.scope.drawing_ids == ["d-eg", "d-1og"]
    assert len(resp.evidence.groups or []) == 2
    assert resp.evidence.links and resp.evidence.links[0].waypoints == [(44.0, 46.0)]
    dumped = resp.model_dump(exclude_none=True)
    no_nulls(dumped)

    # Empty Evidence still omits optional groups/links when dumped with exclude_none=True
    assert Evidence().model_dump(exclude_none=True) == {"boxes": [], "rooms": []}

    # Reject out-of-palette group color and empty ScopeProject drawing_ids
    with pytest.raises(ValidationError):
        EvidenceGroup.model_validate({"id": "g-bad", "label": "Bad", "color": "c7"})
    with pytest.raises(ValidationError):
        EvidenceLink.model_validate({"id": "l-bad", "from_pos": [1.0], "to_pos": [2.0, 3.0]})
    with pytest.raises(ValidationError):
        AskRequest.model_validate({
            "drawing_id": "d-eg",
            "question": "Stairs?",
            "scope": {"kind": "project", "project_id": "p", "drawing_ids": []},
        })

    # ToolResult accepts groups/links or None cleanly
    tr = ToolResult.model_validate({
        "result_id": "r-apt",
        "tool": "count_objects",
        "kind": "count",
        "subject": "apartment",
        "value": 2,
        "groups": [{"id": "unit-A", "label": "Apartment A", "color": "c1"}],
        "links": None,
    })
    assert len(tr.groups) == 1 and tr.links == []

    bundle = ProjectBundle.model_validate({
        "id": "proj-villa",
        "title": "Villa Nord",
        "floors": [
            {"drawing_id": "d-kg", "label": "KG", "order": -1},
            {"drawing_id": "d-eg", "label": "EG", "order": 0},
            {"drawing_id": "d-1og", "label": "1. OG", "order": 1},
        ],
        "auto_matched": True,
    })
    plist = ProjectList(projects=[bundle])
    assert plist.contract_version == "1.2" and len(plist.projects[0].floors) == 3

    d_full = Drawing.model_validate({
        "contract_version": "1.2",
        "id": "d-eg",
        "title": "Villa Nord · EG",
        "frame": {"dxf_min": [0, 0], "dxf_max": [10, 10], "margin_m": 0.6, "m_per_unit": 1.0, "y_flip": True, "px": [800, 600]},
        "m_per_pct_x": 0.1,
        "m_per_pct_y": 0.1,
        "images": {"rebuilt": "/drawings/d-eg/plan.svg"},
        "rooms": [],
        "units": [],
        "credit": {"text": "User", "license": "user"},
        "project_id": "proj-villa",
        "floor_label": "EG",
        "floor_order": 0,
    })
    assert d_full.project_id == "proj-villa" and d_full.floor_label == "EG" and d_full.floor_order == 0


def test_modal_image_is_pinned_to_the_lockfile():
    """L5: the deployed image installs exactly uv.lock's versions."""
    import subprocess

    from tests.conftest import BACKEND

    want = subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-hashes",
                           "--no-emit-project", "--no-header"], cwd=BACKEND, capture_output=True,
                          text=True, check=True).stdout
    assert (BACKEND / "requirements-modal.txt").read_text() == want
    assert "requirements-modal.txt" in (BACKEND / "modal_app.py").read_text()


def test_readme_env_table_is_generated_from_settings():
    """Every Settings alias is documented, and README.md holds exactly the generated table."""
    import importlib.util

    from tests.conftest import BACKEND

    spec = importlib.util.spec_from_file_location("env_table", BACKEND / "scripts" / "env_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.README.read_text(encoding="utf-8")
    assert mod.readme_with_table(text) == text, "run: uv run python -I scripts/env_table.py --write"
