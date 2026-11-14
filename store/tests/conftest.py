"""Fixtures: a seeded project in the scratch database and an app wired to a scripted model (no API key needed).

    JSTORE_DSN=postgresql://postgres:jstore@127.0.0.1:54329/dev_api  python -m pytest tests -q
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

STORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STORE))
os.environ.setdefault("JSTORE_DSN", "postgresql://postgres:jstore@127.0.0.1:54329/dev_api")
os.environ["JSTORE_DATA_DIR"] = tempfile.mkdtemp(prefix="jstore-test-")   # before anything calls store_dir()
for k in ("JSTORE_API_TOKEN", "GEMINI_API_KEY"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from agents.testing import ScriptedModel, assistant_message, function_call  # noqa: E402

from jstore.db import DB, DSN, apply_schema, arr, js  # noqa: E402
from jstore.service.app import create_app  # noqa: E402
from jstore.service.config import Settings  # noqa: E402

assert not DSN.rstrip("/").endswith("/postgres"), "tests need a scratch database, never the one named postgres"

A1, A2, B1 = "a" * 15 + "1", "a" * 15 + "2", "b" * 15 + "1"   # test_a as DXF and PDF, test_b as DXF
TOKEN = "test-token-123"


def wipe(db: DB) -> None:
    """Only what the tests made: the seed project (uploads are removed by the test that made them)."""
    db.run("delete from jstore.issue where project_id like 'test/%'")
    db.run("delete from jstore.project where project_id like 'test/%'")


@pytest.fixture(scope="session")
def seeded() -> dict:
    """Two drawings: test_a (a DXF and a PDF of the same plan, one room, two doors) and test_b (another DXF)."""
    from PIL import Image
    data = Path(os.environ["JSTORE_DATA_DIR"])
    (data / "renders").mkdir(parents=True, exist_ok=True)
    (data / "uploads" / "seed").mkdir(parents=True, exist_ok=True)
    (data / "uploads" / "seed" / "a.dxf").write_text("0\nEOF\n")
    Image.new("RGB", (1000, 500), "white").save(data / "renders" / "a1.png")
    Image.new("RGB", (1000, 500), "white").save(data / "renders" / "a2.png")
    apply_schema()
    with DB() as db:
        wipe(db)
        db.run("insert into jstore.project (project_id, title, source) values ('test/seed', 'seed', 'test')")
        for fid, path, fmt, key in ((A1, "uploads/seed/a.dxf", "dxf", "test_a"), (A2, "uploads/seed/a.pdf", "pdf", "test_a"),
                                    (B1, "uploads/seed/b.dxf", "dxf", "test_b")):
            db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status)
                      values (:f, 'test/seed', :p, :fmt, 6, :k, 'interpreted')""", f=fid, p=path, fmt=fmt, k=key)
        # A1: 100 x 50 drawing units, y up, rendered at 10 px per unit;  A2: 500 x 250 pt rendered at 2 px per pt
        page = """insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image)
                  values (:f, 0, :n, :w, :h, :u, :i, :iw, :ih, cast(:t as double precision[]))"""
        db.run(page, f=A1, n="model", w=100, h=50, u="unit", i="renders/a1.png", iw=1000, ih=500, t=arr([10, 0, 0, 0, -10, 500]))
        db.run(page, f=A2, n="1", w=500, h=250, u="pt", i="renders/a2.png", iw=1000, ih=500, t=arr([2, 0, 0, 0, 2, 0]))
        db.run(page, f=B1, n="model", w=10, h=10, u="unit", i=None, iw=None, ih=None, t=None)
        box = "ST_MakeEnvelope({}, {}, {}, {})"
        cad = ("insert into jstore.cad_entity (file_id, handle, dxftype, layer, closed, geom, text) values "
               "(:f, :h, :t, :l, true, {}, :x)")
        db.run(cad.format(box.format(10, 10, 30, 20)), f=A1, h="R1", t="LWPOLYLINE", l="ROOM", x=None)
        db.run(cad.format(box.format(10, 10, 11, 11)), f=A1, h="D1", t="INSERT", l="DOOR", x=None)
        db.run(cad.format(box.format(29, 15, 30, 16)), f=A1, h="D2", t="INSERT", l="DOOR", x=None)
        db.run(cad.format("ST_MakePoint(20, 15)"), f=A1, h="T1", t="TEXT", l="ROOM_TEXT", x="Kitchen 12.6 m2")
        db.run("""insert into jstore.cad_entity (file_id, handle, dxftype, layer, geom)
                  select :f, 'P' || g, 'POINT', 'BULK', ST_MakePoint(g, g * 0.5) from generate_series(1, 250) g""", f=A1)
        db.run("""insert into jstore.cad_entity (file_id, handle, dxftype, layer, geom)
                  select :f, 'S' || g, 'LINE', 'SECRET', ST_MakePoint(g, g) from generate_series(1, 7) g""", f=B1)
        text_id = db.one("""insert into jstore.pdf_text (file_id, page_no, seq, text, x0, y0, x1, y1)
                            values (:f, 0, 1, '12.6 m2', 100, 50, 200, 100) returning id""", f=A2)["id"]
        ent = """insert into jstore.entity (project_id, drawing_key, kind, number, label, label_norm, zone, host, geom, attrs)
                 values ('test/seed', :k, :kind, :n, :l, :ln, :z, :h, ST_MakeEnvelope(0, 0, 1, 1), cast(:a as jsonb))
                 returning entity_id"""
        room = db.one(ent, k="test_a", kind="room", n="1.01", l="Küche", ln="kitchen", z="A", h=None, a=js({"indoor": True}))["entity_id"]
        door1 = db.one(ent, k="test_a", kind="door", n="T1", l=None, ln=None, z="A", h="1.01", a="{}")["entity_id"]
        door2 = db.one(ent, k="test_a", kind="door", n="T2", l=None, ln=None, z="A", h="1.01", a="{}")["entity_id"]
        other = db.one(ent, k="test_b", kind="room", n="9.99", l="Secret", ln="office", z=None, h=None, a="{}")["entity_id"]
        src = """insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role, bbox)
                 values (:e, :f, 0, :t, :k, :r, cast(:b as double precision[])) returning id"""
        s1 = db.one(src, e=room, f=A1, t="cad_entity", k="R1", r="outline", b=None)["id"]       # box from Layer 1
        s2 = db.one(src, e=room, f=A2, t="pdf_text", k=str(text_id), r="area_text", b=arr([100, 50, 200, 100]))["id"]
        db.run(src, e=door1, f=A1, t="cad_entity", k="D1", r="symbol", b=None)
        db.run(src, e=door2, f=A1, t="cad_entity", k="D2", r="symbol", b=None)
        claim = """insert into jstore.claim (entity_id, property, value_num, unit, file_id, method, source_id, confidence, rank)
                   values (:e, :p, :v, :u, :f, :m, :s, 0.9, :r)"""
        db.run(claim, e=room, p="net_area_m2", v=12.6, u="m2", f=A2, m="printed", s=s2, r="preferred")
        db.run(claim, e=room, p="net_area_m2", v=12.55, u="m2", f=A1, m="measured_outline", s=s1, r="normal")
        db.run(claim, e=other, p="net_area_m2", v=77.7, u="m2", f=B1, m="measured_outline", s=None, r="normal")
        db.run("""insert into jstore.issue (project_id, drawing_key, entity_id, property, kind, detail)
                  values ('test/seed', 'test_a', :e, 'width_m', 'missing_in_source', 'no width in the PDF')""", e=door1)
    yield dict(room=room, doors=[door1, door2], other=other, text_id=text_id, data=data)
    with DB() as db:
        wipe(db)


@pytest.fixture
def settings(seeded: dict, tmp_path: Path) -> Settings:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    return dataclasses.replace(Settings(), model="scripted", data_dir=seeded["data"], dataset_dir=dataset, max_turns=8,
                               shell_timeout=20, max_upload_mb=2, api_token=TOKEN, apply_schema=False)


@pytest.fixture
def make_client(settings: Settings) -> Callable[..., httpx.AsyncClient]:
    """make_client(model, **settings overrides) -> an authorised HTTP client talking to a fresh app in-process.
    model: a ScriptedModel, or "demo" for the service's own demo model."""
    apps = []

    def make(model: Any = "demo", auth: bool = True, **overrides: Any) -> httpx.AsyncClient:
        s = dataclasses.replace(settings, **overrides)
        if model == "demo":
            s, model = dataclasses.replace(s, model="demo"), None
        app = create_app(s, model=model)
        apps.append(app)
        headers = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120, headers=headers)
        c.app = app
        return c
    yield make
    for app in apps:
        app.state.ingestor.shutdown()


def final(**fields: Any) -> Any:
    """The final structured output as the model would send it."""
    a = dict(n=1, answer=2, unit="count", method="counted", sql=None, entity_ids=[], refs=[], check="none", confidence=0.9)
    a.update(fields)
    return assistant_message(json.dumps(dict(answers=[a])))


def script(*steps: Any) -> ScriptedModel:
    return ScriptedModel([s if isinstance(s, list) else [s] for s in steps])


def call(tool: str, n: int = 1, **args: Any) -> Any:
    return function_call(tool, args, call_id=f"call_{n}")


def tool_events(run: dict, tool: str) -> list[dict]:
    return [e for e in run["events"] if e["type"] == "tool_finished" and e["tool"] == tool]
