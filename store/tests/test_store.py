"""The sql tool (guards, scoping, row cap), evidence resolution and the schema document."""
from __future__ import annotations

import pytest

from jstore.db import DB
from jstore.service import prompt, store
from jstore.service.store import Reader, SqlRefused, check_sql, pct_box

from conftest import A1, A2, B1

REFUSED = [
    "", "delete from q.rooms", "update q.rooms set number = '1'", "insert into q.rooms values (1)", "drop view q.rooms",
    "set jstore.files = 'x'", "select 1; select 2", "select 1; set jstore.drawings = 'test_b'",
    "select set_config('jstore.drawings', 'test_b', false)", "select current_setting('jstore.files')",
    "SELECT SET_CONFIG('jstore.files', 'x', false)", "select * from pg_catalog.pg_class", "select * from pg_roles",
    "select pg_read_file('/etc/passwd')", "select * from information_schema.tables", "select * from jstore.cad_entity",
    "select * from jstore . entity", "select query_to_xml('select 1', true, true, '')", "with x as (select 1) delete from q.rooms",
    "select /* hidden */ 1", "explain select 1", "copy q.rooms to stdout", "select ';'",
]


@pytest.mark.parametrize("query", REFUSED)
def test_sql_guard_refuses(query: str) -> None:
    with pytest.raises(SqlRefused):
        check_sql(query)


@pytest.mark.parametrize("query", [
    "select 1", "SELECT count(*) FROM q.rooms;", "with a as (select 1 x) select * from a", "  (select 1) union (select 2)",
    "-- how many\nselect count(*) from q.doors", "select 'a'::text, area_m2::numeric(10,2) from q.rooms -- net"])
def test_sql_guard_accepts(query: str) -> None:
    assert check_sql(query)


def reader(*drawings_files, **kw) -> Reader:
    drawings, files = drawings_files
    return Reader(list(drawings), list(files), **kw)


def test_refusals_and_errors_come_back_as_text(seeded: dict) -> None:
    r = reader(["test_a"], [A1, A2])
    try:
        assert r.query("select 1; select 2")["text"].startswith("Refused: one statement")
        bad = r.query("select nope from q.rooms")
        assert not bad["ok"] and 'column "nope" does not exist' in bad["text"]
        assert r.query("select count(*) as n from q.rooms")["text"].splitlines()[-1] == "[1]"   # usable after an error
        assert len(r.log) == 1  # only executed statements count as results
    finally:
        r.close()


def test_scope_one_run_does_not_see_another_drawing(seeded: dict) -> None:
    a, b = reader(["test_a"], [A1, A2]), reader(["test_b"], [B1])
    try:
        assert "[0]" in a.query("select count(*) from q.cad_entities where layer = 'SECRET'")["text"]
        assert "[7]" in b.query("select count(*) from q.cad_entities where layer = 'SECRET'")["text"]
        assert "9.99" not in a.query("select number from q.rooms")["text"]
        assert "9.99" in b.query("select number from q.rooms")["text"]
        assert "[2]" in a.query("select count(*) from q.files")["text"]      # the same plan as DXF and PDF
        assert "77.7" not in a.query("select value from q.facts union all select value from q.sources")["text"]
        # the database enforces it too, without the text guard: no base tables, no way to change the scope
        for raw, why in (("select count(*) from jstore.cad_entity", "permission denied"),
                         ("select set_config('jstore.files', 'x', false)", "permission denied"),
                         ("select query_to_xml('select 1', true, true, '')", "permission denied"),
                         ("create table public.x (a int)", "read-only"), ("delete from jstore.claim", "permission denied")):
            with pytest.raises(Exception, match=why):
                a._run(raw)
    finally:
        a.close(), b.close()


def test_row_cap_and_rounding(seeded: dict) -> None:
    r = reader(["test_a"], [A1, A2], max_rows=200)
    try:
        out = r.query("select handle, ST_Y(geom) / 3 as y, geom from q.cad_entities where layer = 'BULK' order by ST_X(geom)")
        lines = out["text"].splitlines()
        assert out["rows"] == 250 and out["returned"] == 200
        assert lines[0].startswith("rows: 250 (showing the first 200")
        assert len(lines) == 2 + 200
        assert lines[2] == '["P1", 0.166667, "<geometry: use ST_Area / ST_Length / ST_AsText>"]'
        small = r.query("select count(*) as n, array_agg(handle) as handles from q.cad_entities where layer = 'DOOR'")
        assert small["text"].splitlines()[-1] == '[2, ["D1", "D2"]]'
        assert r.log[-1]["rows"] == 1 and 2.0 in r.log[-1]["numbers"]
    finally:
        r.close()


def test_pct_box_transforms_and_clamps() -> None:
    px, pct = pct_box([10, 10, 30, 20], [10, 0, 0, 0, -10, 500], 1000, 500)   # y up in the file, down in the image
    assert px == [100, 300, 300, 400] and pct == [10, 60, 30, 80]
    assert pct_box([-50, 0, 500, 10], [10, 0, 0, 0, -10, 500], 1000, 500)[1] == [0, 80, 100, 100]
    assert pct_box([0, 0, 1, 1], None, 1000, 500) == (None, None)


def test_evidence_resolves_boxes_per_source(seeded: dict) -> None:
    with DB() as db:
        ev = store.evidence(db, [seeded["room"], 987654321])
    assert ev["missing"] == [987654321]
    room = ev["entities"][0]
    by = {s["file_id"]: s for s in room["sources"]}
    assert room["kind"] == "room" and set(by) == {A1, A2}
    # DXF: no box stored with the entity, so it comes from the Layer 1 geometry of handle R1
    assert by[A1]["box"] == [10, 10, 30, 20] and by[A1]["box_pct"] == [10, 60, 30, 80]
    assert by[A1]["refs"][0] == dict(ref_table="cad_entity", ref_key="R1", role="outline", box=[10, 10, 30, 20],
                                     box_pct=[10, 60, 30, 80], layer="ROOM")
    assert by[A1]["image"] == f"/files/{A1}/pages/0/image" and by[A1]["page_no"] == 0
    # PDF: the stored box, 2 px per point on a 1000 x 500 image
    assert by[A2]["box_pct"] == [20, 20, 40, 40] and by[A2]["refs"][0]["text"] == "12.6 m2"
    with DB() as db:  # a run only resolves entities of its own drawings
        assert store.evidence(db, [seeded["room"]], ["test_b"])["missing"] == [seeded["room"]]


def test_scope_of_includes_every_format_of_the_drawing(seeded: dict) -> None:
    with DB() as db:
        sc = store.scope_of(db, [A1])
    assert sc["drawings"] == ["test_a"] and {f["file_id"] for f in sc["files"]} == {A1, A2}


def test_store_md_matches_the_database(seeded: dict) -> None:
    """Fails when a view documented in prompts/STORE.md is missing, or when its column list has drifted."""
    text = prompt.PATH.read_text(encoding="utf-8")
    with DB() as db:
        live = prompt.columns(db)
        assert prompt.documented(text) <= set(live), f"documented but missing: {prompt.documented(text) - set(live)}"
        assert text == prompt.render(db), "STORE.md is stale: run python -m jstore.service.prompt"
    assert {"rooms", "doors", "facts", "sources", "cad_entities", "texts", "elements"} <= prompt.documented(text)
    assert len(text.encode()) < 8000
