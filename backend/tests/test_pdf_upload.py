"""PDF floor-plan upload, multi-page house auto-bundling, and `/projects` CRUD tests."""

from __future__ import annotations

import time

import pypdfium2 as pdfium
from fastapi.testclient import TestClient

from tests.conftest import make_app


def make_pdf(page_labels: list[str], *, width: int = 600, height: int = 400) -> bytes:
    """Build a spec-valid PDF 1.4 with vector linework and searchable Type1 Helvetica text per page."""
    objs: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add_obj(b"")  # placeholder filled once page object IDs exist
    kid_ids: list[int] = []

    for label in page_labels:
        safe = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = (
            f"q 2 w 40 40 {width - 80} {height - 80} re S "
            f"100 100 180 120 re S Q "
            f"BT /F1 24 Tf 60 {height - 80} Td ({safe}) Tj ET"
        ).encode("latin-1", errors="replace")
        content_id = add_obj(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )
        page_id = add_obj(
            f"<< /Type /Page /Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii")
        )
        kid_ids.append(page_id)

    kids_ref = " ".join(f"{pid} 0 R" for pid in kid_ids)
    objs[pages_id - 1] = f"<< /Type /Pages /Kids [{kids_ref}] /Count {len(kid_ids)} >>".encode("ascii")
    catalog_id = add_obj(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, body in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf.extend(f"{idx} 0 obj\n".encode("ascii"))
        buf.extend(body)
        buf.extend(b"\nendobj\n")

    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(buf)


def upload_pdf(c: TestClient, data: bytes, name: str = "plan.pdf", headers: dict | None = None):
    return c.post(
        "/uploads",
        files={"file": (name, data, "application/pdf")},
        headers=headers or {},
    )


def wait_status(c: TestClient, did: str, timeout: float = 25.0) -> dict:
    t0 = time.time()
    st: dict = {}
    while time.time() - t0 < timeout:
        st = c.get(f"/drawings/{did}/status").json()
        if st.get("status") in ("ready", "failed"):
            return st
        time.sleep(0.15)
    raise AssertionError(f"{did} still {st}")


def test_single_page_pdf_upload_is_public_zero_model_calls_and_serves_pngs(tmp_path):
    """Anonymous single-page PDF uploads succeed without presenter token and make 0 Gemini calls."""
    app = make_app(tmp_path, AURA_PRESENTER_TOKEN="pres")
    with TestClient(app) as c:
        r = upload_pdf(c, make_pdf(["Erdgeschoss EG"]), "single_floor.pdf")
        assert r.status_code == 202, r.text
        body = r.json()
        did = body["drawing_id"]
        st = wait_status(c, did)
        assert st["status"] == "ready", st

        # 0 Gemini model calls spent on PDF upload (unlike PNG/JPEG raster detection)
        snap = c.app.state.svc.budget.snapshot()["event"]
        assert snap["used"] == snap["seed"]

        d = c.get(f"/drawings/{did}").json()
        assert d["kind"] == "raster"
        assert d["source_ext"] == ".pdf"
        assert d["floor_label"] == "Erdgeschoss"
        assert d["floor_order"] == 0
        assert d.get("project_id") is None

        # Rendered PNG and thumbnail are served; raw source.pdf is kept private
        png = c.get(f"/drawings/{did}/original.png")
        assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
        thumb = c.get(f"/drawings/{did}/thumb.png")
        assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")
        assert c.get(f"/drawings/{did}/source.pdf").status_code == 404


def test_multipage_pdf_auto_bundles_floors_splits_single_page_pdfs_and_cascades_delete(tmp_path):
    """A 3-page house PDF splits into per-floor single-page PDFs, infers vertical floor order,
    auto-creates a ProjectBundle in `/projects`, and cascades cleanly on delete."""
    with TestClient(make_app(tmp_path)) as c:
        # Deliberately out-of-order pages: 1. OG (+1), UG (-1), EG (0)
        pdf_bytes = make_pdf(["Plan 1. OG", "Kellergeschoss UG", "Erdgeschoss EG"])
        r = upload_pdf(c, pdf_bytes, "Villa_Alpen.pdf")
        assert r.status_code == 202
        body = r.json()
        did = body["drawing_id"]
        assert wait_status(c, did)["status"] == "ready"

        p2_id = f"{did}-p2"
        p3_id = f"{did}-p3"
        expected_proj_id = f"proj-{did.removeprefix('up-')}"

        for fid, exp_order in ((did, 1), (p2_id, -1), (p3_id, 0)):
            assert c.get(f"/drawings/{fid}/status").json()["status"] == "ready"
            meta = c.get(f"/drawings/{fid}").json()
            assert meta["project_id"] == expected_proj_id
            assert meta["floor_order"] == exp_order
            assert meta["source_ext"] == ".pdf"
            assert c.get(f"/drawings/{fid}/original.png").status_code == 200

            # Every floor folder has its own single-page source.pdf so single-floor agent runs stay scoped
            src_pdf = tmp_path / "data" / "uploads" / fid / "source.pdf"
            assert src_pdf.is_file()
            doc = pdfium.PdfDocument(str(src_pdf))
            try:
                assert len(doc) == 1
            finally:
                doc.close()

        # GET /projects returns the auto-bundled project sorted vertically: UG (-1) -> EG (0) -> 1. OG (1)
        # Upload-backed bundles are private: listed only with that upload's delete token.
        assert expected_proj_id not in [p["id"] for p in c.get("/projects").json()["projects"]]
        projects = c.get("/projects", headers={"x-delete-token": body["delete_token"]}).json()["projects"]
        proj = next(p for p in projects if p["id"] == expected_proj_id)
        assert proj["title"] == "Villa_Alpen"
        assert proj["auto_matched"] is True
        assert [f["drawing_id"] for f in proj["floors"]] == [p2_id, p3_id, did]
        assert [f["order"] for f in proj["floors"]] == [-1, 0, 1]

        # Cascading delete removes primary, siblings, folders, and auto-bundled project
        del_r = c.delete(f"/drawings/{did}", headers={"x-delete-token": body["delete_token"]})
        assert del_r.status_code == 204
        for fid in (did, p2_id, p3_id):
            assert c.get(f"/drawings/{fid}/status").status_code == 404
            assert not (tmp_path / "data" / "uploads" / fid).exists()
        remaining = [p["id"] for p in c.get("/projects", headers={
            "x-delete-token": body["delete_token"]}).json()["projects"]]
        assert expected_proj_id not in remaining


def test_manual_project_crud_and_validation(tmp_path):
    """Single-plan uploads can be manually grouped via POST /projects and ungrouped via DELETE."""
    with TestClient(make_app(tmp_path)) as c:
        u1 = upload_pdf(c, make_pdf(["2nd Floor"]), "upper.pdf").json()
        u2 = upload_pdf(c, make_pdf(["Basement"]), "lower.pdf").json()
        d_upper, d_lower = u1["drawing_id"], u2["drawing_id"]
        assert wait_status(c, d_upper)["status"] == "ready"
        assert wait_status(c, d_lower)["status"] == "ready"
        mine = {"x-delete-token": f"{u1['delete_token']}, {u2['delete_token']}"}  # comma-separated

        # Bundling uploads needs their delete tokens: someone else's upload is refused.
        stranger = c.post("/projects", json={"title": "Grab", "drawing_ids": [d_upper, d_lower]},
                          headers={"x-delete-token": u1["delete_token"]})
        assert stranger.status_code == 403 and d_lower in stranger.json()["error"]

        # Validation: < 2 floors -> 400; unknown drawing_id -> 404
        assert c.post("/projects", json={"title": "Solo", "drawing_ids": [d_upper]}).status_code == 400
        assert c.post("/projects", json={"title": "Ghost", "drawing_ids": [d_upper, "up-missing"]}).status_code == 404

        # Create manual project via drawing_ids (auto-infers & sorts Basement=-1 before 2nd Floor=2)
        created = c.post(
            "/projects",
            json={"id": "proj-manual-1", "title": "Duplex House", "drawing_ids": [d_upper, d_lower]},
            headers=mine,
        )
        assert created.status_code == 200, created.text
        pb = created.json()
        assert pb["id"] == "proj-manual-1"
        assert pb["auto_matched"] is False
        assert [f["drawing_id"] for f in pb["floors"]] == [d_lower, d_upper]
        assert pb["token"] == pb["project_token"]
        owner = {"x-project-token": pb["token"], **mine}  # shown once, only its hash is stored

        # Someone without the owner token can neither overwrite nor delete it.
        hijack = {"id": "proj-manual-1", "title": "Mine now", "drawing_ids": [d_upper, d_lower]}
        assert c.post("/projects", json=hijack).status_code == 403
        assert c.post("/projects", json=hijack, headers={"x-project-token": "guess"}).status_code == 403
        assert c.delete("/projects/proj-manual-1").status_code == 403
        assert c.post("/projects", json=hijack, headers={"origin": "https://evil.example"}).status_code == 403

        # Also accepts explicit contract ProjectBundle payload with custom labels/orders
        updated = c.post(
            "/projects",
            headers=owner,
            json={
                "id": "proj-manual-1",
                "title": "Duplex House Updated",
                "floors": [
                    {"drawing_id": d_upper, "label": "Penthouse", "order": 10},
                    {"drawing_id": d_lower, "label": "Cellar", "order": -2},
                ],
            },
        )
        assert updated.status_code == 200
        assert [f["label"] for f in updated.json()["floors"]] == ["Cellar", "Penthouse"]

        assert "project_token" not in updated.json()  # never re-issued

        assert [p for p in c.get("/projects").json()["projects"] if p["id"] == "proj-manual-1"] == []
        listed = [p for p in c.get("/projects", headers=owner).json()["projects"] if p["id"] == "proj-manual-1"]
        assert len(listed) == 1 and listed[0]["title"] == "Duplex House Updated"

        # Ungroup / delete returns 204, then 404 on repeat
        assert c.delete("/projects/proj-manual-1", headers=owner).status_code == 204
        assert c.delete("/projects/proj-manual-1", headers=owner).status_code == 404


def test_corrupt_pdf_fails_cleanly_and_ten_page_pdf_caps_at_eight(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        bad = upload_pdf(c, b"%PDF-1.7\nnot a valid xref table\n", "broken.pdf").json()["drawing_id"]
        st = wait_status(c, bad)
        assert st["status"] == "failed" and st.get("error")
        assert c.get("/health").status_code == 200

        ten_up = upload_pdf(c, make_pdf([f"Level {i}" for i in range(1, 11)]), "tower.pdf").json()
        ten = ten_up["drawing_id"]
        assert wait_status(c, ten)["status"] == "ready"
        proj_id = f"proj-{ten.removeprefix('up-')}"
        proj = next(p for p in c.get("/projects", headers={"x-delete-token": ten_up["delete_token"]})
                    .json()["projects"] if p["id"] == proj_id)
        assert len(proj["floors"]) == 8
        assert c.get(f"/drawings/{ten}-p8/status").json()["status"] == "ready"
        assert c.get(f"/drawings/{ten}-p9/status").status_code == 404


# ---- WP7: image detection on PDF pages ---------------------------------------------------------

def _fake_detect_hook(calls: list[str], *, fail: bool = False):
    """Stands in for main._raster_hook: writes the same raster.json + capabilities, no model call."""
    import json

    from aura.solvers import raster

    async def hook(drawing_id, out, ctx):
        calls.append(drawing_id)
        if fail:
            raise RuntimeError("model down")
        items = [{"label": "door", "box_2d": [100, 100, 150, 140]},
                 {"label": "door", "box_2d": [300, 500, 360, 540]},
                 {"label": "window", "box_2d": [10, 400, 30, 480]}]
        boxes = raster.boxes_from_items(items)
        r = {"boxes": boxes, "counts": {s: len(v) for s, v in boxes.items()}, "items": items,
             "model": "fake", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
             "latency_ms": 1}
        (out / "raster.json").write_text(json.dumps(r))
        dp = out / "drawing.json"
        d = json.loads(dp.read_text())
        d["capabilities"] = list(raster.SUBJECTS)
        dp.write_text(json.dumps(d))

    return hook


def test_pdf_pages_get_image_detection_labelled_as_estimates(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        calls: list[str] = []
        c.app.state.jobs.after_raster = _fake_detect_hook(calls)
        did = upload_pdf(c, make_pdf(["Erdgeschoss EG", "Plan 1. OG"]), "Haus.pdf").json()["drawing_id"]
        st = wait_status(c, did)
        assert st["status"] == "ready" and not st.get("error"), st
        assert sorted(calls) == sorted([did, f"{did}-p2"])  # every page of the bundle
        for fid in (did, f"{did}-p2"):
            assert c.get(f"/drawings/{fid}").json()["capabilities"] == ["door", "window"]
            b = c.post("/ask", json={"drawing_id": fid,
                                     "question": "How many doors are drawn on this floor plan?"}).json()
            assert b["answer"] == {"kind": "count", "value": 2, "subject": "door"}
            assert b["method"] == "vision" and "gold" not in b
            assert len(b["evidence"]["boxes"]) == 2
            ans = next(n for n in b["trace"] if n["id"] == "answer")
            assert "Model estimate" in ans["body"]


def test_pdf_detection_failure_leaves_the_upload_ready_and_not_indexed(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        calls: list[str] = []
        c.app.state.jobs.after_raster = _fake_detect_hook(calls, fail=True)
        did = upload_pdf(c, make_pdf(["Erdgeschoss EG", "Plan 1. OG"]), "Haus.pdf").json()["drawing_id"]
        st = wait_status(c, did)
        assert st["status"] == "ready", st
        assert "Not indexed" in (st.get("error") or "")
        assert len(calls) == 2
        for fid in (did, f"{did}-p2"):
            assert c.get(f"/drawings/{fid}/status").json()["status"] == "ready"
            assert c.get(f"/drawings/{fid}").json()["capabilities"] == []
            assert c.get(f"/drawings/{fid}/original.png").status_code == 200
