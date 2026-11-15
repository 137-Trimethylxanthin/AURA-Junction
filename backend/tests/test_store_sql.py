"""The guarded sql tool (aura.store_sql) and its place in the tool steps, with a fake store reader."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from aura import steps, store_sql, tools
from aura.gemini.client import Meter
from tests.conftest import FRONTEND_DRAWING
from tests.test_steps import FakeClient, svc_for, validated

STORE = Path(__file__).resolve().parents[2] / "store"


@pytest.fixture(autouse=True, scope="module")
def jstore_importable():
    """jstore's guards are used as they are. Until WP1 installs it with its driver, import it from
    the repo and stand in for the driver module (no connection is ever made here)."""
    added = []
    try:
        import jstore.service.store  # noqa: F401
    except ImportError:
        if str(STORE) not in sys.path:
            sys.path.insert(0, str(STORE))
            added.append(str(STORE))
        if "pg8000" not in sys.modules:
            pg = types.ModuleType("pg8000")
            pg.native = types.ModuleType("pg8000.native")  # type: ignore[attr-defined]
            pg.native.Connection = object  # type: ignore[attr-defined]
            sys.modules["pg8000"], sys.modules["pg8000.native"] = pg, pg.native  # type: ignore[attr-defined]
    yield
    for p in added:
        sys.path.remove(p)


class FakeReader:
    """Answers the wrapped statement and the method lookup; records what ran."""

    def __init__(self, rows: list[dict], methods: list[str] | None = None, total: int | None = None):
        self.rows, self.methods, self.total = rows, methods or [], total
        self.ran: list[str] = []

    def _run(self, sql: str) -> list[dict]:
        self.ran.append(sql)
        if "from q.sources" in sql:
            return [{"method": m} for m in self.methods]
        if sql.startswith("select count(*) as n from ("):
            return [{"n": self.total}]
        return list(self.rows)

    def close(self) -> None:
        pass


@pytest.fixture(scope="module")
def d():
    return tools.load_file(FRONTEND_DRAWING)  # a bundled Swiss drawing: our solvers own its things


def upload_pdf(d):
    return SimpleNamespace(**{**d.__dict__, "source": "upload",
                              "drawing": {**d.drawing, "source_ext": ".pdf"}})


SCOPE = store_sql.StoreScope(("dk-1",), ("f-1",))


@pytest.mark.parametrize("query", [
    "drop table q.rooms",
    "create table x (a int)",
    "select 1; select 2",
    "select set_config('jstore.drawings', 'x', false)",
    "select * from jstore.entity",
    "update q.texts set text = 'x'",
])
def test_ddl_writes_and_second_statements_are_refused_before_running(d, query):
    r = FakeReader([{"n": 1}])
    res = store_sql.run(query, d, SCOPE, reader=r)
    assert not res.ok and res.error.startswith("Refused") and r.ran == []


@pytest.mark.parametrize("query", [
    "select count(*) as n from q.doors",
    'select count(*) from "q"."Doors"',
    "with x as (select * from q.doors where width_m > 1) select count(*) from x",
    "select * from q.texts where file_id in (select file_id from q . rooms)",
    "select count(*) from q.items where kind = 'door'",
    "select count(distinct entity_id) from q.facts where kind = 'door'",
])
def test_owner_rule_refuses_store_rooms_and_items_on_bundled_drawings(d, query):
    r = FakeReader([{"n": 1}])
    res = store_sql.run(query, d, SCOPE, reader=r)
    assert not res.ok and "list_openings" in res.error and r.ran == []


def test_owner_rule_allows_layer1_and_store_owned_uploads(d):
    ok = store_sql.run("select text from q.texts where text ilike '%rooms%'", d, SCOPE,
                       reader=FakeReader([{"text": "rooms"}]))
    assert ok.ok and ok.views == ["texts"]
    pdf = store_sql.run("select count(*) as n from q.doors", upload_pdf(d), SCOPE,
                        reader=FakeReader([{"n": 4}]))
    assert pdf.ok


def test_owner_rule_is_the_database_scope(d):
    """Bundled / DXF / IFC drawings: the reader gets drawings=[] so every interpreted view is empty
    (the scope, not the query text, enforces it); store-owned uploads get both."""
    ref = {"store": {"drawing_key": "dk-1", "file_ids": ["f-1"]}}
    bundled = SimpleNamespace(**{**d.__dict__, "drawing": {**d.drawing, **ref}})
    assert store_sql.scope_for(bundled) == store_sql.StoreScope((), ("f-1",))
    assert store_sql.scope_for(upload_pdf(bundled)) == store_sql.StoreScope(("dk-1",), ("f-1",))
    no_files = SimpleNamespace(**{**d.__dict__, "drawing": {**d.drawing, "store": {"drawing_key": "dk-1"}}})
    assert store_sql.scope_for(no_files) is None


@pytest.mark.parametrize(("query", "finish_as"), [
    ("select 42 as n from q.files", "value:n"),
    ("select cast('7' as int) as n, 1 as k from q.files", "value:n"),
    ("select 3.5 as m2 from q.texts", "sum:m2"),
])
def test_numbers_typed_into_the_query_are_refused(d, query, finish_as):
    res = store_sql.run(query, d, SCOPE, reader=FakeReader([{"n": 42, "k": 1, "m2": 3.5}]))
    assert res.ok
    with pytest.raises(store_sql.SqlRefused, match="constant"):
        store_sql.take(res, finish_as)


def test_count_of_an_aggregate_reads_its_cell():
    res = store_sql.SqlResult(ok=True, query="select count(*) from q.windows", rows=[{"count": 26}], total=1,
                              columns=["count"], views=["windows"])
    t = store_sql.take(res, "count")
    assert t["value"] == 26 and t["rule"] == "the cell count = 26"


def test_a_result_that_reads_no_view_is_refused(d):
    res = store_sql.run("select 42 as n", d, SCOPE, reader=FakeReader([{"n": 42}]))
    with pytest.raises(store_sql.SqlRefused, match="no q"):
        store_sql.take(res, "count")


def test_take_arithmetic_is_done_by_code():
    rows = [{"layer": "A", "m2": 1.25, "entity_id": 1}, {"layer": "B", "m2": 2.5, "entity_id": 2},
            {"layer": "C", "m2": None, "entity_id": 3}]
    res = store_sql.SqlResult(ok=True, query="select * from q.texts", rows=rows, total=3, columns=list(rows[0]), views=["texts"])
    assert store_sql.take(res, "count")["value"] == 3
    s = store_sql.take(res, "sum:m2")
    assert s["value"] == 3.75 and s["unit"] == "m2" and "1 empty left out" in s["rule"]
    one = store_sql.SqlResult(ok=True, query="select count(*) as n, array_agg(entity_id) as ids from q.texts",
                              rows=[{"n": 7, "ids": [1, 2]}], total=1, columns=["n", "ids"], views=["texts"])
    assert store_sql.take(one, "cell")["value"] == 7.0
    assert store_sql.take(one, "value:n")["rule"] == "the cell n = 7"
    with pytest.raises(store_sql.SqlRefused):
        store_sql.take(res, "cell")  # three rows
    with pytest.raises(store_sql.SqlRefused):
        store_sql.take(res, "sum:nope")
    big = store_sql.SqlResult(ok=True, query="select * from q.texts", rows=rows, total=500, columns=list(rows[0]),
                              views=["texts"])
    with pytest.raises(store_sql.SqlRefused):
        store_sql.take(big, "sum:m2")  # more rows than were read: sum in SQL instead


def test_model_detected_entities_make_an_estimate(d):
    res = store_sql.run("select entity_id from q.texts", d, SCOPE,
                        reader=FakeReader([{"entity_id": 5}], methods=["gemini_tiled_detection"]))
    method, estimate, why = store_sql.method_of(res)
    assert (method, estimate) == ("vision", True) and "gemini_tiled_detection" in why
    exact = store_sql.run("select entity_id from q.texts", d, SCOPE,
                          reader=FakeReader([{"entity_id": 5}], methods=["dxf_text"]))
    assert store_sql.method_of(exact) == (store_sql.EXACT_METHOD, False, "read from the parsed files")
    assert store_sql.method_of(exact)[0] != "vision"


def test_boxes_for_maps_store_refs(d):
    box = {"id": "door-x", "x": 1, "y": 1, "w": 1, "h": 1, "subject": "door", "store_ref": {"entity_id": 42}}
    dd = SimpleNamespace(drawing={"evidence": {"door": [box]},
                                  "rooms": [{"room_id": "1.01", "store_ref": 43}]})
    boxes, rooms = store_sql.boxes_for(dd, [42, 43, 44])
    assert [b["id"] for b in boxes] == ["door-x"] and "store_ref" not in boxes[0] and rooms == ["1.01"]


@pytest.fixture
def store_on(monkeypatch):
    reader = FakeReader([])
    monkeypatch.setattr(store_sql, "available", lambda svc=None: True)
    monkeypatch.setattr(store_sql, "scope_for", lambda d: SCOPE)
    monkeypatch.setattr(store_sql, "_reader", lambda scope: reader)
    return reader


async def ask(d, turns, history=None):
    client = FakeClient(turns)
    stats = {"meter": Meter(), "route": "template"}
    built = await steps.answer_with_tools(svc_for(client), d, "how many fire ratings are printed", "en",
                                          SimpleNamespace(ip_hash=None, presenter=False), stats, history)
    return built, stats, client


async def test_sql_step_trace_shape_and_number_from_rows(d, store_on):
    store_on.rows = [{"n": 3, "ids": [11, 12, 13]}]
    store_on.methods = ["dxf_text"]
    query = "select count(*) as n, array_agg(entity_id) as ids from q.texts where kind = 'fire'"
    built, stats, client = await ask(d, [[("sql", {"query": query, "finish_as": "value:n",
                                                   "what": "printed fire ratings"})]])
    body = validated(built, stats)
    assert body["answer"] == {"kind": "note", "value": "Printed fire ratings", "number": 3}
    assert body["method"] == store_sql.EXACT_METHOD and stats["meter"].calls == 1
    assert [r["id"] for r in body["evidence"]["rows"]] == ["11", "12", "13"]
    by = {n.get("stage"): n for n in body["trace"] if n.get("stage")}
    res = by["resources"]["detail"]
    assert res["query"] == query and res["rows"] == 1 and res["columns"] == ["n", "ids"] and "ms" in res
    assert by["filter"]["detail"]["rule"] == "the cell n = 3"
    assert by["filter"]["detail"]["kept"] == ["11", "12", "13"]
    assert by["answer"]["detail"]["source"] == "sql"
    decl = client.sent[0]["tools"][0]["functionDeclarations"]
    assert "sql" in [x["name"] for x in decl]


async def test_sql_estimate_and_follow_up_reuses_ids(d, store_on):
    store_on.rows = [{"entity_id": 7, "label": "D1"}, {"entity_id": 8, "label": "D2"}]
    store_on.methods = ["gemini_tiled_detection"]
    turns = [[("sql", {"query": "select entity_id, label from q.texts", "finish_as": "count",
                       "what": "issues"})]]
    first, stats, _ = await ask(d, turns)
    body = validated(first, stats)
    assert body["method"] == "vision" and "estimate" in body["explanation"]
    store_on.ran.clear()
    store_on.rows = [{"n": 1}]
    store_on.methods = []
    turns = [[("sql", {"query": "select count(*) as n from q.texts where entity_id = any(:s1)",
                       "finish_as": "cell"})]]
    second, stats2, _ = await ask(d, turns, history=[{"message": "q", "response": body}])
    validated(second, stats2)
    assert "any(array[7,8]::bigint[])" in store_on.ran[0]


async def test_refused_query_is_reported_and_unanswerable_abstains_with_store_on(d, store_on):
    turns = [[("sql", {"query": "select count(*) as n from q.doors", "finish_as": "cell"})],
             [("finish", {"set": "none", "answer": "count"})]]
    built, stats, client = await ask(d, turns)
    body = validated(built, stats)
    assert body["answer"]["kind"] == "abstain" and body["method"] == "none"
    reply = next(p["functionResponse"]["response"] for c in client.sent[1]["contents"] for p in c["parts"]
                 if "functionResponse" in p)
    assert "list_openings" in reply["error"]
    assert any(n.get("stage") == "decision" for n in body["trace"])  # what was tried stays visible


async def test_store_off_hides_sql_and_never_imports_jstore(d, monkeypatch):
    monkeypatch.setattr(store_sql, "available", lambda svc=None: False)
    built, _, client = await ask(d, [[("rooms_containing", {"kind": "wc", "finish_as": "count"})]])
    assert built is not None
    assert "sql" not in [x["name"] for x in client.sent[0]["tools"][0]["functionDeclarations"]]


def test_store_off_never_imports_jstore():
    code = ("import os, sys; os.environ.pop('AURA_STORE_DSN', None); import aura.steps, aura.store_sql; "
            "assert not aura.store_sql.available(); assert 'jstore' not in sys.modules")
    out = subprocess.run([sys.executable, "-c", code], check=False, cwd=Path(__file__).resolve().parents[1],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


async def test_finish_on_an_sql_set_reads_its_cell(d, store_on):
    store_on.rows = [{"n": 30}]
    turns = [[("sql", {"query": "select count(*) as n from q.elements where ifc_class = 'IfcColumn'"})],
             [("finish", {"set": "s1", "answer": "count"})]]
    built, stats, _ = await ask(d, turns)
    body = validated(built, stats)
    assert body["answer"]["number"] == 30 and stats["meter"].calls == 2


async def test_no_scale_offers_only_sql_and_finish(d, store_on):
    store_on.rows = [{"n": 3}]
    nd = tools.LoadedDrawing(id=d.id, dir=None, drawing={**d.drawing, "toolbox": False}, engine=d.engine,
                             source="upload")
    built, _, client = await ask(nd, [[("sql", {"query": "select count(*) as n from q.texts", "finish_as": "cell"})]])
    assert built is not None
    assert {x["name"] for x in client.sent[0]["tools"][0]["functionDeclarations"]} == {"finish", "sql"}


def test_said_is_plain_in_every_language():
    """The explanation of an SQL-path answer names what was measured and how, never a column or table."""
    cell = {"kind": "number", "value": 63.57, "unit": "m", "rule": "the cell perimeter_m = 63.57"}
    en = store_sql.said(cell, ["rooms"], "vision", "found by raster_detection", "en")
    assert en == ("This is the perimeter of one room. It was measured on the room outline traced from the "
                  "scan, so it is an estimate.")
    cnt = {"kind": "count", "value": 4, "unit": None, "rule": "count = 4 rows"}
    assert store_sql.said(cnt, ["doors"], "vision", "", "en").startswith("Counted on this plan: 4 doors. It comes from symbols")
    agg = {"kind": "number", "value": 0, "unit": None, "rule": "the cell count = 0"}
    assert store_sql.said(agg, ["windows"], "cad_geometry", "", "en") == (
        "The windows of this plan were counted. It was taken from the geometry in the drawing file.")
    total = {"kind": "number", "value": 10.0, "unit": "m2", "rule": "sum of column area_m2 over 3 rows = 10"}
    assert store_sql.said(total, ["rooms"], "ocr", "", "en").startswith("The area of 3 rooms, added up. It was read")
    keys = set(store_sql.SAID["en"])
    for lang in ("de", "fr", "fi", "it"):
        assert set(store_sql.SAID[lang]) == keys, lang
        for t in (cell, cnt, agg, total):
            s = store_sql.said(t, ["rooms"], "vision", "", lang)
            assert s and "perimeter_m" not in s and "area_m2" not in s and "Rule" not in s and "SQL" not in s
            assert s != store_sql.said(t, ["rooms"], "vision", "", "en")
    assert store_sql.said(cell, ["rooms"], "vision", "", "xx") == store_sql.said(cell, ["rooms"], "vision", "", "en")


def test_ids_named_by_the_statement():
    assert store_sql.named_entity_ids("select perimeter_m from q.rooms where entity_id = 143313") == [143313]
    assert store_sql.named_entity_ids("select 1 from q.rooms where entity_id in (4, 5 ,4)") == [4, 5]
    assert store_sql.named_entity_ids("select count(*) from q.rooms where entity_id = other.entity_id") == []
