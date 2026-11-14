"""The HTTP service end to end with scripted models: auth, upload -> ingest -> ask, runs, tools, verification."""
from __future__ import annotations

import asyncio
import json
import re

import ezdxf
import pytest

from jstore.db import DB
from jstore.service import aura

from conftest import A1, A2, B1, TOKEN, call, final, script, tool_events

DOORS = "select count(*) as n, array_agg(entity_id) as ids from q.doors where not is_duplicate"


def sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def ask(client, model_files, question="How many doors?"):
    r = await client.post("/ask", json=dict(file_ids=model_files, questions=[question]))
    assert r.status_code == 200, r.text
    run = (await client.get(f"/runs/{r.json()['run_id']}")).json()
    return r.json()["answers"][0], run


# ---------------------------------------------------------------------------------------------- auth
async def test_every_route_needs_the_token(make_client, seeded) -> None:
    client = make_client(auth=False)
    fill = dict(file_id=A1, page_no="0", key="test_a", run_id="0" * 16, name="1.png",
                drawing_id="test_a", conv_id="0" * 16, answer_id="0" * 16)
    checked = 0
    # the AURA router is mounted as one included router: its routes are listed on the router itself
    for route in [r for r in client.app.routes if hasattr(r, "path")] + list(aura.router.routes):
        path = re.sub(r"\{(\w+)(:path)?\}", lambda m: fill[m[1]], route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = await client.request(method, path)
            if path in ("/", "/health"):
                assert r.status_code == 200
                assert path == "/" or r.json() == {"status": "ok"}      # liveness only, no configuration
                continue
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a token"
            assert (await client.request(method, path, headers={"Authorization": "Bearer wrong"})).status_code == 401
            assert (await client.request(method, path, params={"token": "wrong"})).status_code == 401
            checked += 1
    assert checked == 16 + 15       # the store's own routes + /aura
    assert (await client.get("/drawings", params={"token": TOKEN})).status_code == 200       # for <img> and EventSource
    assert (await client.get("/drawings", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
    full = (await client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})).json()
    assert full["database"]["ok"] and full["auth"] is True and TOKEN not in json.dumps(full)


# ---------------------------------------------------------------------------------------------- upload -> ingest -> ask
def tiny_dxf(path) -> bytes:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True, dxfattribs={"layer": "ROOM_OUTLINE"})
    for x in (1, 2, 3):
        msp.add_line((x, 0), (x, 0.9), dxfattribs={"layer": "DOOR"})
    msp.add_text("Kitchen", dxfattribs={"layer": "ROOM_TEXT"}).set_placement((1, 1))
    doc.saveas(path)
    return path.read_bytes()


async def test_upload_ingest_ask_with_the_demo_model(make_client, tmp_path) -> None:
    client = make_client("demo")
    data = tiny_dxf(tmp_path / "plan.dxf")
    r = await client.post("/files", files={"file": ("My Plan, v2.dxf", data)})
    assert r.status_code == 200, r.text
    up = r.json()
    fid = up["file_id"]
    assert up["ext"] == "dxf" and up["size"] == len(data) and up["status"] == "registered" and up["ingesting"]
    assert up["project"] == f"upload/{fid}" and "," not in up["drawing"]
    try:
        # asked at once: the run waits for the worker process to finish parsing
        r = await client.post("/ask", json=dict(file_ids=[fid], questions=["How many files?", "And again?"]))
        assert r.status_code == 200, r.text
        out = r.json()
        assert [a["n"] for a in out["answers"]] == [1, 2] and out["usage"]["sql_calls"] == 1
        a = out["answers"][0]
        assert a["answer"] == 1 and a["warnings"] == [] and a["sql"].startswith("select")
        assert a["evidence"][0]["kind"] == "cad" and a["evidence"][0]["file_id"] == fid and len(a["evidence"][0]["bbox"]) == 4
        run = (await client.get(f"/runs/{out['run_id']}")).json()
        types = [e["type"] for e in run["events"]]
        assert types[0] == "run_started" and types[-1] == "run_finished" and "ingest" in types and "answers" in types
        sql = tool_events(run, "sql")
        assert len(sql) == 1 and sql[0]["rows"] == 1 and sql[0]["exit_code"] == 0
        info = (await client.get(f"/files/{fid}")).json()
        assert info["status"] in ("parsed", "interpreted") and info["counts"]["cad_entity"] == 5 and not info["ingesting"]
        assert (await client.get(f"/files/{fid}/raw")).content == data
        again = (await client.post("/files", files={"file": ("other name.dxf", data)})).json()     # same bytes, same file
        assert again["file_id"] == fid and not again["ingesting"]
        # the stream replays for a late subscriber
        ev = sse_events((await client.get(f"/runs/{out['run_id']}/events")).text)
        assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1)) and ev[-1]["type"] == "run_finished"
    finally:
        with DB() as db:
            db.run("delete from jstore.project where project_id = :p", p=up["project"])


async def test_upload_rejects_bad_files(make_client) -> None:
    client = make_client()
    assert (await client.post("/files", files={"file": ("x.exe", b"MZ")})).status_code == 422
    assert (await client.post("/files", files={"file": ("x.dxf", b"")})).status_code == 422
    assert (await client.post("/files", files={"file": ("x.dxf", b"0" * (3 << 20))})).status_code == 413
    assert (await client.get("/files/ffffffffffffffff")).status_code == 404
    assert (await client.get("/files/not-an-id")).status_code == 404


async def test_register_only_inside_the_dataset_folder(make_client, settings, tmp_path) -> None:
    client = make_client()
    tiny_dxf(settings.dataset_dir / "site7_bldg8_floor9.dxf")
    (tmp_path / "outside.dxf").write_text("0\nEOF\n")
    (settings.dataset_dir / "link.dxf").symlink_to(tmp_path / "outside.dxf")
    assert (await client.post("/files/register", json={"path": "../outside.dxf"})).status_code == 403
    assert (await client.post("/files/register", json={"path": "link.dxf"})).status_code == 403
    assert (await client.post("/files/register", json={"path": "missing.dxf"})).status_code == 404
    r = await client.post("/files/register", json={"path": "site7_bldg8_floor9.dxf"})
    assert r.status_code == 200 and r.json()["drawing"] == "site7_bldg8_floor9"
    fid = r.json()["file_id"]
    try:
        for _ in range(300):
            info = (await client.get(f"/files/{fid}")).json()
            if not info["ingesting"] and info["status"] != "registered":
                break
            await asyncio.sleep(0.1)
        assert info["status"] in ("parsed", "interpreted"), info
    finally:
        with DB() as db:
            db.run("delete from jstore.project where project_id = :p", p=r.json()["project"])


# ---------------------------------------------------------------------------------------------- the agent's tools
async def test_answer_gets_evidence_sources_and_agreement(make_client, seeded) -> None:
    ids = seeded["doors"]
    client = make_client(script(call("sql", query=DOORS), final(answer=2, sql=DOORS, entity_ids=ids)))
    a, run = await ask(client, [A1])
    assert run["drawings"] == ["test_a"] and a["answer"] == 2 and a["warnings"] == [] and a["confidence"] == 0.9
    assert a["evidence_total"] == 2 and [e["entity_id"] for e in a["evidence"]] == ids
    e = a["evidence"][0]
    assert e["kind"] == "cad" and e["handle"] == "D1" and e["box_pct"] == [10, 78, 11, 80] and e["label"] == "door T1"
    assert e["image_url"] == f"/files/{A1}/pages/0/image" and e["bbox_px"] == [100, 390, 110, 400]
    assert (await client.get(f"/runs/{run['id']}/artifacts/{e['image']}")).headers["content-type"] == "image/png"
    assert tool_events(run, "sql")[0]["rows"] == 1 and run["usage"]["requests"] == 2

    area = "select area_m2, agreement, entity_id from q.rooms where number = '1.01'"
    client = make_client(script(call("sql", query=area), final(answer=12.6, unit="m2", sql=area, entity_ids=[seeded["room"]])))
    a, _ = await ask(client, [A1], "Area of room 1.01?")
    assert a["warnings"] == [] and a["agreement"] == {"net_area_m2": {"agree": 1}}
    assert {(s["format"], s["value"], s["method"]) for s in a["sources"]} == {("pdf", 12.6, "printed"), ("dxf", 12.55, "measured_outline")}
    assert {e["file_id"] for e in a["evidence"]} == {A1, A2}      # the same room in both formats


async def test_unverified_numbers_are_flagged(make_client, seeded) -> None:
    # 3 is in no sql result; the entity id belongs to another drawing; the reference points outside the run
    client = make_client(script(call("sql", query=DOORS), final(
        answer=3, sql="select 3", entity_ids=[seeded["other"]], refs=[dict(file_id=B1, ref_table="cad_entity", ref_key="S1")])))
    a, _ = await ask(client, [A1])
    assert a["confidence"] == 0.4 and len(a["warnings"]) == 4 and a["evidence"] == [] and a["sources"] == []
    assert "not verified: 3" in a["warnings"][0] and "not executed" in a["warnings"][1]
    # no sql at all: nothing backs the number.  A sum of a column and a row count do.
    a, _ = await ask(make_client(script(final(answer=2))), [A1])
    assert a["warnings"] and a["confidence"] == 0.4
    rows = "select entity_id, 1.5 as w from q.doors"
    a, _ = await ask(make_client(script(call("sql", query=rows), final(answer=3.0, unit="m"))), [A1])
    assert a["warnings"] == [] and a["answer"] == 3
    a, _ = await ask(make_client(script(call("sql", query=rows), final(answer="There are 2 doors"))), [A1])
    assert a["warnings"] == []


async def test_sql_tool_is_scoped_to_the_run(make_client, seeded) -> None:
    probe = "select (select count(*) from q.cad_entities where layer = 'SECRET') as secret, (select count(*) from q.files) as files"
    client = make_client(script(call("sql", 1, query=probe), call("sql", 2, query="select set_config('jstore.files', 'x', false)"),
                                call("sql", 3, query="select * from q.nothing"), final(answer=0)))
    _, run = await ask(client, [A1])
    ev = tool_events(run, "sql")
    assert ev[0]["output"].splitlines()[-1] == "[0, 2]"
    assert ev[1]["output"].startswith("Refused") and ev[1]["exit_code"] == 1 and ev[2]["output"].startswith("Error")
    _, run = await ask(make_client(script(call("sql", query=probe), final(answer=7))), [B1])
    assert tool_events(run, "sql")[0]["output"].splitlines()[-1] == "[7, 1]"
    assert (await client.post("/ask", json=dict(file_ids=["f" * 16], questions=["x"]))).status_code == 404


async def test_view_image_shows_renders_and_crops(make_client, seeded) -> None:
    client = make_client(script(call("view_image", 1, file_id=A1, page_no=0, box=None),
                                call("view_image", 2, file_id=A1, page_no=0, box=[10, 20, 60, 80]),
                                call("view_image", 3, file_id=B1, page_no=0, box=None),
                                call("view_image", 4, file_id=A1, page_no=5, box=None), final(answer=None)))
    _, run = await ask(client, [A1])
    ev = tool_events(run, "view_image")
    assert [e["exit_code"] for e in ev] == [0, 0, 1, 1]
    assert (ev[0]["width"], ev[0]["height"], ev[1]["shown_width"], ev[1]["shown_height"]) == (1000, 500, 500, 300)
    assert "no such file in this run" in ev[2]["output"] and "no page 5" in ev[3]["output"]
    img = await client.get(ev[1]["view"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content[:4] == b"\x89PNG"
    _, run = await ask(make_client(script(call("view_image", file_id=B1, page_no=0, box=None), final(answer=None))), [B1])
    assert "no rendered image" in tool_events(run, "view_image")[0]["output"]


async def test_shell_is_the_last_resort(make_client, seeded, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "sk-secret-do-not-leak")
    client = make_client(script(call("shell", 1, command="ls", timeout_s=None), call("sql", 2, query="select 1"),
                                call("shell", 3, command="ls; env; echo out > note.txt", timeout_s=None), final(answer=None)))
    _, run = await ask(client, [A1])
    first, second = tool_events(run, "shell")
    assert first["ended"] == "refused" and "query the store first" in first["output"]
    assert second["exit_code"] == 0 and "drawing.dxf" in second["output"] and "sk-secret" not in second["output"]
    assert "JSTORE_DSN" not in second["output"]
    arts = (await client.get(f"/runs/{run['id']}/artifacts")).json()["artifacts"]
    assert [a["name"] for a in arts] == ["note.txt"]
    assert (await client.get(f"/runs/{run['id']}/artifacts/note.txt")).text == "out\n"
    assert (await client.get(f"/runs/{run['id']}/artifacts/../run.json")).status_code in (403, 404)
    off = make_client(script(call("sql", 1, query="select 1"), call("shell", 2, command="ls", timeout_s=None), final(answer=None)), shell=False)
    _, run = await ask(off, [A1])
    assert "switched off" in tool_events(run, "shell")[0]["output"]


async def test_a_failing_model_ends_the_run_as_an_error(make_client, seeded) -> None:
    client = make_client(script(call("sql", query="select 1")))      # the script runs out: the model call raises
    r = await client.post("/ask", json=dict(file_ids=[A1], questions=["x"]))
    assert r.status_code == 502 and r.json()["status"] == "error" and r.json()["error"]


# ---------------------------------------------------------------------------------------------- read endpoints
async def test_drawings_entities_pages(make_client, seeded) -> None:
    client = make_client()
    ls = (await client.get("/drawings", params={"project": "test/seed"})).json()
    assert ls["total"] == 2 and [d["drawing"] for d in ls["drawings"]] == ["test_a", "test_b"]
    assert [f["format"] for f in ls["drawings"][0]["files"]] == ["dxf", "pdf"] and ls["drawings"][0]["status"] == "interpreted"
    assert (await client.get("/drawings", params={"q": "test_b"})).json()["total"] == 1
    assert (await client.get("/drawings", params={"project": "test"})).json()["total"] == 2
    d = (await client.get("/drawings/test_a")).json()
    page = d["files"][0]["pages"][0]
    assert page["image"] == f"/files/{A1}/pages/0/image" and (page["image_w"], page["image_h"]) == (1000, 500)
    assert {k["kind"]: k["n"] for k in d["entities"]} == {"door": 2, "room": 1} and d["issues"][0]["kind"] == "missing_in_source"
    assert (await client.get("/drawings/nope")).status_code == 404
    png = await client.get(page["image"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert (await client.get(f"/files/{B1}/pages/0/image")).status_code == 404        # no render
    ents = (await client.get("/drawings/test_a/entities", params={"kind": "room"})).json()
    area = ents["entities"][0]["values"]["net_area_m2"]
    assert ents["total"] == 1 and area["value"] == 12.6 and area["agreement"] == "agree" and area["n_sources"] == 2
    assert {(s["format"], s["value"]) for s in area["sources"]} == {("pdf", 12.6), ("dxf", 12.55)}
    assert (await client.get("/drawings/test_a/entities")).json()["total"] == 3
    ev = (await client.get("/evidence", params={"entity_ids": f"{seeded['room']},{seeded['doors'][0]}"})).json()
    assert [e["kind"] for e in ev["entities"]] == ["room", "door"] and ev["missing"] == []
    assert (await client.get("/evidence", params={"entity_ids": "1;drop"})).status_code == 422
