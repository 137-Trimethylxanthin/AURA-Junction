"""Project membership is server state: a client-supplied scope can never widen a conversation."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_agent_client import MockSidecarState, make_hybrid_app, parse_sse_raw
from tests.test_pdf_upload import make_pdf, upload_pdf, wait_status


def test_conversation_switches_floors_only_inside_a_stored_project(tmp_path):
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        r1 = upload_pdf(c, make_pdf(["Ground floor"]), "a.pdf").json()
        r2 = upload_pdf(c, make_pdf(["First floor"]), "b.pdf").json()
        u1, u2 = r1["drawing_id"], r2["drawing_id"]
        assert wait_status(c, u1)["status"] == "ready" and wait_status(c, u2)["status"] == "ready"

        evs, _ = parse_sse_raw(c.post("/chat", json={"drawing_id": u1, "message": "How many rooms?"}).text)
        conv = evs[0][1]["conversation_id"]
        hop = {"drawing_id": u2, "conversation_id": conv, "message": "And here?"}

        # A made-up project scope that "contains" both drawings is not trusted.
        fake = {"kind": "project", "project_id": "proj-fake", "drawing_ids": [u1, u2]}
        assert c.post("/chat", json=hop | {"scope": fake}).status_code == 404
        assert c.post("/chat", json=hop).status_code == 400

        # Once the server stores the bundle, the hop is allowed ...
        made = c.post("/projects", json={"id": "proj-real", "title": "House", "drawing_ids": [u1, u2]},
                      headers=[("x-delete-token", r1["delete_token"]),  # repeated header form
                               ("x-delete-token", r2["delete_token"])])
        assert made.status_code == 200
        assert c.post("/chat", json=hop).status_code == 200
        real = {"kind": "project", "project_id": "proj-real", "drawing_ids": [u1, u2]}
        owner = {"x-project-token": made.json()["token"]}
        assert c.post("/chat", json=hop | {"scope": real}).status_code == 404  # private bundle
        assert c.post("/chat", json=hop | {"scope": real}, headers=owner).status_code == 200

        # ... but a scope naming a drawing outside that stored bundle is refused.
        u3 = upload_pdf(c, make_pdf(["Attic"]), "c.pdf").json()["drawing_id"]
        assert wait_status(c, u3)["status"] == "ready"
        smuggled = real | {"drawing_ids": [u1, u2, u3]}
        assert c.post("/chat", json={"drawing_id": u1, "message": "Stairs?", "scope": smuggled},
                      headers=owner).status_code == 400
        assert c.post("/ask", json={"drawing_id": u1, "question": "Stairs?", "scope": smuggled},
                      headers=owner).status_code == 400


def _uploads(c, n):
    out = []
    for i in range(n):
        r = upload_pdf(c, make_pdf([f"Floor {i}"]), f"f{i}.pdf").json()
        assert wait_status(c, r["drawing_id"])["status"] == "ready"
        out.append((r["drawing_id"], r["delete_token"]))
    return out


def test_every_upload_floor_needs_its_own_proof(tmp_path):
    """(a) One matching delete token never covers another upload; the owner token covers only the
    floors the project already had."""
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        (u1, t1), (u2, t2), (u3, t3) = _uploads(c, 3)
        body = {"id": "proj-a", "title": "A", "drawing_ids": [u1, u2]}
        assert c.post("/projects", json=body, headers={"x-delete-token": t1}).status_code == 403
        made = c.post("/projects", json=body, headers={"x-delete-token": f"{t1},{t2}"})
        assert made.status_code == 200
        owner = {"x-project-token": made.json()["token"]}
        assert c.post("/projects", json=body | {"title": "A2"}, headers=owner).status_code == 200
        grow = body | {"drawing_ids": [u1, u2, u3]}
        r = c.post("/projects", json=grow, headers=owner)
        assert r.status_code == 403 and u3 in r.json()["error"]
        assert c.post("/projects", json=grow, headers=owner | {"x-delete-token": t3}).status_code == 200


def test_project_scope_on_a_private_bundle_needs_its_token(tmp_path):
    """(b) /chat and /ask cannot read someone else's upload-backed project by id."""
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        (u1, t1), (u2, t2) = _uploads(c, 2)
        made = c.post("/projects", json={"id": "proj-b", "title": "B", "drawing_ids": [u1, u2]},
                      headers={"x-delete-token": f"{t1},{t2}"}).json()
        scope = {"kind": "project", "project_id": "proj-b", "drawing_ids": [u1, u2]}
        ask = {"drawing_id": u1, "question": "How many rooms?", "scope": scope}
        assert c.post("/ask", json=ask).status_code == 404
        assert c.post("/chat", json={"drawing_id": u1, "message": "Rooms?", "scope": scope}).status_code == 404
        assert c.post("/ask", json=ask, headers={"x-project-token": made["token"]}).status_code == 200


def test_owned_project_ignores_upload_delete_tokens(tmp_path):
    """(c) Once a project has an owner token, a floor's delete token alone no longer reaches it."""
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        (u1, t1), (u2, t2) = _uploads(c, 2)
        made = c.post("/projects", json={"id": "proj-c", "title": "C", "drawing_ids": [u1, u2]},
                      headers={"x-delete-token": f"{t1},{t2}"}).json()
        only_upload = {"x-delete-token": t1}
        assert c.delete("/projects/proj-c", headers=only_upload).status_code == 403
        assert "proj-c" not in [p["id"] for p in c.get("/projects", headers=only_upload).json()["projects"]]
        assert c.delete("/projects/proj-c", headers={"x-project-token": made["token"]}).status_code == 204


def test_project_scope_with_an_id_the_server_does_not_hold(tmp_path):
    """A group made in the browser (never saved, saved under a token the request lacks, or lost on
    the server) still answers across its sheets: library drawings are public, uploads need their
    delete tokens."""
    import copy
    import json

    from aura import tools
    from aura.config import Settings
    from aura.main import create_app
    from tests.conftest import FRONTEND_DRAWING

    base = json.loads(FRONTEND_DRAWING.read_text())
    lib = []
    for i, keep in enumerate((6, 4)):
        d = copy.deepcopy(base)
        d |= {"id": f"lib-{i}", "title": f"Plan {i}"}
        d["evidence"]["door"] = d["evidence"]["door"][:keep]
        lib.append(tools.LoadedDrawing(id=d["id"], dir=None, drawing=d, engine=tools.BundleEngine(d),
                                       gold=[], anchors=[]))
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild", AURA_ANSWERER="hybrid")
    with TestClient(create_app(s, extra_drawings=lib, sidecar_transport=MockSidecarState().handler)) as c:
        scope = {"kind": "project", "project_id": "proj-mwgxu9xx", "drawing_ids": ["lib-0", "lib-1"]}
        ask = {"drawing_id": "lib-0", "question": "how many doors are there?", "scope": scope}
        r = c.post("/ask", json=ask)
        assert r.status_code == 200, r.text
        assert r.json()["answer"]["value"] == 10
        # The same group saved by its owner (private row): the token-less question still answers.
        assert c.post("/projects", json={"id": "proj-mwgxu9xx", "title": "Mine",
                                         "drawing_ids": ["lib-0", "lib-1"]}).status_code == 200
        assert c.post("/ask", json=ask).status_code == 200
        # A conversation may move between the sheets of the accepted group.
        evs, _ = parse_sse_raw(c.post("/chat", json={"drawing_id": "lib-0", "message": "How many rooms?",
                                                     "scope": scope}).text)
        conv = evs[0][1]["conversation_id"]
        hop = {"drawing_id": "lib-1", "conversation_id": conv, "message": "And doors?"}
        assert c.post("/chat", json=hop | {"scope": scope}).status_code == 200
        # An unknown sheet is still refused, and one sheet is not a group.
        bad = scope | {"drawing_ids": ["lib-0", "nope"]}
        assert c.post("/ask", json=ask | {"scope": bad}).status_code == 404
        assert c.post("/ask", json=ask | {"scope": scope | {"drawing_ids": ["lib-0"]}}).status_code == 404

        # Uploads join an unsaved group only with every upload's own delete token.
        (u1, t1), (u2, t2) = _uploads(c, 2)
        up = {"kind": "project", "project_id": "proj-local-x", "drawing_ids": [u1, u2]}
        q = {"drawing_id": u1, "question": "How many rooms?", "scope": up}
        assert c.post("/ask", json=q).status_code == 404
        assert c.post("/ask", json=q, headers={"x-delete-token": t1}).status_code == 404
        assert c.post("/ask", json=q, headers={"x-delete-token": f"{t1},{t2}"}).status_code == 200
