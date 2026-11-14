"""Project membership is server state: a client-supplied scope can never widen a conversation."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_agent_client import MockSidecarState, make_hybrid_app, parse_sse_raw
from tests.test_pdf_upload import make_pdf, upload_pdf, wait_status


def test_conversation_switches_floors_only_inside_a_stored_project(tmp_path):
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        u1 = upload_pdf(c, make_pdf(["Ground floor"]), "a.pdf").json()["drawing_id"]
        u2 = upload_pdf(c, make_pdf(["First floor"]), "b.pdf").json()["drawing_id"]
        assert wait_status(c, u1)["status"] == "ready" and wait_status(c, u2)["status"] == "ready"

        evs, _ = parse_sse_raw(c.post("/chat", json={"drawing_id": u1, "message": "How many rooms?"}).text)
        conv = evs[0][1]["conversation_id"]
        hop = {"drawing_id": u2, "conversation_id": conv, "message": "And here?"}

        # A made-up project scope that "contains" both drawings is not trusted.
        fake = {"kind": "project", "project_id": "proj-fake", "drawing_ids": [u1, u2]}
        assert c.post("/chat", json=hop | {"scope": fake}).status_code == 404
        assert c.post("/chat", json=hop).status_code == 400

        # Once the server stores the bundle, the hop is allowed ...
        made = c.post("/projects", json={"id": "proj-real", "title": "House", "drawing_ids": [u1, u2]})
        assert made.status_code == 200
        assert c.post("/chat", json=hop).status_code == 200
        real = {"kind": "project", "project_id": "proj-real", "drawing_ids": [u1, u2]}
        assert c.post("/chat", json=hop | {"scope": real}).status_code == 200

        # ... but a scope naming a drawing outside that stored bundle is refused.
        u3 = upload_pdf(c, make_pdf(["Attic"]), "c.pdf").json()["drawing_id"]
        assert wait_status(c, u3)["status"] == "ready"
        smuggled = real | {"drawing_ids": [u1, u2, u3]}
        assert c.post("/chat", json={"drawing_id": u1, "message": "Stairs?", "scope": smuggled}).status_code == 400
        assert c.post("/ask", json={"drawing_id": u1, "question": "Stairs?", "scope": smuggled}).status_code == 400
