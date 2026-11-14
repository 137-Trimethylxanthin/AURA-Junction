"""Uploads: magic bytes, caps, presenter rule, killable parse worker, delete token, cleanup, CSV."""

from __future__ import annotations

import io
import struct
import time
import zlib

import pytest
from fastapi.testclient import TestClient

from aura import uploads
from tests.conftest import REPO, make_app

MB = 1024 * 1024


def loop_forever(kind, src, out, drawing_id, meta, conn):
    """A 'DXF' whose parse never ends (child entry point for the kill test)."""
    while True:
        pass


def plain_dxf() -> bytes:
    """A small DXF with no Swiss layers: two lines and a text."""
    import ezdxf

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 8), (0, 8)], close=True)
    msp.add_line((0, 0), (10, 8))
    msp.add_text("EI30", dxfattribs={"height": 0.3}).set_placement((2, 2))
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


def png_header(w: int, h: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk
            + struct.pack(">I", zlib.crc32(chunk)))


def tiny_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    return buf.getvalue()


def wait_status(c: TestClient, did: str, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/drawings/{did}/status").json()
        if st.get("status") in ("ready", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError(f"{did} still {st}")


def upload(c: TestClient, data: bytes, name: str = "plan.dxf", headers: dict | None = None):
    return c.post("/uploads", files={"file": (name, data, "application/octet-stream")},
                  headers=headers or {})


@pytest.fixture
def client(tmp_path):
    with TestClient(make_app(tmp_path, AURA_PRESENTER_TOKEN="pres")) as c:
        yield c


def test_sniff_by_magic_bytes():
    assert uploads.sniff(b"  0\nSECTION\n  2\nHEADER\n") == "dxf"
    assert uploads.sniff(b"999\nmade by x\n0\r\nSECTION\r\n") == "dxf"
    assert uploads.sniff(b"AutoCAD Binary DXF\r\n\x1a\x00rest") == "dxf"
    assert uploads.sniff(b"AC1032\x00\x00") == "dwg"
    assert uploads.sniff(b"PK\x03\x04....") == "zip"
    assert uploads.sniff(b"MZ\x90\x00") is None
    assert uploads.sniff(tiny_png()) == "png"
    assert uploads.image_size(png_header(20000, 20000), "png") == (20000, 20000)


def test_exe_renamed_dxf_is_415(client):
    r = upload(client, b"MZ\x90\x00" + b"\x00" * 1000, "setup.dxf")
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"


def test_dwg_and_zip_rejected(client):
    assert upload(client, b"AC1032" + b"\x00" * 100, "a.dwg").status_code == 415
    assert upload(client, b"PK\x03\x04" + b"\x00" * 100, "a.zip").status_code == 415


def test_30mb_is_413(client):
    r = upload(client, b"  0\nSECTION\n" + b"9" * (30 * MB))
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_21mb_dxf_is_413_under_body_cap(client):
    r = upload(client, b"  0\nSECTION\n" + b"9" * (21 * MB))
    assert r.status_code == 413


def test_huge_png_header_rejected_before_decode(client):
    # Only a header: decoding would fail, so a 413 proves it was rejected from the header.
    r = upload(client, png_header(20000, 20000) + b"\x00" * 64, "big.png",
               headers={"x-aura-presenter": "pres"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_anonymous_png_needs_presenter(client):
    r = upload(client, tiny_png(), "a.png")
    assert r.status_code == 403 and r.json()["code"] == "presenter_required"
    ok = upload(client, tiny_png(), "a.png", headers={"x-aura-presenter": "pres"})
    assert ok.status_code == 202
    # This app has no model client: detection cannot run, so the upload fails visibly.
    st = wait_status(client, ok.json()["drawing_id"])
    assert st["status"] == "failed" and "not available" in st["error"]


def test_dxf_upload_lifecycle_and_path_safety(client, tmp_path):
    r = upload(client, plain_dxf(), "../../etc/passwd.dxf")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued" and body["delete_token"]
    did = body["drawing_id"]
    assert did.startswith("up-")
    # Stored under its id only; the client filename is never a path.
    up = tmp_path / "data" / "uploads"
    assert max(p.name for p in (up / did).iterdir()) == "source.dxf"
    assert not (tmp_path / "etc").exists()
    st = wait_status(client, did)
    assert st["status"] == "ready", st
    d = client.get(f"/drawings/{did}").json()
    assert d["source"] == "upload" and d["capabilities"] == [] and d["title"] == "passwd.dxf"
    assert d["expires_at"] > d["created_at"]
    assert client.get(f"/drawings/{did}/plan.svg").status_code == 200
    # No Swiss layers: answers abstain, no model call, no image check.
    a = client.post("/ask", json={"drawing_id": did, "question": "How many doors?"}).json()
    assert a["answer"]["reason"] == "not_supported_yet" and a["calls"] == 0
    ar = client.post("/ask", json={"drawing_id": did, "question": "What is the floor area?"}).json()
    assert ar["answer"]["reason"] == "not_supported_yet"
    # Uploads are never in the library listing.
    assert did not in [x["id"] for x in client.get("/drawings").json()["drawings"]]
    # Delete: wrong token 403, right token 204, then gone.
    assert client.delete(f"/drawings/{did}", headers={"x-delete-token": "nope"}).status_code == 403
    assert client.delete(f"/drawings/{did}").status_code == 403
    assert client.delete(f"/drawings/{did}",
                         headers={"x-delete-token": body["delete_token"]}).status_code == 204
    assert client.get(f"/drawings/{did}/status").status_code == 404
    assert not (up / did).exists()
    assert client.delete("/drawings/swiss-46639", headers={"x-delete-token": "x"}).status_code == 403


def test_not_ready_drawing_is_409_for_ask_and_get(tmp_path):
    app = make_app(tmp_path, job_options={"join_timeout_s": 30, "target": loop_forever})
    with TestClient(app) as c:
        did = upload(c, plain_dxf()).json()["drawing_id"]
        assert c.get(f"/drawings/{did}/status").json()["status"] in ("queued", "processing")
        assert c.get(f"/drawings/{did}").status_code == 409
        r = c.post("/ask", json={"drawing_id": did, "question": "How many doors?"})
        assert r.status_code == 409 and r.json()["code"] == "processing"


def test_parse_that_loops_forever_is_killed_and_server_stays_up(tmp_path):
    app = make_app(tmp_path, job_options={"join_timeout_s": 2.0, "target": loop_forever})
    with TestClient(app) as c:
        did = upload(c, plain_dxf()).json()["drawing_id"]
        t0 = time.time()
        while time.time() - t0 < 1.5:  # the server keeps answering while the child spins
            assert c.get("/health").status_code == 200
            time.sleep(0.2)
        st = wait_status(c, did, timeout=15)
        assert st["status"] == "failed" and "longer than" in st["error"]
        assert time.time() - t0 < 10


def test_cleanup_removes_expired_uploads(tmp_path):
    from aura.main import cleanup_once

    app = make_app(tmp_path, AURA_UPLOAD_TTL_S=60)
    with TestClient(app) as c:
        did = upload(c, plain_dxf()).json()["drawing_id"]
        wait_status(c, did)
        assert c.portal.call(cleanup_once, c.app.state.svc) == 0  # not expired yet
        orphan = tmp_path / "data" / "uploads" / "up-orphan0000000000000000000000"
        orphan.mkdir()
        n = c.portal.call(cleanup_once, c.app.state.svc, time.time() + 3601)  # injected clock
        assert n == 1
        assert not orphan.exists()  # M3: a folder without a DB row is swept too
        assert c.get(f"/drawings/{did}/status").status_code == 404
        assert not (tmp_path / "data" / "uploads" / did).exists()


def test_uploads_cut_off_by_a_restart_are_marked_failed(tmp_path):
    """F1: a job lives in one process; after a restart its row must not poll 'processing'."""
    import sqlite3

    with TestClient(make_app(tmp_path)) as c:
        did = upload(c, plain_dxf()).json()["drawing_id"]
        wait_status(c, did)
    db = sqlite3.connect(tmp_path / "data" / "aura.sqlite3")
    db.execute("UPDATE drawings SET status='processing' WHERE id=?", (did,))
    db.commit()
    db.close()
    with TestClient(make_app(tmp_path)) as c:
        st = c.get(f"/drawings/{did}/status").json()
        assert st["status"] == "failed" and "restarted" in st["error"]


def test_csv_escapes_formula_labels(tmp_path):
    import json

    from aura import tools
    from tests.conftest import FRONTEND_DRAWING

    data = json.loads(FRONTEND_DRAWING.read_text()) | {"id": "csv-demo"}
    for r in data["rooms"]:
        if r["name"] == "Cuisine":
            r["name"] = '=HYPERLINK("http://evil","x")'
    d = tools.LoadedDrawing(id="csv-demo", dir=None, drawing=data, engine=tools.BundleEngine(data))
    with TestClient(make_app(tmp_path)) as c:
        c.app.state.svc.registry.add(d)
        a = c.post("/ask", json={"drawing_id": "csv-demo",
                                 "question": "What is the net floor area of apartment A?"}).json()
        c.portal.call(c.app.state.svc.writer.flush)
        r = c.get(f"/answers/{a['answer_id']}/evidence.csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        text = r.text
        assert "'=HYPERLINK(" in text and ',=HYPERLINK(' not in text
        assert text.splitlines()[0].startswith("kind,id,subject")
        assert "139.34" in text
        assert c.get("/answers/nope/evidence.csv").status_code == 404


@pytest.mark.dataset
def test_swiss_dataset_dxf_upload_answers_gold(tmp_path):
    dxf = REPO / "data/junction-dataset/data/swiss_dwellings/site11420_bldg17737_floor46639.dxf"
    with TestClient(make_app(tmp_path)) as c:
        t0 = time.time()
        did = upload(c, dxf.read_bytes(), "floor46639.dxf").json()["drawing_id"]
        st = wait_status(c, did, timeout=25)
        assert st["status"] == "ready" and time.time() - t0 < 20
        a = c.post("/ask", json={"drawing_id": did,
                                 "question": "How many windows are drawn on this floor plan?"}).json()
        assert a["answer"]["value"] == 12 and len(a["evidence"]["boxes"]) == 12
        assert a["check"] == {"method": "decisions_api", "status": "sample", "image": "none"}
        assert a["calls"] == 0


# ---- privacy of uploads (security review) ------------------------------------------------

def test_upload_files_no_store_409_404_and_allow_list(tmp_path):
    app = make_app(tmp_path, job_options={"join_timeout_s": 30, "target": loop_forever})
    with TestClient(app) as c:
        pending = upload(c, plain_dxf()).json()["drawing_id"]
        time.sleep(0.3)
        r = c.get(f"/drawings/{pending}/plan.svg")
        assert r.status_code == 409 and r.json()["code"] == "processing"
    with TestClient(make_app(tmp_path / "b")) as c:
        body = upload(c, plain_dxf()).json()
        did = body["drawing_id"]
        wait_status(c, did)
        f = c.get(f"/drawings/{did}/plan.svg")
        assert f.status_code == 200 and f.headers["cache-control"] == "private, no-store"
        for path in (f"/drawings/{did}", f"/drawings/{did}/status"):
            assert c.get(path).headers["cache-control"] == "private, no-store"
        a = c.post("/ask", json={"drawing_id": did, "question": "How many doors?"})
        assert a.headers["cache-control"] == "private, no-store"
        c.portal.call(c.app.state.svc.writer.flush)
        csv = c.get(f"/answers/{a.json()['answer_id']}/evidence.csv")
        assert csv.status_code == 200 and csv.headers["cache-control"] == "private, no-store"
        # traversal / non-allow-listed names
        for name in ("source.dxf", "drawing.json", "model.json", "..%2Fsource.dxf"):
            assert c.get(f"/drawings/{did}/{name}").status_code == 404
        assert c.get("/drawings/swiss-46639/../../etc/passwd").status_code == 404
        # delete → files, answers and the CSV are gone
        assert c.delete(f"/drawings/{did}", headers={"x-delete-token": body["delete_token"]}
                        ).status_code == 204
        assert c.get(f"/drawings/{did}/plan.svg").status_code == 404
        assert c.get(f"/drawings/{did}").status_code == 404
        assert c.get(f"/answers/{a.json()['answer_id']}/evidence.csv").status_code == 404
        gone = c.post("/ask", json={"drawing_id": did, "question": "How many doors?"})
        assert gone.status_code == 404


@pytest.mark.dataset
def test_library_files_stay_publicly_cacheable(tmp_path):
    from aura.config import Settings
    from aura.main import create_app
    from tests.conftest import BACKEND

    build = BACKEND / "build" / "drawings"
    if not (build / "swiss-46639" / "plan.svg").is_file():
        pytest.skip("dataset: run scripts/preprocess.py first")
    s = Settings(DATA_DIR=tmp_path / "d", AURA_BUILD_DIR=build)
    with TestClient(create_app(s, use_gemini=False)) as c:
        r = c.get("/drawings/swiss-46639/plan.svg")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"


def test_uploads_never_listed_but_askable_by_id_over_mcp(tmp_path):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from aura.mcp import build_mcp

    with TestClient(make_app(tmp_path)) as c:
        body = upload(c, plain_dxf()).json()
        did = body["drawing_id"]
        wait_status(c, did)
        c.get(f"/drawings/{did}")  # loads it into the registry cache
        svc = c.app.state.svc
        assert did not in svc.registry.library_ids()
        assert did not in [x["id"] for x in c.get("/drawings").json()["drawings"]]
        mcp = build_mcp(lambda: svc)

        async def listed_and_asked():
            async with Client(mcp) as m:
                lib = (await m.call_tool("list_drawings", {})).structured_content
                ids = [x["id"] for x in (lib.get("result", lib))]
                assert did not in ids and "swiss-46639" in ids
                a = (await m.call_tool("ask_drawing", {"drawing_id": did,
                                                       "question": "How many doors?"})).data
                return a

        a = c.portal.call(listed_and_asked)
        assert a["answer"]["reason"] == "not_supported_yet"
        assert c.delete(f"/drawings/{did}", headers={"x-delete-token": body["delete_token"]}
                        ).status_code == 204

        async def after_delete():
            async with Client(mcp) as m:
                with pytest.raises(ToolError, match="Unknown drawing"):
                    await m.call_tool("ask_drawing", {"drawing_id": did, "question": "doors?"})

        c.portal.call(after_delete)


# ---- raster (presenter-only, model estimate) ------------------------------------------------

def test_presenter_png_gets_vision_estimate_with_ranked_check(tmp_path):
    """Replays the recorded live run: hydrogen detection on the Swiss plan PNG, then the
    tile checks of its boxes (ranking only)."""
    from aura.gemini.client import FakeTransport
    from tests.conftest import FIXTURES, SWISS_IMAGE

    fake = FakeTransport(FIXTURES / "gemini")
    app = make_app(tmp_path, transport=fake, AURA_PRESENTER_TOKEN="pres")
    with TestClient(app) as c:
        did = upload(c, SWISS_IMAGE.read_bytes(), "plan.png",
                     headers={"x-aura-presenter": "pres"}).json()["drawing_id"]
        st = wait_status(c, did)
        assert st["status"] == "ready", st
        d = c.get(f"/drawings/{did}").json()
        assert d["kind"] == "raster" and d["capabilities"] == ["door", "window"]
        assert "detection" not in d
        # Anyone holding the id may ask; the answer itself costs 0 calls.
        b = c.post("/ask", json={"drawing_id": did,
                                 "question": "How many doors are drawn on this floor plan?"}).json()
        assert b["answer"] == {"kind": "count", "value": 18, "subject": "door"}
        assert b["method"] == "vision" and "gold" not in b and b["calls"] == 0
        ans = next(n for n in b["trace"] if n["id"] == "answer")
        assert ans["body"].startswith("About 18 doors. Model estimate") and ans["kind"] == "model"
        ch = b["check"]
        assert ch["image"] == "upload" and "agreed" not in ch and ch["total"] == 18
        assert ch["ranking"][0] == ch["least_certain_id"] and ch["calls"] == 1
        area = c.post("/ask", json={"drawing_id": did, "question": "What is the floor area?"}).json()
        assert area["answer"]["reason"] == "not_supported_yet"
        detect = [p for p, _ in fake.calls if "generateContent" in p]
        assert len(detect) == 1


def test_raster_detection_failure_marks_upload_failed(tmp_path):
    from aura.gemini.client import FakeTransport

    app = make_app(tmp_path, transport=FakeTransport(tmp_path / "none"), AURA_PRESENTER_TOKEN="p")
    with TestClient(app) as c:
        did = upload(c, tiny_png(), "a.png", headers={"x-aura-presenter": "p"}).json()["drawing_id"]
        st = wait_status(c, did)
        assert st["status"] == "failed" and st["error"]


def _open_paths() -> list[str]:
    import os

    out = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            out.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            pass
    return out


@pytest.mark.skipif(not __import__("os").path.isdir("/proc/self/fd"), reason="needs /proc")
def test_deleted_upload_leaves_no_file_and_no_descriptor(tmp_path):
    """H2: the temp-file descriptor is closed, so a deleted upload is really gone."""
    with TestClient(make_app(tmp_path)) as c:
        r = upload(c, plain_dxf()).json()
        did = r["drawing_id"]
        wait_status(c, did)
        assert c.get(f"/drawings/{did}").json()["credit"]["license"] == "user upload"  # M3
        c.portal.call(c.app.state.svc.writer.execute,
                      "INSERT INTO check_cache(key, t, drawing_id, subject, result) VALUES "
                      "(?,?,?,?,?)", ("k1", 0.0, did, "door", "{}"))
        assert c.delete(f"/drawings/{did}",
                        headers={"x-delete-token": r["delete_token"]}).status_code == 204
        c.portal.call(c.app.state.svc.writer.flush)
        assert c.app.state.svc.store.check("k1") is None  # M3: its cached checks go too
        up = tmp_path / "data" / "uploads"
        assert not (up / did).exists() and not list(up.glob(".tmp-*"))
        assert not [p for p in _open_paths() if did in p or ".tmp-" in p]


def test_uploads_are_limited_per_ip_queue_and_storage(tmp_path):
    """H1: limits are checked before the body is read."""
    with TestClient(make_app(tmp_path, AURA_UPLOADS_PER_IP_HOUR=2, AURA_PRESENTER_TOKEN="pres")) as c:
        assert upload(c, plain_dxf()).status_code == 202
        assert upload(c, plain_dxf()).status_code == 202
        r = upload(c, plain_dxf())
        assert r.status_code == 429 and r.json()["code"] == "rate_limited"
        # the presenter is not limited per IP
        assert upload(c, plain_dxf(), headers={"x-aura-presenter": "pres"}).status_code == 202
    with TestClient(make_app(tmp_path / "q", AURA_UPLOAD_QUOTA_BYTES=1)) as c:
        (tmp_path / "q" / "data" / "uploads").mkdir(parents=True, exist_ok=True)
        (tmp_path / "q" / "data" / "uploads" / "x").write_bytes(b"12")
        assert upload(c, plain_dxf()).status_code == 429
    app = make_app(tmp_path / "j", AURA_MAX_QUEUED_UPLOADS=1,
                   job_options={"join_timeout_s": 30, "target": loop_forever})
    with TestClient(app) as c:
        assert upload(c, plain_dxf()).status_code == 202
        assert upload(c, plain_dxf()).status_code == 429


def test_child_drops_secrets_and_refuses_oversized_models(monkeypatch):
    """M4 + H1: the parse child keeps no secret env vars; a DXF with thousands of doors is
    refused in the child instead of reaching the web process."""
    import os
    from unittest import mock

    from aura import parse_worker

    monkeypatch.setattr(parse_worker.resource, "setrlimit", lambda *a: None)  # not in-process
    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}):  # restored afterwards
        parse_worker._limits()
        assert "GEMINI_API_KEY" not in os.environ and "PATH" in os.environ


def test_dxf_with_too_many_objects_is_refused_in_the_child(tmp_path, monkeypatch):
    from aura import parse_worker

    src = tmp_path / "a.dxf"
    src.write_bytes(plain_dxf())
    monkeypatch.setattr(parse_worker, "MAX_TEXTS", 0)  # plain_dxf has one text
    with pytest.raises(parse_worker.TooLarge):
        parse_worker.parse_dxf(src, tmp_path / "out", "up-x", {})


def test_cors_is_pinned_and_foreign_origins_cannot_upload(tmp_path):
    """M2: only the pinned Pages project (and its previews) pass; a look-alike does not."""
    with TestClient(make_app(tmp_path)) as c:
        def pre(origin):
            return c.options("/ask", headers={"origin": origin,
                                             "access-control-request-method": "POST"}).headers
        assert pre("https://aura-j26.pages.dev").get("access-control-allow-origin")
        assert pre("https://1a2b3c4d.aura-j26.pages.dev").get("access-control-allow-origin")
        for bad in ("https://aura-attacker.pages.dev", "https://evil.aura.pages.dev"):
            assert not pre(bad).get("access-control-allow-origin"), bad
        r = upload(c, plain_dxf(), headers={"origin": "https://aura-attacker.pages.dev"})
        assert r.status_code == 403
        assert upload(c, plain_dxf(), headers={"origin": "https://aura-j26.pages.dev"}).status_code == 202
