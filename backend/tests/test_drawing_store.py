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


class _FakeStore:
    def __init__(self):
        self.deleted: list[list[str]] = []

    def available(self):
        return True

    async def delete(self, file_ids):
        self.deleted.append(sorted(file_ids))


def test_store_project_is_deleted_only_when_no_upload_still_uses_it(tmp_path):
    """Two uploads with identical bytes share one store project: deleting one keeps it."""
    from aura.main import _remove_upload

    with TestClient(make_app(tmp_path)) as c:
        s = c.app.state.svc
        ids = []
        for _ in range(2):
            r = upload_pdf(c, make_pdf(["EG"]), "a.pdf").json()
            assert wait_status(c, r["drawing_id"])["status"] == "ready"
            ids.append(r["drawing_id"])
        for did in ids:
            meta = json.dumps({"kind": "pdf", "store": {"file_id": "f" * 16, "drawing_key": "k", "state": "materialised"}})
            c.portal.call(s.writer.execute, "UPDATE drawings SET meta=? WHERE id=?", (meta, did))
        fake = _FakeStore()
        c.portal.call(_remove_upload, s, ids[0], fake)
        assert fake.deleted == [[]]  # the other upload still uses those bytes
        c.portal.call(_remove_upload, s, ids[1], fake)
        assert fake.deleted[-1] == ["f" * 16]


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
