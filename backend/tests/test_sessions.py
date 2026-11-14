"""Session history mirror (/sessions), mounted on the real app the way main.py will include it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aura.sessions import router
from tests.conftest import make_app


@pytest.fixture
def c(tmp_path):
    app = make_app(tmp_path)
    if not any(getattr(r, "path", None) == "/sessions" for r in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        yield client


A = {"X-Aura-Client": "a" * 32}
B = {"X-Aura-Client": "b" * 32}


def sess(i: str = "s1", **kw):
    body = {
        "id": i, "drawing_id": "swiss-46639", "title": "North windows · 4 found",
        "project_id": None, "pinned": False, "turns": [{"id": f"{i}-t0", "role": "user", "text": "q"}],
        "activeTurnId": None, "rejectedBoxIds": ["b1"], "updated_at": 1000.0,
    }
    return body | kw


def test_upsert_list_and_scope_by_client(c):
    assert c.post("/sessions", json=sess(), headers=A).status_code == 200
    assert c.post("/sessions", json=sess(title="Renamed", updated_at=2000.0), headers=A).status_code == 200
    assert c.post("/sessions", json=sess("s2"), headers=B).status_code == 200
    # Same id from another browser is a separate row, never an overwrite.
    assert c.post("/sessions", json=sess(title="Theirs"), headers=B).status_code == 200
    got = c.get("/sessions", headers=A).json()["sessions"]
    assert [s["id"] for s in got] == ["s1"]
    assert got[0]["title"] == "Renamed"
    assert got[0]["rejectedBoxIds"] == ["b1"]  # extra fields round-trip
    assert c.get("/sessions", params={"drawing_id": "other"}, headers=A).json() == {"sessions": []}


def test_requires_a_valid_client_header(c):
    assert c.get("/sessions").status_code == 401
    assert c.get("/sessions", params={"client_id": "a" * 32}).status_code == 401  # query is ignored
    assert c.get("/sessions", headers={"X-Aura-Client": "short"}).status_code == 401
    assert c.post("/sessions", json=sess()).status_code == 401
    assert c.patch("/sessions/s1", json={"pinned": True}).status_code == 401
    assert c.delete("/sessions/s1").status_code == 401


def test_patch_and_delete(c):
    c.post("/sessions", json=sess(), headers=A)
    r = c.patch("/sessions/s1", json={"title": "Mine", "pinned": True}, headers=A)
    assert r.status_code == 200
    assert r.json()["pinned"] is True and r.json()["customTitle"] is True
    assert c.patch("/sessions/s1", json={"pinned": True}, headers=B).status_code == 404
    assert c.delete("/sessions/s1", headers=B).status_code == 200  # not theirs: no-op
    assert len(c.get("/sessions", headers=A).json()["sessions"]) == 1
    assert c.delete("/sessions/s1", headers=A).status_code == 200
    assert c.get("/sessions", headers=A).json() == {"sessions": []}


def test_validation_and_cap(c, monkeypatch):
    assert c.post("/sessions", json=sess("bad id/../x"), headers=A).status_code == 422
    monkeypatch.setattr("aura.sessions.MAX_PER_CLIENT", 3)
    c.post("/sessions", json=sess("keep", pinned=True, updated_at=0.0), headers=A)
    for n in range(5):
        c.post("/sessions", json=sess(f"s{n}", updated_at=float(n + 1)), headers=A)
    ids = {s["id"] for s in c.get("/sessions", headers=A).json()["sessions"]}
    assert ids == {"keep", "s2", "s3", "s4"}
