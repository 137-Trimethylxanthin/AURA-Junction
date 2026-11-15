"""One page of a PDF of several is what a drawing shows: its counts and its model are that page's.  A CAD drawing
without a render fills its page with the main body of the drawing, not with a block of entities far away."""
from __future__ import annotations

import pytest

from jstore.db import DB, arr
from jstore.service import aura_model as am

F_PDF, F_DWG = "d" * 15 + "1", "d" * 15 + "2"
SHEETS, SPECK = "sheets of a set", "plan with a far block"


@pytest.fixture(scope="module")
def made(seeded: dict) -> dict:
    from PIL import Image
    Image.new("RGB", (600, 400), "white").save(seeded["data"] / "renders" / "sheet.png")
    with DB() as db:
        db.run("delete from jstore.project where project_id = 'test/sheet'")
        db.run("insert into jstore.project (project_id, title, source) values ('test/sheet', 'sheet', 'test')")
        for fid, path, fmt, key in ((F_PDF, "uploads/seed/set.pdf", "pdf", SHEETS), (F_DWG, "uploads/seed/far.dwg", "dwg", SPECK)):
            db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status)
                      values (:f, 'test/sheet', :p, :fmt, 6, :k, 'interpreted')""", f=fid, p=path, fmt=fmt, k=key)
        page = """insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image)
                  values (:f, :n, :name, :w, :h, :u, :i, :iw, :ih, cast(:t as double precision[]))"""
        for n, name in enumerate(("1", "2", "4")):          # the PDF's page 3 was not kept
            db.run(page, f=F_PDF, n=n, name=name, w=300, h=200, u="pt", i="renders/sheet.png", iw=600, ih=400, t=arr([2, 0, 0, 0, 2, 0]))
        ent = """insert into jstore.entity (project_id, drawing_key, kind, label, geom)
                 values ('test/sheet', :k, :kind, :l, ST_MakeEnvelope(0, 0, 1, 1)) returning entity_id"""
        src = """insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role, bbox)
                 values (:e, :f, :p, 'pdf_text', null, 'label', cast(:b as double precision[]))"""
        # page index 1 holds most (it is the page shown): 3 rooms and 2 doors; index 0: 1 room; index 2: 2 rooms, 1 door
        for page_no, rooms, doors in ((0, 1, 0), (1, 3, 2), (2, 2, 1)):
            for kind, n in (("room", rooms), ("door", doors)):
                for i in range(n):
                    e = db.one(ent, k=SHEETS, kind=kind, l=f"{kind} {page_no}.{i}")["entity_id"]
                    db.run(src, e=e, f=F_PDF, p=page_no, b=arr([10 + 20 * i, 10, 25 + 20 * i, 20]))
        db.run(page, f=F_DWG, n=0, name="model", w=1000, h=1000, u="unit", i=None, iw=None, ih=None, t=None)
        db.run("insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, cast(:m as double precision[]), 'units')",
               f=F_DWG, m=arr([0.0254, 0, 0, 0, 0.0254, 0]))
        # the plan: 400 lines within 2000 x 1000 units around (25000, -22000); 30 entities (7 %) of a block at the origin
        db.run("""insert into jstore.cad_entity (file_id, handle, dxftype, layer, geom)
                  select :f, 'M' || g, 'LINE', 'A-WALL', ST_MakeLine(ST_MakePoint(24000 + (g % 20) * 100, -22500 + (g / 20) * 50),
                                                                    ST_MakePoint(24050 + (g % 20) * 100, -22480 + (g / 20) * 50))
                  from generate_series(0, 399) g""", f=F_DWG)
        db.run("""insert into jstore.cad_entity (file_id, handle, dxftype, layer, geom)
                  select :f, 'F' || g, 'LINE', 'TITLE', ST_MakeLine(ST_MakePoint(g * 10, g * 10), ST_MakePoint(g * 10 + 5, g * 10))
                  from generate_series(0, 29) g""", f=F_DWG)
    yield {}
    with DB() as db:
        db.run("delete from jstore.project where project_id = 'test/sheet'")


def test_counts_of_a_pdf_of_several_pages_are_those_of_the_page_shown(made: dict) -> None:
    with DB() as db:
        sheet, counts = am.page_counts(db, SHEETS)
        m = am.build(db, SHEETS)
        assert am.page_counts(db, SPECK) is None and am.page_counts(db, "test_a") is None     # not a PDF of several pages
    assert sheet == (2, 4)                                 # the page the PDF numbers 2, of the 4 it has
    assert counts == {"room": 3, "door": 2}
    # the model that answers are counted from holds the same things, and the document says which page it is
    assert m.base.page_no == 1 and m.sheet == (2, 4)
    assert len(m.rooms) == 3 and sum(1 for i in m.items if i["kind"] == "door") == 2
    doc = am.drawing_doc(m)
    assert (doc["sheet_page"], doc["sheet_pages"], len(doc["rooms"])) == (2, 4, 3)


def test_off_page_is_only_another_page_of_the_file_shown() -> None:
    base = am.Base(file_id="f", page_no=1, format="pdf", name="2", image="x.png", w=10, h=10, to_image=(1, 0, 0, 0, 1, 0),
                   frame=None, frame_method=None, registered=False)
    src = lambda f, p: dict(file_id=f, page_no=p)  # noqa: E731
    assert am.off_page([src("f", 0)], base) and am.off_page([src("f", 0), src("g", 1)], base)
    assert not am.off_page([src("f", 0), src("f", 1)], base)
    assert not am.off_page([src("g", 0)], base) and not am.off_page([], base)   # another file of the drawing, or no source


def test_a_cad_page_drawn_from_geometry_fills_with_the_plan_not_a_far_block(made: dict) -> None:
    with DB() as db:
        base = am.pick_base(db, SPECK)
    assert base is not None and base.image is None
    back = am.inv(base.to_image)
    (x0, y1), (x1, y0) = am.apply(back, 0, 0), am.apply(back, base.w, base.h)
    assert 23800 < x0 < 24000 and 26000 <= x1 < 26300 and -22700 < y0 < -22500 and -21550 < y1 < -21300
    assert (x1 - x0) < 2500                               # the plan fills the page (with the far block: 26 000 units)
