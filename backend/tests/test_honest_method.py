"""No answer over geometry that came from pixels or detections is labelled exact, whatever solver
built it: the drawing's `evidence_method` has the last word."""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from aura import tools
from aura.config import Settings
from aura.main import create_app
from tests.conftest import FRONTEND_DRAWING

QUESTIONS = [
    "Which room is the largest and how big is it?",
    "Which room is the smallest?",
    "How many doors are there?",
    "What is the total floor area?",
    "How many rooms are there?",
    "How many windows are in the largest room?",
]


def _client(tmp_path, methods):
    d = copy.deepcopy(json.loads(FRONTEND_DRAWING.read_text()))
    d["id"] = "scan-1"
    if methods:
        d["evidence_method"] = methods
    ld = tools.LoadedDrawing(id="scan-1", dir=None, drawing=d, engine=tools.BundleEngine(d),
                             gold=[], anchors=[])
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    return TestClient(create_app(s, extra_drawings=[ld]))


def _ask(c, q):
    r = c.post("/ask", json={"drawing_id": "scan-1", "question": q})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("q", QUESTIONS)
def test_scan_geometry_is_never_exact(tmp_path, q):
    scan = {k: "vision" for k in ("room", "door", "window", "wc", "washbasin")}
    with _client(tmp_path, scan) as c:
        r = _ask(c, q)
        if r["answer"]["kind"] == "abstain":
            return
        assert r["method"] == "vision", r["method"]
        for n in r["trace"]:
            assert "CAD" not in n["tag"] and not n["tag"].startswith("exact"), n
            if n["id"] == "answer":
                assert n["kind"] != "exact", n


def test_largest_room_on_a_scan_is_an_estimate_and_cad_stays_exact(tmp_path):
    q = QUESTIONS[0]
    with _client(tmp_path, {"room": "vision", "door": "cad_geometry"}) as c:
        r = _ask(c, q)
        assert r["method"] == "vision" and r["answer"]["number"] > 0
        ans = next(n for n in r["trace"] if n["id"] == "answer")
        assert ans["tag"].startswith("estimate") and "estimate" in ans["body"]
    with _client(tmp_path / "cad", {"room": "cad_geometry", "door": "vision"}) as c:
        assert _ask(c, q)["method"] == "cad_geometry"
    with _client(tmp_path / "plain", None) as c:
        assert _ask(c, q)["method"] == "cad_geometry"
