"""The store library (aura.store_library): store drawings as shared, permanent library drawings.

No database here: `FakeSource` stands in for jstore (the listing rows it would read and the documents
`aura_model` would build; the latter is the captured fixture the drawing-store tests use)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aura import steps, store_library, store_sql, tools
from aura.config import Settings
from aura.contract import CONTRACT_VERSION, Drawing, DrawingSummary, ProjectList
from aura.store_library import (
    Licences,
    StoreLibrary,
    build_entries,
    excluded,
    make_id,
    method_of,
    patterns_of,
)
from tests.conftest import make_app

FIX = Path(__file__).parent / "fixtures" / "store_site1000_floor9385.json"
DEFAULT_EXCLUDE = patterns_of(Settings().store_library_exclude)


def row(key, project, formats=("png",), *, usable=True, image=True, cad=False, counts=None, storeys=None,
        scale=None):
    return {"key": key, "project": project, "usable": usable, "scale": scale, "ingested": 1_700_000_000.0,
            "has_image": image, "has_cad": cad, "counts": counts or {},
            "files": [{"file_id": f"{abs(hash((key, f))) % 16**16:016x}", "path": f"data/{project.split('/')[0]}/{key}.{f}",
                       "format": f} for f in formats],
            "storeys": storeys or []}


def storey(slug, name, elevation, rooms=0, items=0, shapes=0):
    return {"slug": slug, "name": name, "elevation": elevation, "n_rooms": rooms, "n_items": items,
            "n_shapes": shapes}


ROWS = [
    row("school_plan", "stress/school_plan", counts={"room": 80, "door": 129, "window": 82, "toilet": 12}, scale="1:200"),
    row("high_quality-1", "cubicasa5k/high_quality-1", counts={"room": 11, "door": 8}),
    row("site1_bldg2_floor3", "swiss_dwellings/site1/bldg2", ("dxf", "pdf", "png"), counts={"room": 25, "apartment": 3}),
    row("covered_floor", "swiss_dwellings/site9/bldg9", ("dxf",), counts={"room": 5}),
    row("Sheet - A001 - SITE PLAN", "opening_design/Park", ("dwg",), image=False, cad=True, counts={"door": 1}),
    row("old_scan", "habs/x", ("jpg",)),
    row("tower", "ifc_models/tower", ("ifc",), image=False, storeys=[
        storey("eg", "EG", 0.0, rooms=4, items=9), storey("og-1", "OG 1", 3.0, rooms=5, items=7),
        storey("roof", "Roof", 6.0, shapes=12)]),
    row("shed", "ifc_models/shed", ("ifc",), image=False, storeys=[storey("unassigned", "", None, shapes=3)]),
    row("duplex_arc", "ifc_bench/duplex", ("ifc",), image=False, storeys=[storey("l1", "Level 1", 0.0, rooms=9)]),
    row("bsc-clinic_clinic-plumbing", "ifc_models/bsc-clinic", ("ifc",), image=False,
        storeys=[storey("l1", "Level 1", 0.0, items=9)]),
    row("someones_upload", "upload/abc", counts={"room": 3}),
    row("broken", "habs/y", ("jpg",), usable=False),
    row("resbim-388", "ifc_models/resbim-388", ("ifc",), image=False),  # no storey with content, no page
]


def entries(**kw):
    return build_entries(ROWS, kw.pop("covered", {"covered_floor"}), kw.pop("patterns", DEFAULT_EXCLUDE), **kw)


class FakeSource:
    """What JStoreSource gives, from memory. Counts the builds (laziness, caching)."""

    def __init__(self, rows=None, tables=None):
        self.rows_ = ROWS if rows is None else rows
        self.sig, self.doc_sig = "s1", "d1"
        self.tables = tables  # ref tables given to every entity (None: the fixture's own)
        self.builds: list[tuple[str, str | None]] = []
        self.listings = 0
        self.fail = False

    def signature(self):
        if self.fail:
            raise ConnectionError("down")
        return self.sig

    def rows(self):
        self.listings += 1
        return self.rows_

    def file_ids(self):
        if self.fail:
            raise ConnectionError("down")
        return {f["file_id"] for r in self.rows_ if not r["project"].startswith("upload/") for f in r["files"]}

    def drawing_signature(self, key):
        return self.doc_sig

    def build(self, key, slug):
        self.builds.append((key, slug))
        b = json.loads(FIX.read_text())
        if self.tables is not None:
            b["sources"] = {k: list(self.tables) for k in b["sources"]}
        return {"doc": b["doc"], "evidence": b["evidence"], "model": b["model"], "sources": b["sources"],
                "page": "renders/f1/p0.png", "synthetic": False}

    def plan_svg(self, key, slug, page):
        return f'<svg xmlns="http://www.w3.org/2000/svg"><!-- {key} {page} --></svg>'

    def plan_png(self, key, slug, side):
        return b"\x89PNG fake"


def library(tmp_path, source=None, *, store_map=True, **settings) -> StoreLibrary:
    if store_map:  # one bundle already shows the drawing `covered_floor`
        (tmp_path / "build").mkdir(exist_ok=True)
        (tmp_path / "build" / "store-map.json").write_text(json.dumps({
            "swiss-9": {"project": "p", "files": [{"sha256_16": "ab" * 8, "drawing_key": "covered_floor"}]}}))
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "build" / "drawings",
                 AURA_STORE_DATA_DIR=tmp_path / "store", **settings)
    return StoreLibrary(s, source=source or FakeSource())


def with_library(c, tmp_path, source=None, **settings) -> StoreLibrary:
    """The running app with a store library over a fake store (as create_app wires the real one)."""
    lib = library(tmp_path, source, **settings)
    lib.registry = c.app.state.svc.registry
    c.app.state.svc.registry.store_library = c.app.state.drawing_store.library = lib
    return lib


def page_render(tmp_path, root="store") -> Path:
    from PIL import Image

    p = tmp_path / root / "renders" / "f1" / "p0.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 400), "white").save(p)
    return p


# ---- ids ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["site1000_bldg2082_floor9385", "Xcel Sports Complex-Sheet - A-100-A - Site Plan",
                                 "1101_https___www_searchculture_gr_aggregator_edm_theocharakis_000163_111587" * 3,
                                 "_leading", "Ünïcödé ключ", "up-looks-like-an-upload", "k.x~y"])
def test_ids_are_stable_url_safe_and_never_upload_ids(key):
    did = make_id(key)
    assert did == make_id(key) and tools.ID_RE.match(did) and did.startswith("st-") and not did.startswith("up-")
    storey_id = make_id(key, "a" * 40 + "-2")
    assert tools.ID_RE.match(storey_id) and storey_id.startswith(did + "--") and len(storey_id) <= 100


def test_ids_differ_for_keys_that_share_a_slug():
    assert make_id("a_b") != make_id("a-b") != make_id("A B")


# ---- listing --------------------------------------------------------------------------------------


def test_listing_keeps_what_can_be_shown_and_never_the_rest():
    es = entries()
    keys = [e.key for e in es]
    assert set(keys) == {"school_plan", "high_quality-1", "site1_bldg2_floor3", "Sheet - A001 - SITE PLAN",
                         "old_scan", "tower", "shed"}
    assert "someones_upload" not in keys            # upload/% projects
    assert "covered_floor" not in keys              # a bundle already shows it (store-map.json)
    assert "broken" not in keys and "resbim-388" not in keys   # unreadable / nothing to show
    assert "duplex_arc" not in keys and "bsc-clinic_clinic-plumbing" not in keys   # CC BY-ND (default exclusion)
    assert keys[-1] == "old_scan"                   # interpreted drawings first


def test_default_exclusion_is_the_two_nd_models_and_patterns_are_globs():
    assert DEFAULT_EXCLUDE == ["duplex_arc", "bsc-clinic_clinic-plumbing"]
    assert excluded("duplex_arc", DEFAULT_EXCLUDE) and not excluded("duplex_arc_2", DEFAULT_EXCLUDE)
    es = entries(patterns=patterns_of("site*_floor3 , high_quality-?"))
    assert {e.key for e in es} == {"school_plan", "Sheet - A001 - SITE PLAN", "old_scan", "tower", "shed",
                                   "duplex_arc", "bsc-clinic_clinic-plumbing"}


def test_list_entries_have_the_contract_shape_and_the_filter_fields():
    by_key = {e.key: e for e in entries() if e.slug is None}
    for e in entries():
        s = DrawingSummary.model_validate(e.summary)   # extra="forbid": exactly the contract's fields
        assert s.source == "library" and s.status == "ready" and s.id == e.id and s.facts is None
        assert s.thumbnail.startswith(f"/drawings/{e.id}/") and s.credit.text and s.credit.license
    school = by_key["school_plan"].summary
    assert school["kind"] == "raster" and school["source_ext"] == ".png"
    assert school["subtitle"] == "Stress test · PNG · 1:200 · 80 rooms · 129 doors · 82 windows · 12 fixtures"
    swiss = by_key["site1_bldg2_floor3"].summary
    assert swiss["title"] == "Site 1 · Building 2 · Floor 3" and swiss["kind"] == "cad_dxf"
    assert swiss["source_ext"] == ".dxf" and "Swiss Dwellings · DXF + PDF + PNG" in swiss["subtitle"]
    dwg = by_key["Sheet - A001 - SITE PLAN"].summary   # the type filter has no DWG: it stays under its kind
    assert dwg["kind"] == "cad_dxf" and "source_ext" not in dwg and dwg["thumbnail"].startswith(
        f"/drawings/{dwg['id']}/plan.svg")
    assert "not interpreted" in by_key["old_scan"].summary["subtitle"]
    assert by_key["high_quality-1"].summary["title"] == "High quality 1"


def test_ifc_model_is_one_entry_per_storey_grouped_as_a_building(tmp_path):
    tower = [e for e in entries() if e.key == "tower"]
    assert [e.slug for e in tower] == ["eg", "og-1"]   # the footprint-only roof is left out
    assert [e.summary["title"] for e in tower] == ["Tower · EG", "Tower · OG 1"]
    assert {e.summary["project_id"] for e in tower} == {make_id("tower")}
    assert [(e.summary["floor_label"], e.summary["floor_order"]) for e in tower] == [("EG", 0), ("OG 1", 1)]
    assert all(e.summary["kind"] == "ifc" and e.summary["source_ext"] == ".ifc" for e in tower)
    shed = next(e for e in entries() if e.key == "shed")   # a model with outlines only still shows them
    assert shed.slug == "unassigned" and "project_id" not in shed.summary and "outlines only" in shed.summary["subtitle"]
    projects = library(tmp_path).projects()
    assert len(projects) == 1 and ProjectList.model_validate({"projects": projects})
    assert projects[0]["id"] == make_id("tower") and projects[0]["auto_matched"] is False
    assert [f["drawing_id"] for f in projects[0]["floors"]] == [e.id for e in tower]


def test_licence_file_gives_title_credit_and_link(tmp_path):
    src = tmp_path / "dataset" / "data" / "habs"
    src.mkdir(parents=True)
    (src / "LICENSE.md").write_text(
        "# habs\n\n| file | title | licence | credit | source |\n|---|---|---|---|---|\n"
        "| old_scan.jpg | Site Plan - Tannehill Furnace | PDM | HAER, National Park Service | https://www.loc.gov/x |\n"
        "\n- PDM: https://creativecommons.org/publicdomain/mark/1.0/\n", encoding="utf-8")
    cub = tmp_path / "dataset" / "data" / "cubicasa5k"
    cub.mkdir(parents=True)
    (cub / "LICENSE.md").write_text("All files: CC-BY-NC-SA-4.0. Credit: CubiCasa5K, (c) 2019 CubiCasa.\n\n"
                                    "- CC-BY-NC-SA-4.0: https://creativecommons.org/licenses/by-nc-sa/4.0/\n")
    by_key = {e.key: e.summary for e in entries(licences=Licences(tmp_path / "dataset"))}
    assert by_key["old_scan"]["title"] == "Site Plan - Tannehill Furnace"
    assert by_key["old_scan"]["credit"] == {"text": "HAER, National Park Service", "license": "PDM",
                                            "url": "https://www.loc.gov/x"}
    assert by_key["high_quality-1"]["credit"] == {
        "text": "CubiCasa5K, (c) 2019 CubiCasa.", "license": "CC-BY-NC-SA-4.0",
        "url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"}
    assert by_key["school_plan"]["credit"]["license"] == "LicenseRef-source-dataset"   # no licence row: generic


def test_listing_is_cached_until_the_store_signature_changes(tmp_path, monkeypatch):
    src = FakeSource()
    lib = library(tmp_path, src)
    assert lib.count() == 8 and lib.count() == 8 and src.listings == 1 and src.builds == []   # no document built
    monkeypatch.setattr(store_library, "LIST_TTL_S", 0.0)
    assert lib.count() == 8 and src.listings == 1          # same signature: the rows are not read again
    src.sig = "s2"
    src.rows_ = ROWS[:2]
    assert lib.count() == 2 and src.listings == 2
    src.fail = True                                        # Postgres down: the last listing stays
    assert lib.count() == 2


def test_bundle_covered_keys_come_from_the_store_map(tmp_path):
    bare = library(tmp_path, store_map=False)
    assert bare.count() == 9 and "covered_floor" in {e.key for e in bare._entries.values()}
    lib = library(tmp_path)
    assert lib.count() == 8 and "covered_floor" not in {e.key for e in lib._entries.values()}


# ---- documents ------------------------------------------------------------------------------------


def test_method_of_is_decided_by_the_weakest_entity():
    assert method_of([{"cad_entity"}, {"cad_entity", "raster_detection", "pdf_path"}]) is None   # CAD confirms it
    assert method_of([{"ifc_element"}]) is None
    assert method_of([{"pdf_path", "pdf_text"}]) == "pdf_vector"
    assert method_of([{"pdf_path"}, {"raster_word"}]) == "ocr"
    assert method_of([{"cad_entity"}, {"raster_detection"}]) == "vision"
    assert method_of([{"region"}]) == "vision" and method_of([set()]) == "vision"   # unknown is never measured


def test_document_has_the_contract_shape_our_id_and_honest_methods(tmp_path):
    lib = library(tmp_path)
    e = next(x for x in lib.summaries() if x["title"] == "School plan")
    d = lib.get(e["id"])
    pub = d.public_drawing()
    doc = Drawing.model_validate(pub)
    assert pub["contract_version"] == CONTRACT_VERSION == "1.2" and doc.id == e["id"] and doc.source == "library"
    assert doc.kind == "raster" and doc.source_ext == ".png" and doc.title == "School plan"
    assert doc.frame.y_flip is True and len(doc.frame.px) == 2 and len(doc.rooms) == 31
    assert all(u.startswith(f"/drawings/{e['id']}/") for k, u in pub["images"].items() if k != "original_alignment")
    assert pub["images"]["original_alignment"] == "exact" and "?v=" in pub["images"]["rebuilt"]
    assert not {"evidence", "texts", "gold"} & set(pub)
    # as on a store-indexed upload: detections are estimates, PDF vectors are not, nothing is "cad"
    em = d.drawing["evidence_method"]
    assert em["door"] == "vision" and em["room"] == "pdf_vector" and "cad_geometry" not in em.values()
    boxes = [b for v in d.drawing["evidence"].values() for b in v]
    assert boxes and all("note" not in b and isinstance(b["store_ref"], int) for b in boxes)
    tr = tools.count_objects(d, "door", {"kind": "floor"})
    assert tr.value == 29 and tr.method == "vision" and tr.meta.get("estimate") is True
    assert {b.id for b in tr.boxes} == {b["id"] for b in d.drawing["evidence"]["door"]}   # highlights = the document


def test_cad_and_ifc_sources_are_exact_not_estimates(tmp_path):
    lib = library(tmp_path, FakeSource(tables=("cad_entity", "pdf_path", "raster_detection")))
    d = lib.get(make_id("site1_bldg2_floor3"))
    assert set(d.drawing["evidence_method"].values()) == {"cad_geometry"}
    tr = tools.count_objects(d, "door", {"kind": "floor"})
    assert tr.method == "cad_geometry" and not tr.meta.get("estimate") and "geometry" in tr.summary
    assert steps._box_pad(d, "door") is None   # the store's boxes are not padded like our solvers'


def test_documents_are_lazy_cached_and_rebuilt_when_the_drawing_changes(tmp_path, monkeypatch):
    src = FakeSource()
    lib = library(tmp_path, src)
    did = make_id("school_plan")
    d1 = lib.get(did)
    assert src.builds == [("school_plan", None)] and lib.get(did) is d1 and len(src.builds) == 1
    assert json.loads((d1.dir / "drawing.json").read_text())["id"] == did
    assert d1.dir == tmp_path / "data" / "store-library" / did
    v1 = tools.data_version(d1)
    assert v1 > 0                                           # the answer cache has a data version to key on
    again = library(tmp_path, src)                          # a restart: read from the cache, not rebuilt
    assert again.get(did).drawing["rooms"] == d1.drawing["rooms"] and len(src.builds) == 1
    monkeypatch.setattr(store_library, "DOC_TTL_S", 0.0)
    src.doc_sig = "d2"
    (d1.dir / "plan.svg").write_text("old")
    d2 = lib.get(did)
    assert d2 is not d1 and len(src.builds) == 2 and not (d2.dir / "plan.svg").exists()
    assert d2.public_drawing()["images"]["rebuilt"] != d1.public_drawing()["images"]["rebuilt"]   # new ?v=


def test_unknown_excluded_and_upload_ids_are_not_served(tmp_path):
    src = FakeSource()
    lib = library(tmp_path, src)
    for key in ("duplex_arc", "someones_upload", "covered_floor", "nope"):
        assert lib.get(make_id(key)) is None
    assert src.builds == []
    src.build = lambda key, slug: None                       # e.g. the model turned out to be an upload
    assert lib.get(make_id("school_plan")) is None


# ---- images ---------------------------------------------------------------------------------------


def test_page_renders_are_found_under_the_store_dir_then_the_extra_roots(tmp_path):
    lib = library(tmp_path, AURA_STORE_RENDER_DIRS=f"{tmp_path / 'nowhere'}, {tmp_path / 'old-store'}")
    assert lib.render_path("renders/f1/p0.png") is None
    extra = page_render(tmp_path, "old-store")
    assert lib.render_path("renders/f1/p0.png") == extra.resolve()
    own = page_render(tmp_path, "store")
    assert lib.render_path("renders/f1/p0.png") == own.resolve()          # the store's own dir first
    (tmp_path / "secret.png").write_bytes(b"x")
    assert lib.render_path("../secret.png") is None and lib.render_path(str(tmp_path / "secret.png")) is None


def test_images_are_generated_once_into_the_data_dir(tmp_path):
    page = page_render(tmp_path)
    lib = library(tmp_path)
    d = lib.get(make_id("school_plan"))
    assert d.file("original.png") == page.resolve()
    thumb = d.file("thumb.png")
    assert thumb.parent == d.dir and thumb.read_bytes()[:2] == b"\xff\xd8" and thumb.stat().st_size < page.stat().st_size * 5
    svg = d.file("plan.svg")
    assert svg.parent == d.dir and svg.suffix == ".svg" and str(page.resolve()) in svg.read_text()
    lib.source.plan_svg = lambda *a: pytest.fail("generated twice")
    assert d.file("plan.svg") == svg
    assert d.file("drawing.json") is None and d.file("../x") is None
    assert d.image_path() is None   # an interpreted drawing answers from its index: no image check


# ---- the app --------------------------------------------------------------------------------------


def test_app_lists_opens_and_serves_store_drawings_for_everyone(tmp_path):
    page_render(tmp_path)
    with TestClient(make_app(tmp_path)) as c:
        before = c.get("/drawings").json()["drawings"]
        lib = with_library(c, tmp_path)
        listed = c.get("/drawings").json()
        assert listed["contract_version"] == "1.2"
        rows = listed["drawings"]
        assert rows[:len(before)] == before and len(rows) == len(before) + 8    # bundles first, as before
        for r in rows:
            DrawingSummary.model_validate(r)
        did = make_id("school_plan")
        assert did in {r["id"] for r in rows} and lib.source.builds == []       # listing builds no document
        assert c.get("/health").json()["checks"]["drawings"] == len(rows)
        doc = c.get(f"/drawings/{did}")
        assert doc.status_code == 200 and Drawing.model_validate(doc.json()).id == did
        assert "expires_at" not in doc.json()
        assert c.get(f"/drawings/{did}/status").json() == {"contract_version": "1.2", "id": did, "status": "ready"}
        for name, ctype in (("original.png", "image/png"), ("thumb.png", "image/jpeg"), ("plan.svg", "image/svg+xml")):
            r = c.get(f"/drawings/{did}/{name}")
            assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
            assert r.headers["cache-control"] == "public, max-age=3600"
        assert c.get(f"/drawings/{did}/drawing.json").status_code == 404
        assert c.get(f"/drawings/{make_id('duplex_arc')}").status_code == 404          # excluded
        assert c.get(f"/drawings/{make_id('someones_upload')}").status_code == 404     # an upload's key
        projects = c.get("/projects").json()["projects"]
        assert [p["id"] for p in projects] == [make_id("tower")] and len(projects[0]["floors"]) == 2


def test_flag_off_lists_nothing_from_the_store(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        assert c.app.state.svc.registry.store_library is None
        assert c.get(f"/drawings/{make_id('school_plan')}").status_code == 404
    assert Settings().store_library is False


def test_store_drawings_are_permanent_delete_refused_cleanup_and_sweep_keep_them(tmp_path, monkeypatch):
    from aura.main import cleanup_once

    with TestClient(make_app(tmp_path)) as c:
        lib = with_library(c, tmp_path)
        did = make_id("school_plan")
        assert c.get(f"/drawings/{did}").status_code == 200
        r = c.delete(f"/drawings/{did}", headers={"x-delete-token": "anything"})
        assert r.status_code == 403 and "Only uploads" in r.json()["error"]
        assert c.post(f"/drawings/{did}/reindex", headers={"x-delete-token": "x"}).status_code == 404
        folder = lib.get(did).dir
        c.portal.call(cleanup_once, c.app.state.svc, 4_000_000_000.0, c.app.state.drawing_store)   # far past any TTL
        assert folder.is_dir() and c.get(f"/drawings/{did}").status_code == 200

        # the store side: every dataset file id is permanent, so forget() and the sweep never delete one
        ds = c.app.state.drawing_store
        ds.ok = True
        deleted: list[list[str]] = []

        async def delete(file_ids):
            deleted.append(sorted(file_ids))
        monkeypatch.setattr(ds, "delete", delete)
        fid = lib.entry(did).file_ids[0]
        assert fid in ds.permanent_file_ids()
        assert not any(f in ds.permanent_file_ids() for r in ROWS if r["project"].startswith("upload/") for f in
                       (x["file_id"] for x in r["files"]))
        monkeypatch.setattr(ds, "_upload_projects", lambda: [fid, "c" * 16])
        c.portal.call(ds.sweep)
        c.portal.call(ds.forget, [fid])
        assert deleted == [["c" * 16], []]

        lib.source.fail = True                       # Postgres down, cached ids still protect the files
        assert fid in ds.permanent_file_ids()
        lib._file_ids = None
        with pytest.raises(ConnectionError):         # nothing cached: a delete waits instead of guessing
            ds.permanent_file_ids()


def test_exact_questions_answer_from_the_store_document_without_a_model(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        with_library(c, tmp_path)
        did = make_id("school_plan")
        r = c.post("/ask", json={"drawing_id": did, "question": "How many doors are there?"}).json()
        assert r["answer"] == {"kind": "count", "value": 29, "subject": "door"} and r["calls"] == 0
        assert r["method"] == "vision"                                   # a detection: an estimate
        assert len(r["evidence"]["boxes"]) == 29
        rooms = c.post("/ask", json={"drawing_id": did, "question": "How many rooms are there?"}).json()
        assert rooms["answer"]["kind"] == "count" and rooms["method"] == "pdf_vector" and rooms["calls"] == 0


# ---- the sql tool ---------------------------------------------------------------------------------


def test_sql_scope_and_ownership(tmp_path):
    lib = library(tmp_path)
    scan = lib.get(make_id("school_plan"))
    files = lib.entry(scan.id).file_ids
    # a store-library drawing: the store owns rooms and items, scoped to its drawing key and files
    assert store_sql.store_owns_things(scan)
    assert store_sql.scope_for(scan) == store_sql.StoreScope(("school_plan",), files)
    store_sql.check_owner("select count(*) from q.doors", scan)           # not refused
    # one storey of a multi-storey model: Layer 2 has no storey scope, so it stays closed (files only)
    eg = lib.get(make_id("tower", "eg"))
    assert not store_sql.store_owns_things(eg)
    assert store_sql.scope_for(eg) == store_sql.StoreScope((), lib.entry(eg.id).file_ids)
    with pytest.raises(store_sql.SqlRefused):
        store_sql.check_owner("select count(*) from q.doors", eg)
    assert "'EG'" in eg.sql_note and scan.sql_note == ""
    # a single-storey model is the whole drawing
    shed = lib.get(make_id("shed", "unassigned"))
    assert store_sql.store_owns_things(shed) and shed.sql_note == ""


def test_bundles_and_uploads_keep_their_owner_rule():
    """The refusal for bundle-owned drawings is not weakened: the flag is a property of StoreDrawing,
    never something a drawing file can claim."""
    bundle = tools.load_file(Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "drawing.swiss-46639.json")
    claimed = SimpleNamespace(**{**bundle.__dict__, "drawing": {
        **bundle.drawing, "store": {"drawing_key": "dk", "file_ids": ["f"], "owner": "store"}}})
    assert not store_sql.store_owns_things(bundle) and not store_sql.store_owns_things(claimed)
    assert store_sql.scope_for(claimed) == store_sql.StoreScope((), ("f",))
    with pytest.raises(store_sql.SqlRefused):
        store_sql.check_owner("select count(*) from q.rooms", claimed)


def test_sql_answers_about_pixel_traced_rooms_are_estimates():
    res = store_sql.SqlResult(ok=True, query="select count(*) from q.rooms", views=["rooms"],
                              methods=["measured_outline", "region"])
    assert store_sql.method_of(res)[:2] == ("vision", True)
    cad = store_sql.SqlResult(ok=True, query="select count(*) from q.rooms", views=["rooms"],
                              methods=["cad_entity", "regional_plan"])
    assert store_sql.method_of(cad)[1] is False


# ---- concurrency: the event loop never waits for the store ---------------------------------------


class LoopWatch(FakeSource):
    """Records every store call that was made from a thread running an event loop."""

    def __init__(self):
        super().__init__()
        self.on_loop: list[str] = []

    def _seen(self, what):
        if store_library._on_loop():
            self.on_loop.append(what)

    def signature(self):
        self._seen("signature")
        return super().signature()

    def rows(self):
        self._seen("rows")
        return super().rows()

    def file_ids(self):
        self._seen("file_ids")
        return super().file_ids()

    def drawing_signature(self, key):
        self._seen("drawing_signature")
        return super().drawing_signature(key)

    def build(self, key, slug):
        self._seen("build")
        return super().build(key, slug)


def test_requests_never_ask_the_store_or_build_on_the_event_loop(tmp_path, monkeypatch):
    page_render(tmp_path)
    with TestClient(make_app(tmp_path)) as c:
        src = LoopWatch()
        lib = with_library(c, tmp_path, src)
        monkeypatch.setattr(store_library, "LIST_TTL_S", 0.0)   # every call would re-read, if it could
        monkeypatch.setattr(store_library, "DOC_TTL_S", 0.0)
        monkeypatch.setattr(store_library, "FILE_IDS_TTL_S", 0.0)
        did, eg, og = make_id("school_plan"), make_id("tower", "eg"), make_id("tower", "og-1")
        assert c.get("/projects").status_code == 200
        assert c.delete(f"/drawings/{did}").status_code == 403
        assert c.get(f"/drawings/{did}").status_code == 200
        assert c.get(f"/drawings/{did}/plan.svg").status_code == 200
        r = c.post("/ask", json={"drawing_id": eg, "question": "How many doors are there?"})
        assert r.status_code == 200
        assert c.get(f"/answers/{r.json()['answer_id']}/evidence.csv").status_code == 200
        assert c.post("/projects", json={"title": "Mine", "drawing_ids": [did, eg]}).status_code in (200, 201)
        c.portal.call(c.app.state.drawing_store.forget, ["0" * 16])
        assert {k for k, _ in src.builds} >= {"school_plan", "tower"}
        assert (("tower", "og-1") in src.builds)        # the sibling storey was warmed with the one asked
        assert src.on_loop == []
        # on the loop a loaded drawing is served as it is, an unknown id is a plain miss: no store call
        calls = len(src.builds)

        async def on_loop():
            return lib.get(og), lib.get("st-nope-00000000"), lib.projects(), lib.summaries()

        got = c.portal.call(on_loop)
        assert got[0] is not None and got[1] is None and len(got[3]) == 8
        assert src.on_loop == [] and len(src.builds) == calls


def test_the_state_lock_is_free_while_the_store_is_asked(tmp_path):
    import threading

    src = FakeSource()
    lib = library(tmp_path, src)
    lib.summaries()
    entered, release = threading.Event(), threading.Event()
    slow = src.build

    def build(key, slug):
        entered.set()
        assert release.wait(5)
        return slow(key, slug)

    src.build = build
    t = threading.Thread(target=lib.get, args=(make_id("school_plan"),))
    t.start()
    assert entered.wait(5)
    try:   # one drawing is being built: the listing and another drawing are not held up
        assert lib.lock.acquire(timeout=1)
        lib.lock.release()
        assert len(lib.summaries()) == 8
        src.build = slow
        assert lib.get(make_id("old_scan")) is not None
    finally:
        release.set()
        t.join(5)
    assert lib.get(make_id("school_plan")) is not None


def test_listing_change_keeps_loaded_drawings_and_a_rebuild_gets_new_image_files(tmp_path, monkeypatch):
    page_render(tmp_path)
    src = FakeSource()
    lib = library(tmp_path, src)
    did = make_id("school_plan")
    d = lib.get(did)
    svg = d.file("plan.svg")
    # the store changed somewhere else: the listing is re-read, this drawing's entry and document stay
    src.sig = "s2"
    lib._list_checked = 0.0
    assert lib.get(did) is d and src.listings == 2 and len(src.builds) == 1
    # this drawing changed: rebuilt, its images get new files and the old ones are gone
    src.doc_sig = "d2"
    lib._doc_checked.clear()
    d2 = lib.get(did)
    assert d2 is not d and len(src.builds) == 2
    (d.dir / svg.name).write_text("late write for the old version")   # an image request that lost the race
    svg2 = d2.file("plan.svg")
    assert svg2.name != svg.name and "late write" not in svg2.read_text()
    assert d2.public_drawing()["images"] != d.public_drawing()["images"]              # so browsers fetch the new ones


def test_thumbnail_leaves_the_process_pixel_limit_alone(tmp_path):
    from PIL import Image

    page_render(tmp_path)
    before = Image.MAX_IMAGE_PIXELS
    assert library(tmp_path).get(make_id("school_plan")).file("thumb.png") is not None
    assert Image.MAX_IMAGE_PIXELS == before


# ---- wiring ---------------------------------------------------------------------------------------


def test_flag_on_without_a_store_stays_off_and_says_so(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="aura.main"), \
            TestClient(make_app(tmp_path, AURA_STORE_LIBRARY=True)) as c:
        assert c.app.state.svc.registry.store_library is None and c.app.state.drawing_store.library is None
        assert len(c.get("/drawings").json()["drawings"]) == 1
    assert any("store library stays off" in r.getMessage() for r in caplog.records)


def test_lifespan_wires_the_library_when_the_store_is_up(tmp_path, monkeypatch):
    from aura import drawing_store

    async def start(self):
        self.ok = True

    async def none(self, *a, **k):
        return 0

    monkeypatch.setattr(drawing_store.DrawingStore, "start", start)
    monkeypatch.setattr(drawing_store.DrawingStore, "resume_all", none)
    monkeypatch.setattr(drawing_store.DrawingStore, "sweep", none)
    monkeypatch.setattr(drawing_store.DrawingStore, "stop", none, raising=False)
    monkeypatch.setattr(store_library, "JStoreSource", FakeSource)
    with TestClient(make_app(tmp_path, AURA_STORE_LIBRARY=True, AURA_STORE_DSN="postgresql://u:p@127.0.0.1:1/x")) as c:
        lib = c.app.state.svc.registry.store_library
        assert isinstance(lib, StoreLibrary) and c.app.state.drawing_store.library is lib
        assert lib.source.listings == 1                         # read once at startup, off the loop
        assert len(c.get("/drawings").json()["drawings"]) == 1 + 9   # no store map here: covered_floor too


# ---- the real reader, against a scratch store database (skipped without one) ----------------------

SCRATCH = "AURA_TEST_STORE_DSN"   # e.g. postgresql://postgres:<password>@127.0.0.1:54329/dev_api


@pytest.fixture
def scratch_store(monkeypatch):
    import os

    dsn = os.environ.get(SCRATCH)
    if not dsn:
        pytest.skip(f"set {SCRATCH} to a scratch store database")
    assert not dsn.rstrip("/").endswith("/postgres"), "a scratch database, never the one named postgres"
    import jstore.db as jdb

    monkeypatch.setattr(jdb, "DSN", dsn)
    lib_f, up_f, bad_f = "c" * 15 + "1", "c" * 15 + "2", "c" * 15 + "3"

    def wipe(db):
        db.run("delete from jstore.project where project_id in ('stlib/one', 'stlib/two', :u)", u=f"upload/{up_f}")

    jdb.apply_schema()
    with jdb.DB() as db:
        wipe(db)
        for pid in ("stlib/one", "stlib/two", f"upload/{up_f}"):
            db.run("insert into jstore.project (project_id, title, source) values (:p, 't', 'test')", p=pid)
        ins = """insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status, ingested_at)
                 values (:f, :p, :path, 'png', 6, :k, :s, now())"""
        db.run(ins, f=lib_f, p="stlib/one", path="data/stlib/scan.png", k="stlib_scan", s="interpreted")
        db.run(ins, f=bad_f, p="stlib/two", path="data/stlib/bad.png", k="stlib_bad", s="failed")
        db.run(ins, f=up_f, p=f"upload/{up_f}", path="mine.png", k="mine~cccccccc", s="interpreted")
        page = """insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image)
                  values (:f, 0, '1', 1000, 500, 'px', :i, 1000, 500, cast('{1,0,0,0,1,0}' as double precision[]))"""
        db.run(page, f=lib_f, i=f"renders/{lib_f}/p0.png")
        db.run(page, f=up_f, i=f"renders/{up_f}/p0.png")
        ent = """insert into jstore.entity (project_id, drawing_key, kind, number, geom)
                 values (:p, :k, :kind, :n, ST_MakeEnvelope(0, 0, 1, 1)) returning entity_id"""
        e = db.one(ent, p="stlib/one", k="stlib_scan", kind="room", n="1")["entity_id"]
        db.run(ent, p="stlib/one", k="stlib_scan", kind="door", n="T1")
        db.run("""insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role)
                  values (:e, :f, 0, 'region', '1', 'outline')""", e=e, f=lib_f)
    try:
        yield SimpleNamespace(lib=lib_f, upload=up_f, bad=bad_f, db=jdb.DB)
    finally:
        with jdb.DB() as db:
            wipe(db)


def test_jstore_source_reads_the_store_and_never_an_upload(scratch_store, tmp_path):
    s = scratch_store
    src = store_library.JStoreSource()
    ids = src.file_ids()
    assert s.lib in ids and s.bad in ids and s.upload not in ids      # dataset files permanent, uploads not
    rows = {r["key"]: r for r in src.rows()}
    assert "mine~cccccccc" not in rows and not any(r["project"].startswith("upload/") for r in rows.values())
    r = rows["stlib_scan"]
    assert r["project"] == "stlib/one" and r["usable"] and r["has_image"] and not r["has_cad"]
    assert r["counts"] == {"room": 1, "door": 1} and r["storeys"] == []
    assert [(f["file_id"], f["format"], f["path"]) for f in r["files"]] == [(s.lib, "png", "data/stlib/scan.png")]
    assert rows["stlib_bad"]["usable"] is False
    listed = {e.key for e in build_entries(list(rows.values()), set(), [])}
    assert "stlib_scan" in listed and "stlib_bad" not in listed
    # the listing signature ignores uploads; the library's own drawings change it
    sig = src.signature()
    with s.db() as db:
        db.run("""insert into jstore.entity (project_id, drawing_key, kind, geom)
                  values (:p, 'mine~cccccccc', 'room', ST_MakeEnvelope(0, 0, 1, 1))""", p=f"upload/{s.upload}")
        db.run("update jstore.page set image = null where file_id = :f", f=s.upload)
    assert src.signature() == sig
    doc_sig = src.drawing_signature("stlib_scan")
    assert doc_sig and src.drawing_signature("no_such_drawing") is None
    with s.db() as db:
        db.run("""insert into jstore.entity (project_id, drawing_key, kind, geom)
                  values ('stlib/one', 'stlib_scan', 'window', ST_MakeEnvelope(0, 0, 1, 1))""")
    assert src.signature() != sig and src.drawing_signature("stlib_scan") != doc_sig
    # an upload's drawing is never built, even when asked for by its key
    assert src.build("mine~cccccccc", None) is None and src.plan_svg("mine~cccccccc", None, None) is None
    # a library drawing is built into a contract document by the library over this reader
    lib = library(tmp_path, src, store_map=False)
    d = lib.get(make_id("stlib_scan"))
    assert d is not None and Drawing.model_validate(d.public_drawing()).id == make_id("stlib_scan")
    assert d.drawing["store"]["file_ids"] == [s.lib] and d.drawing["evidence_method"].get("room") == "vision"
    assert lib.get(make_id("mine~cccccccc")) is None


def test_plan_svg_gets_the_size_of_its_view_box():
    from aura.store_library import sized_svg

    plain = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 707.10" font-family="Arial"><path stroke-width="2"/></svg>'
    out = sized_svg(plain)
    assert out.startswith('<svg width="1000" height="707.1" xmlns=') and out.endswith('<path stroke-width="2"/></svg>')
    assert sized_svg(out) == out                                    # already sized: untouched
    assert sized_svg('<svg viewBox="0 0 0 5"></svg>') == '<svg viewBox="0 0 0 5"></svg>'
    assert sized_svg("<svg></svg>") == "<svg></svg>" and sized_svg("no svg") == "no svg"
