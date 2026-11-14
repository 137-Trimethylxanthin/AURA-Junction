"""The drawing store bridge (aura/drawing_store.py): normalisation and honesty rules on a captured
store drawing (no database needed), stage mapping, quick mode and the ref-counted delete."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from aura import tools
from aura.drawing_store import DrawingStore, normalise
from tests.conftest import make_app
from tests.test_pdf_upload import make_pdf, upload_pdf, wait_status

FIX = Path(__file__).parent / "fixtures" / "store_site1000_floor9385.json"


def _materialised() -> dict:
    b = json.loads(FIX.read_text())
    ours = {"id": "up-test", "title": "plan.pdf", "kind": "raster", "source": "upload",
            "images": {"original": "/drawings/up-test/original.png"},
            "frame": {"px": [2000, 1415]}, "rooms": [], "units": [], "capabilities": []}
    return normalise(ours, b["doc"], b["evidence"], b["model"], {int(k): v for k, v in b["sources"].items()})


def test_normalise_keeps_ours_and_labels_every_subject_honestly():
    d = _materialised()
    assert d["id"] == "up-test" and d["images"] == {"original": "/drawings/up-test/original.png"}
    assert d["frame"]["px"] == [2000, 1415]  # our own render of the page is the image served
    # Doors/windows/fixtures came from a model detection; rooms from the PDF's vectors and text.
    assert d["evidence_method"]["door"] == "vision" and d["evidence_method"]["window"] == "vision"
    assert d["evidence_method"]["room"] == "pdf_vector"
    assert "cad_geometry" not in d["evidence_method"].values()
    boxes = [b for v in d["evidence"].values() for b in v]
    assert boxes and all("note" not in b for b in boxes)  # notes mean CAD duplicates to BundleEngine
    assert all("store_ref" in b for b in boxes) and all("store_ref" in r for r in d["rooms"])
    assert set(d["evidence"]) <= {"door", "window", "washbasin", "wc", "bathtub", "shower"}
    assert len(d["rooms"]) == 31 and len(d["evidence"]["door"]) == 29


def test_bundle_engine_reports_store_methods_never_cad():
    d = _materialised()
    eng = tools.BundleEngine(d)
    doors = eng.answer("door", {"kind": "floor"}, None)
    assert doors["method"] == "vision" and doors["estimate"] is True
    assert "DXF" not in doors["summary"] and "estimate" in doors["summary"]
    area = eng.answer("area", {"kind": "floor"}, None)
    assert area["method"] == "pdf_vector" and not area.get("estimate")
    ld = tools.LoadedDrawing(id="up-test", dir=None, drawing=d, engine=eng)
    tr = tools.count_objects(ld, "door", {"kind": "floor"})
    assert tr.method == "vision" and tr.meta.get("estimate") is True


def test_page_stage_mapping():
    ds = DrawingStore.__new__(DrawingStore)
    ds.ok = True
    ready = {"status": "ready"}
    assert ds.page_stage({"status": "queued"}, {})[0] == "queued"
    assert ds.page_stage({"status": "processing"}, {})[0] == "parsing"
    assert ds.page_stage({"status": "failed"}, {})[0] == "failed"
    assert ds.page_stage(ready, {"kind": "dxf"})[0] == "indexed_ours"
    assert ds.page_stage(ready, {"store": {"state": "registered", "db_stage": "interpreting"}})[0] == "interpreting"
    assert ds.page_stage(ready, {"store": {"state": "materialised", "owner": "store"}})[0] == "indexed_store"
    assert ds.page_stage(ready, {"store": {"state": "partial"}})[0] == "partial"
    assert ds.page_stage(ready, {"store": {"state": "failed"}})[0] == "store_failed"
    ds.ok = False
    assert ds.page_stage(ready, {"kind": "pdf"})[0] == "ready_unindexed"


def test_quick_mode_upload_ends_ready_unindexed(tmp_path):
    """No AURA_STORE_DSN: the store never starts, the upload is usable and says so."""
    with TestClient(make_app(tmp_path)) as c:
        assert c.get("/health").json()["store"] == "off"
        assert c.app.state.drawing_store.available() is False
        did = upload_pdf(c, make_pdf(["EG"]), "a.pdf").json()["drawing_id"]
        assert wait_status(c, did)["status"] == "ready"
        st = c.get(f"/drawings/{did}/status").json()
        assert st["stage"] == "ready_unindexed" and st["mode"] == "quick"
        assert st["pages"] == [{"drawing_id": did, "page": 1, "stage": "ready_unindexed"}]


def _uploads_with_store_meta(c, n, fid="f" * 16):
    ids = []
    for _ in range(n):
        r = upload_pdf(c, make_pdf(["EG"]), "a.pdf").json()
        assert wait_status(c, r["drawing_id"])["status"] == "ready"
        ids.append(r["drawing_id"])
    s = c.app.state.svc
    for did in ids:
        meta = json.dumps({"kind": "pdf", "store": {"file_id": fid, "drawing_key": "k", "state": "materialised"}})
        c.portal.call(s.writer.execute, "UPDATE drawings SET meta=? WHERE id=?", (meta, did))
    return ids


def _armed(c, monkeypatch, permanent=()):
    """The app's DrawingStore, 'on', with its Postgres side recorded instead of executed."""
    ds = c.app.state.drawing_store
    ds.ok = True
    deleted: list[list[str]] = []

    async def delete(file_ids):
        deleted.append(sorted(file_ids))
    monkeypatch.setattr(ds, "delete", delete)
    monkeypatch.setattr(ds, "permanent_file_ids", lambda: set(permanent))
    return ds, deleted


def test_store_project_is_deleted_only_when_no_upload_still_uses_it(tmp_path, monkeypatch):
    """Two uploads with identical bytes share one store project: deleting one keeps it."""
    from aura.main import _remove_upload

    with TestClient(make_app(tmp_path)) as c:
        ids = _uploads_with_store_meta(c, 2)
        ds, deleted = _armed(c, monkeypatch)
        c.portal.call(_remove_upload, c.app.state.svc, ids[0], ds)
        assert deleted == [[]]  # the other upload still uses those bytes
        c.portal.call(_remove_upload, c.app.state.svc, ids[1], ds)
        assert deleted[-1] == ["f" * 16]


def test_dataset_bytes_are_never_deleted_with_an_upload(tmp_path, monkeypatch):
    """An upload identical to a bundled twin reuses the dataset row; deleting it keeps the row."""
    from aura.main import _remove_upload

    with TestClient(make_app(tmp_path)) as c:
        (did,) = _uploads_with_store_meta(c, 1, fid="0590bbb6eb080328")
        ds, deleted = _armed(c, monkeypatch, permanent={"0590bbb6eb080328"})
        c.portal.call(_remove_upload, c.app.state.svc, did, ds)
        assert deleted == [[]]


def test_delete_while_the_store_is_down_is_finished_by_the_sweep(tmp_path, monkeypatch):
    from aura.main import _remove_upload

    with TestClient(make_app(tmp_path)) as c:
        (did,) = _uploads_with_store_meta(c, 1)
        ds = c.app.state.drawing_store
        ds.ok = False  # Postgres down: the store side is skipped, a tombstone stays
        c.portal.call(_remove_upload, c.app.state.svc, did, ds)
        st = c.app.state.svc.store
        assert [r["file_id"] for r in st._q("SELECT file_id FROM store_tombstones")] == ["f" * 16]
        ds, deleted = _armed(c, monkeypatch)  # Postgres is back
        monkeypatch.setattr(ds, "_upload_projects", list)
        assert c.portal.call(ds.sweep) == 1 and deleted == [["f" * 16]]
        assert st._q("SELECT count(*) n FROM store_tombstones")[0]["n"] == 0


def test_orphan_upload_projects_are_swept_but_live_and_dataset_ones_stay(tmp_path, monkeypatch):
    """A store project created for an upload deleted during ingestion has no live row: swept."""
    with TestClient(make_app(tmp_path)) as c:
        _uploads_with_store_meta(c, 1, fid="a" * 16)
        ds, deleted = _armed(c, monkeypatch, permanent={"b" * 16})
        monkeypatch.setattr(ds, "_upload_projects", lambda: ["a" * 16, "b" * 16, "c" * 16])
        c.portal.call(ds.sweep)
        assert deleted == [["c" * 16]]  # a: live upload, b: dataset bytes, c: orphan


def test_registration_of_a_deleted_upload_is_undone(tmp_path, monkeypatch):
    """Deleted before or while the store takes the file: nothing of it stays."""
    with TestClient(make_app(tmp_path)) as c:
        ds, deleted = _armed(c, monkeypatch)
        src = tmp_path / "x.pdf"
        src.write_bytes(make_pdf(["EG"]))
        assert c.portal.call(ds.register_upload, "up-gone", src, "x.pdf") is None
        assert deleted == []  # never reached the store
def test_jobs_hand_every_parsed_page_to_the_store(tmp_path):
    """The Jobs hook signature (drawing_id, page_ids) matches DrawingStore.on_parsed."""
    seen = []
    with TestClient(make_app(tmp_path)) as c:
        ds = c.app.state.drawing_store
        ds.ok = True
        ds._spawn = lambda coro: (seen.append(coro), coro.close())
        did = upload_pdf(c, make_pdf(["EG", "1. OG"]), "two.pdf").json()["drawing_id"]
        assert wait_status(c, did)["status"] == "ready"
        assert len(seen) == 2  # both pages of the PDF


def test_store_workers_get_the_store_environment_even_after_a_scrubbed_parse(tmp_path, monkeypatch):
    """parse_worker makes every later spawn start with a scrubbed environment; the store's pool
    must still see JSTORE_DSN (else jstore silently uses its default credentials)."""
    from aura import drawing_store as DS
    from aura import parse_worker

    monkeypatch.setenv("JSTORE_DSN", "postgresql://u:p@127.0.0.1:1/x")
    import multiprocessing as mp
    if __import__("os").path.exists("/usr/bin/env"):
        mp.get_context("spawn").set_executable(parse_worker._launcher())
    try:
        with DS.make_pool(1) as pool:
            got = pool.submit(_env_of, "JSTORE_DSN").result(timeout=60)
    finally:
        import sys
        mp.get_context("spawn").set_executable(sys.executable)
    assert got == "postgresql://u:p@127.0.0.1:1/x"


def _env_of(name: str) -> str | None:
    import os
    return os.environ.get(name)


def _store_app(tmp_path, gemini=None):
    """An app serving the materialised store fixture as drawing `store-fixture` (no sidecar)."""
    from aura.config import Settings
    from aura.main import create_app
    from tests.test_agent_client import MockGeminiDecisionsTransport

    d = _materialised() | {"id": "store-fixture"}
    ld = tools.LoadedDrawing(id="store-fixture", dir=None, drawing=d, engine=tools.BundleEngine(d))
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    gem = gemini or MockGeminiDecisionsTransport()
    return create_app(s, transport=gem, extra_drawings=[ld]), gem


def test_store_indexed_subjects_answer_from_the_index_not_image_scoring(tmp_path):
    """Labelled rooms and WCs on a store-indexed PDF come from its index (truth 4 WCs here)."""
    app, _gem = _store_app(tmp_path)
    with TestClient(app) as c:
        wc = c.post("/ask", json={"drawing_id": "store-fixture", "question": "how many toilets"}).json()
        assert wc["answer"] == {"kind": "count", "value": 4, "subject": "wc"}
        assert wc["method"] == "vision" and wc["calls"] == 0
        k = c.post("/ask", json={"drawing_id": "store-fixture", "question": "how many kitchens"}).json()
        assert k["answer"]["kind"] == "count" and k["method"] == "pdf_vector"


def test_full_mode_skips_our_detection_in_the_parse_slot(tmp_path):
    """The store indexes the page (more accurate, M3); our detection runs only in quick mode."""
    calls = []
    with TestClient(make_app(tmp_path)) as c:
        jobs = c.app.state.jobs

        async def detect(did, out, ctx):
            calls.append(did)
        jobs.after_raster = detect
        jobs.store_on = lambda: True
        did = upload_pdf(c, make_pdf(["EG"]), "a.pdf").json()["drawing_id"]
        assert wait_status(c, did)["status"] == "ready" and calls == []
        jobs.store_on = lambda: False
        did = upload_pdf(c, make_pdf(["OG"]), "b.pdf").json()["drawing_id"]
        assert wait_status(c, did)["status"] == "ready" and calls == [did]


def test_svg_with_a_dtd_or_entity_anywhere_is_refused(tmp_path):
    from aura import uploads
    pad = b"<g/>" * 5000  # past the sniffed head
    cases = {
        "late_entity": b"<svg xmlns='http://www.w3.org/2000/svg'>" + pad + b"<!ENTITY a 'x'></svg>",
        "doctype_no_bracket": b"<?xml version='1.0'?><!doctype svg SYSTEM 'x.dtd'><svg/>",
        "plain": b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>",
    }
    got = {}
    for name, data in cases.items():
        p = tmp_path / f"{name}.svg"
        p.write_bytes(data)
        got[name] = uploads.svg_is_safe(p, chunk=4096)
    assert got == {"late_entity": False, "doctype_no_bracket": False, "plain": True}


def test_svg_upload_counts_toward_the_queue_cap(tmp_path):
    with TestClient(make_app(tmp_path, AURA_MAX_QUEUED_UPLOADS=1)) as c:
        c.app.state.drawing_store.ok = True
        c.app.state.drawing_store.on_parsed = lambda *a: None  # the store never finishes here
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        first = c.post("/uploads", files={"file": ("a.svg", svg, "image/svg+xml")})
        assert first.status_code == 202
        second = c.post("/uploads", files={"file": ("b.svg", svg, "image/svg+xml")})
        assert second.status_code == 429


def test_reindex_guard_rate_limit_and_budget_context(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        ds = c.app.state.drawing_store
        ds.ok = True
        ds._spawn = lambda coro: coro.close()
        r = upload_pdf(c, make_pdf(["EG"]), "a.pdf").json()
        did, tok = r["drawing_id"], r["delete_token"]
        assert wait_status(c, did)["status"] == "ready"
        h = {"x-delete-token": tok}
        ds.following.add(did)
        assert c.post(f"/drawings/{did}/reindex", headers=h).status_code == 409  # still working
        ds.following.discard(did)
        assert c.post(f"/drawings/{did}/store/retry", headers=h).status_code == 202
        assert set(ds.ctx[did]) == {"presenter", "ip_hash"}
        assert c.post(f"/drawings/{did}/reindex", headers=h).status_code == 429  # within a minute


def test_forget_waits_for_a_registration_in_progress(tmp_path, monkeypatch):
    import asyncio

    with TestClient(make_app(tmp_path)) as c:
        ds, deleted = _armed(c, monkeypatch)

        async def scenario():
            async with ds.register_lock:
                task = asyncio.create_task(ds.forget(["e" * 16]))
                await asyncio.sleep(0.05)
                assert not task.done() and deleted == []  # blocked behind the registration
            await task
            return deleted
        assert c.portal.call(scenario) == [["e" * 16]]
