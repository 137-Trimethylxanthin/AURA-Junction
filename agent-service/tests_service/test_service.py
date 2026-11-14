"""End-to-end tests of the service with a scripted model: no network, no API key."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from agents.testing import function_call

from service.agent import instructions
from service.config import ROOT, sandbox_available

from .conftest import SECRET, final, happy_script, script, sse_events, upload

pytestmark = pytest.mark.asyncio
Q = {"questions": ["How many doors are there?"]}


def events_of(run: dict, type: str) -> list[dict]:
    return [e for e in run["events"] if e["type"] == type]


# ---- files ---------------------------------------------------------------------------------------------------
async def test_upload_and_raw(make_client):
    async with make_client() as c:
        fid = await upload(c, "plan.dxf", b"0\nSECTION\n")
        r = await c.get(f"/files/{fid}/raw")
        assert r.status_code == 200 and r.content == b"0\nSECTION\n"


@pytest.mark.parametrize("name,size,status", [
    ("evil.exe", 10, 422), ("noext", 10, 422), ("plan.dxf", 0, 422),
    ("plan.dxf", 1024 * 1024 + 1024, 413),      # passes the Content-Length guard, caught while streaming to disk
    ("plan.dxf", 3 * 1024 * 1024, 413),         # refused on Content-Length alone
    ("PLAN.IFC", 1000, 200)])
async def test_upload_limits(make_client, settings, name, size, status):
    async with make_client() as c:
        r = await c.post("/files", files={"file": (name, b"x" * size)})
        assert r.status_code == status, r.text
    if status != 200:  # nothing is left behind by a refused upload
        assert not any((settings.data_dir / "files").iterdir())


async def test_register_allowed_and_refused(make_client, tmp_path):
    async with make_client() as c:
        r = await c.post("/files/register", json={"path": "sub/Secret Name A-801.dxf"})
        assert r.status_code == 200 and r.json()["ext"] == "dxf"
        raw = await c.get(f"/files/{r.json()['file_id']}/raw")
        assert raw.status_code == 200 and raw.text == "0\nEOF\n"
        refused = {"../outside.dxf": 403, str(tmp_path / "outside.dxf"): 403, "/etc/passwd": 403,
                   "sub/../../outside.dxf": 403, "link.dxf": 403,           # symlink leaving the root
                   "nope.dxf": 404, "sub": 404, "notes.txt": 422, "": 422}
        for path, status in refused.items():
            r = await c.post("/files/register", json={"path": path})
            assert r.status_code == status, (path, r.status_code, r.text)
        assert (await c.post("/files/register", json={})).status_code == 422


# ---- ask -----------------------------------------------------------------------------------------------------
async def test_ask_runs_the_agent(make_client, settings, secrets_in_env):
    model = happy_script()
    async with make_client(model) as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], **Q})
        assert r.status_code == 200, r.text
        body = r.json()
        a = body["answers"][0]
        assert a["answer"] == 5 and isinstance(a["answer"], int)            # 5.0 normalised to an exact count
        assert a["evidence"] == [dict(kind="cad", handle="2F3", layer="DOORS", bbox=[0, 0, 1, 2])]  # nulls dropped
        assert body["usage"]["requests"] == 3 and body["seconds"] > 0
        run = (await c.get(f"/runs/{body['run_id']}")).json()
    model.assert_complete()
    assert run["status"] == "done" and run["answers"] == body["answers"]
    assert [e["seq"] for e in run["events"]] == list(range(1, len(run["events"]) + 1))
    # the model was given the shared prompt files, the neutral file name and never the original name
    call = model.first_call
    assert call.system_instructions == instructions() and "Counting conventions" in call.system_instructions
    assert [t.name for t in call.tools] == ["shell", "view_image"]
    assert "drawing.dxf" in str(call.input) and "Kaarna" not in str(call.input)
    assert run["files"][0]["original_name"] == "Villa Kaarna floor 2.dxf"
    # shell: ran in the workspace with a scrubbed environment
    sh = events_of(run, "tool_finished")[0]
    assert sh["tool"] == "shell" and sh["exit_code"] == 0 and "render.png" in sh["output"] and "drawing.dxf" in sh["output"]
    assert SECRET not in json.dumps(run) and "key=[]" in sh["output"]
    assert "API_KEY" not in sh["output"] and "TOKEN" not in sh["output"] and "BIFROST" not in sh["output"]
    assert f"PATH={settings.python_bin}:" in sh["output"]
    # view_image: the model received a downscaled image, the log only its description
    vi = events_of(run, "tool_finished")[1]
    assert (vi["width"], vi["height"], vi["shown_width"], vi["shown_height"]) == (3000, 1000, 1568, 523)
    out = [i for i in model.last_call.input if isinstance(i, dict) and i.get("call_id") == "call_2" and "output" in i][0]["output"]
    assert [p["type"] for p in out] == ["input_text", "input_image"] and out[1]["image_url"].startswith("data:image/png;base64,")
    assert "3000x1000" in out[0]["text"]


async def test_bad_input(make_client):
    async with make_client() as c:
        fid = await upload(c)
        for body in ({"file_ids": [fid], "questions": []}, {"file_ids": [], **Q}, {"file_ids": [fid], "questions": [" "]},
                     {"file_ids": fid, **Q}, {"questions": ["q"]}):
            for route in ("/ask", "/runs"):
                assert (await c.post(route, json=body)).status_code == 422
        assert (await c.post("/ask", content=b"{not json", headers={"content-type": "application/json"})).status_code == 422
        for bad in ("0" * 16, "nope", "../" * 3):
            assert (await c.post("/ask", json={"file_ids": [bad], **Q})).status_code == 404


async def test_agent_failures_end_as_error_runs(make_client):
    loop = [[function_call("shell", dict(command="true", timeout_s=None), call_id=f"c{i}")] for i in range(20)]
    cases = {"RuntimeError: provider exploded": script(RuntimeError("provider exploded")),
             "MaxTurnsExceeded": script(*loop),
             "ModelBehaviorError": script([final([{"n": 1}])])}       # final output that does not fit the schema
    for expect, model in cases.items():
        async with make_client(model) as c:
            fid = await upload(c)
            r = await c.post("/ask", json={"file_ids": [fid], **Q})
            assert r.status_code == 502, r.text
            body = r.json()
            assert body["status"] == "error" and expect in body["error"] and "Traceback" not in r.text
            run = (await c.get(f"/runs/{body['run_id']}")).json()
            assert run["status"] == "error" and run["events"][-1]["type"] == "run_finished"


async def test_missing_answers_become_abstentions(make_client):
    async with make_client() as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], "questions": ["a?", "b?"]})
        assert [a["n"] for a in r.json()["answers"]] == [1, 2] and r.json()["answers"][1]["answer"] is None


# ---- background runs, events, artifacts ------------------------------------------------------------------------
async def test_background_run_and_event_order(make_client):
    async with make_client() as c:
        f1, f2 = await upload(c), await upload(c, "second.ifc")
        r = await c.post("/runs", json={"file_ids": [f1, f2], **Q})
        assert r.status_code == 202 and r.json()["status"] == "queued"
        rid = r.json()["run_id"]
        s = await c.get(f"/runs/{rid}/events")                      # subscribed while the run is live
        assert s.headers["content-type"].startswith("text/event-stream")
        ev = sse_events(s.text)
        assert [(e["type"], e.get("tool")) for e in ev] == [
            ("run_started", None), ("tool_started", "shell"), ("tool_finished", "shell"),
            ("tool_started", "view_image"), ("tool_finished", "view_image"), ("answers", None), ("run_finished", None)]
        assert [e["seq"] for e in ev] == list(range(1, 8)) and [e["t"] for e in ev] == sorted(e["t"] for e in ev)
        assert ev[0]["files"] == ["drawing.dxf", "drawing_2.ifc"] and ev[1]["call_id"] == ev[2]["call_id"] == "call_1"
        assert ev[-2]["answers"][0]["answer"] == 5 and ev[-1]["status"] == "done"
        assert "id: 3\nevent: tool_finished\ndata: " in s.text
        # a late subscriber gets the whole backlog; `after` / Last-Event-ID resume behind what was seen
        assert sse_events((await c.get(f"/runs/{rid}/events")).text) == ev
        assert sse_events((await c.get(f"/runs/{rid}/events?after=5")).text) == ev[5:]
        assert sse_events((await c.get(f"/runs/{rid}/events", headers={"Last-Event-ID": "6"})).text) == ev[6:]
        assert (await c.get(f"/runs/{rid}")).json()["status"] == "done"


async def test_late_subscriber_gets_backlog_then_live(make_client):
    """Registry level, where delivery can be observed while the run is still going."""
    model = script([function_call("shell", dict(command="echo one", timeout_s=None), call_id="c1")],
                   [function_call("shell", dict(command="sleep 1; echo two", timeout_s=None), call_id="c2")],
                   [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        reg = c._transport.app.state.registry
        run = reg.create([fid], ["q"])
        task = reg.start(run)
        while not any(e.get("call_id") == "c2" for e in run.events):  # the second command has started
            await asyncio.sleep(0.01)
        backlog, seen = len(run.events), []
        async for e in reg.subscribe(run):
            seen.append((e["seq"], e["type"], run.status))
        await task
    assert backlog >= 4 and [s[0] for s in seen] == list(range(1, len(run.events) + 1))
    assert seen[backlog][2] == "running" and seen[-1][1] == "run_finished"   # live events arrived before the end


async def test_ask_stream(make_client):
    async with make_client() as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], **Q, "stream": True})
        ev = sse_events(r.text)
        assert r.headers["x-run-id"] and ev[0]["type"] == "run_started" and ev[-1]["type"] == "run_finished"


async def test_artifacts(make_client):
    async with make_client() as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        r = await c.get(f"/runs/{rid}/artifacts")
        assert [a["name"] for a in r.json()["artifacts"]] == ["render.png"]      # the input drawing is not an artifact
        img = await c.get(f"/runs/{rid}/artifacts/render.png")
        assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content[:4] == b"\x89PNG"
        assert img.headers["x-content-type-options"] == "nosniff" and "content-disposition" not in img.headers
        # an input is not listed, but is served under its workspace name: evidence cites images by that name
        assert (await c.get(f"/runs/{rid}/artifacts/drawing.dxf")).content == b"0\nEOF\n"
        assert (await c.get(f"/runs/{rid}/artifacts/missing.png")).status_code == 404


async def test_viewed_images_are_kept_as_the_model_saw_them(make_client):
    """The agent reuses file names for successive crops, and looks at the input itself: both must stay viewable."""
    def paint(colour: str, size: int) -> str:
        return f"python -c \"from PIL import Image; Image.new('RGB', ({size}, {size}), '{colour}').save('crop.png')\""
    model = script([function_call("shell", dict(command=paint("red", 50), timeout_s=None), call_id="c1")],
                   [function_call("view_image", dict(path="crop.png"), call_id="c2")],
                   [function_call("shell", dict(command=paint("blue", 70), timeout_s=None), call_id="c3")],
                   [function_call("view_image", dict(path="crop.png"), call_id="c4"),
                    function_call("view_image", dict(path="drawing.png"), call_id="c5")], [final()])
    async with make_client(model) as c:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (20, 10), "green").save(buf, "PNG")
        fid = await upload(c, "scan.png", buf.getvalue())
        run = (await c.get(f"/runs/{(await c.post('/ask', json={'file_ids': [fid], **Q})).json()['run_id']}")).json()
        views = {e["call_id"]: e for e in events_of(run, "tool_finished") if e["tool"] == "view_image"}
        sizes = {}
        for k, e in views.items():
            r = await c.get(e["view"])
            assert r.status_code == 200 and r.headers["content-type"] == "image/png", e
            sizes[k] = Image.open(io.BytesIO(r.content)).size
        assert sizes == {"c2": (50, 50), "c4": (70, 70), "c5": (20, 10)}       # c2 still shows the first crop
        assert len({e["view"] for e in views.values()}) == 3 and views["c5"]["path"] == "drawing.png"
        assert (await c.get(f"/runs/{run['id']}/artifacts/drawing.png")).status_code == 200
        for bad in ("9999.png", "x.png", "1.png.txt", "..%2frun.json", "%2e%2e"):
            assert (await c.get(f"/runs/{run['id']}/views/{bad}")).status_code == 404, bad


async def test_agent_written_html_and_svg_cannot_run_script(make_client):
    write = "echo '<script>alert(1)</script>' > report.html; echo '<svg xmlns=\"http://www.w3.org/2000/svg\"><script>1</script></svg>' > p.svg"
    model = script([function_call("shell", dict(command=write, timeout_s=None), call_id="c1")], [final()])
    async with make_client(model) as c:
        fid = await upload(c, "in.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        html = await c.get(f"/runs/{rid}/artifacts/report.html")
        assert html.headers["content-type"].startswith("text/html") is False
        assert html.headers["content-disposition"].startswith("attachment")
        for r in (html, await c.get(f"/runs/{rid}/artifacts/p.svg"), await c.get(f"/files/{fid}/raw")):
            assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["content-security-policy"] == "sandbox"


# ---- containment -----------------------------------------------------------------------------------------------
async def test_path_traversal_on_every_path_parameter(make_client, settings):
    escape = "ln -s /etc/passwd link.txt; ln -s / rootdir; ln -s ../run.json up.json; mkdir -p d; echo ok > d/fine.txt"
    model = script([function_call("shell", dict(command=escape, timeout_s=None), call_id="c1")],
                   [function_call("view_image", dict(path="../../../../etc/passwd"), call_id="c2"),
                    function_call("view_image", dict(path="/etc/passwd"), call_id="c3"),
                    function_call("view_image", dict(path="link.txt"), call_id="c4"),
                    function_call("view_image", dict(path="drawing.dxf"), call_id="c5")],
                   [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        run = (await c.get(f"/runs/{rid}")).json()
        views = {e["call_id"]: e["output"] for e in events_of(run, "tool_finished") if e["tool"] == "view_image"}
        assert all("outside the working directory" in views[k] for k in ("c2", "c3", "c4")), views
        assert "not a PNG/JPEG" in views["c5"]
        assert [a["name"] for a in (await c.get(f"/runs/{rid}/artifacts")).json()["artifacts"]] == ["d/fine.txt"]
        assert (await c.get(f"/runs/{rid}/artifacts/d/fine.txt")).text == "ok\n"
        secret = settings.data_dir / "runs" / rid / "run.json"
        assert secret.is_file()
        attacks = [f"/runs/{rid}/artifacts/../run.json", f"/runs/{rid}/artifacts/%2e%2e/run.json",
                   f"/runs/{rid}/artifacts/..%2frun.json", f"/runs/{rid}/artifacts/d/../../run.json",
                   f"/runs/{rid}/artifacts/%2Fetc%2Fpasswd", f"/runs/{rid}/artifacts//etc/passwd",
                   f"/runs/{rid}/artifacts/link.txt", f"/runs/{rid}/artifacts/rootdir/etc/passwd",
                   f"/runs/{rid}/artifacts/up.json", f"/runs/{rid}/artifacts/.cache", f"/runs/{rid}/artifacts/d%00x",
                   "/runs/../files", "/runs/%2e%2e%2ffiles", f"/runs/{rid}%2f..%2f{rid}", "/runs/" + "g" * 16,
                   f"/runs/{rid.upper()}x", "/runs/..%2f..%2frun/events", "/runs/%2e%2e/artifacts",
                   "/files/../raw", "/files/%2e%2e%2f%2e%2e%2fetc%2fpasswd/raw", f"/files/{fid}%2f..%2f{fid}/raw",
                   f"/files/{fid}/../../runs/{rid}/run.json", "/files/" + "0" * 16 + "/raw"]
        for url in attacks:
            r = await c.get(url)
            assert r.status_code in (403, 404, 405), (url, r.status_code)  # 405: the client collapsed ".." itself
            assert "root:" not in r.text and '"questions"' not in r.text, url


# ---- shell -----------------------------------------------------------------------------------------------------
async def test_shell_timeout_kills_the_process_group(make_client, settings):
    cmd = "sleep 300 & echo $! > child.pid; echo $$ > parent.pid; echo started; sleep 300"
    model = script([function_call("shell", dict(command=cmd, timeout_s=1), call_id="c1")], [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        t0 = time.monotonic()
        body = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()
        assert time.monotonic() - t0 < 15
        run = (await c.get(f"/runs/{body['run_id']}")).json()
    e = events_of(run, "tool_finished")[0]
    assert e["timed_out"] is True and e["exit_code"] is None and "started" in e["output"] and 1 <= e["seconds"] < 5
    assert run["status"] == "done"                                   # the model is told and carries on
    assert "timed out after 1 s" in str(model.last_call.input)
    ws = settings.data_dir / "runs" / body["run_id"] / "ws"
    await asyncio.sleep(0.2)
    for name in ("child.pid", "parent.pid"):
        pid = int((ws / name).read_text())
        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split(")")[-1].split()[0] == "Z", f"{name} survived"


async def test_shell_timeout_is_capped_and_output_truncated(make_client):
    model = script([function_call("shell", dict(command="sleep 30", timeout_s=9999), call_id="c1"),
                    function_call("shell", dict(command="echo HEAD; seq 1 200000; echo TAIL; exit 3", timeout_s=None), call_id="c2")],
                   [final()])
    async with make_client(model, shell_timeout=1, max_output_chars=2000, max_event_chars=400) as c:
        fid = await upload(c)
        run = (await c.get(f"/runs/{(await c.post('/ask', json={'file_ids': [fid], **Q})).json()['run_id']}")).json()
    by = {e["call_id"]: e for e in events_of(run, "tool_finished")}
    assert by["c1"]["timed_out"] and by["c2"]["exit_code"] == 3
    assert len(by["c2"]["output"]) < 500 and by["c2"]["output"].startswith("HEAD") and "TAIL" in by["c2"]["output"]
    to_model = [i["output"] for i in model.last_call.input if isinstance(i, dict) and i.get("call_id") == "c2" and "output" in i][0]
    assert len(to_model) < 2200 and "characters omitted" in to_model and to_model.endswith("TAIL\n\n[exit code 3]")


@pytest.mark.skipif(not sandbox_available("docker")[0], reason="docker (or its image) is not available")
async def test_docker_sandbox(make_client, secrets_in_env):
    cmd = (f"env; echo hi > out.txt; touch /usr/x 2>&1; ls {ROOT / 'service'} 2>&1; "
           "junction-areas --help; "
           "python -c \"import ezdxf, urllib.request as u; u.urlopen('http://1.1.1.1', timeout=3)\" 2>&1 | tail -1")
    model = script([function_call("shell", dict(command=cmd, timeout_s=None), call_id="c1"),
                    function_call("shell", dict(command="sleep 60", timeout_s=1), call_id="c2"),
                    function_call("shell", dict(command="yes AAAAAAAAAAAAAAAA", timeout_s=None), call_id="c3")], [final()])
    async with make_client(model, sandbox="docker", max_output_bytes=1 << 20) as c:
        assert (await c.get("/health")).json()["sandbox"]["mode"] == "docker"
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        run = (await c.get(f"/runs/{rid}")).json()
        assert [a["name"] for a in (await c.get(f"/runs/{rid}/artifacts")).json()["artifacts"]] == ["out.txt"]
    by = {e["call_id"]: e for e in events_of(run, "tool_finished")}
    out = by["c1"]["output"]
    assert SECRET not in out and "Read-only file system" in out and "No such file" in out and "unreachable" in out
    assert "usage: junction-areas" in out
    assert by["c2"]["timed_out"] and by["c3"]["ended"] == "overflow"
    import subprocess
    assert subprocess.run(["docker", "ps", "-q", "--filter", "name=junction-"], capture_output=True).stdout == b""


# ---- auth, health, persistence ---------------------------------------------------------------------------------
async def test_auth(make_client):
    async with make_client(api_token="s3cret") as c:
        h = await c.get("/health")
        assert h.status_code == 200 and h.json() == {"status": "ok"}  # liveness needs no token, and tells nothing
        for open_before in ("/docs", "/openapi.json", "/redoc"):
            assert (await c.get(open_before)).status_code == 401
        fid_req = dict(files={"file": ("a.dxf", b"x")})
        assert (await c.post("/files", **fid_req)).status_code == 401
        assert (await c.post("/files", headers={"Authorization": "Bearer wrong"}, **fid_req)).status_code == 401
        ok = {"Authorization": "Bearer s3cret"}
        fid = (await c.post("/files", headers=ok, **fid_req)).json()["file_id"]
        for method, url in [("GET", f"/files/{fid}/raw"), ("POST", "/files/register"), ("POST", "/ask"), ("POST", "/runs"),
                            ("GET", "/runs/" + "0" * 16), ("GET", "/runs/" + "0" * 16 + "/events"),
                            ("GET", "/runs/" + "0" * 16 + "/artifacts"), ("GET", "/runs/" + "0" * 16 + "/artifacts/x")]:
            assert (await c.request(method, url)).status_code == 401, url
        assert (await c.get(f"/files/{fid}/raw", headers=ok)).status_code == 200
        assert (await c.get("/openapi.json", headers=ok)).status_code == 404         # not served at all with a token
        assert (await c.get("/health", headers=ok)).json()["auth"] is True
        assert (await c.get(f"/files/{fid}/raw?token=s3cret")).status_code == 200     # for <img> / EventSource
        assert (await c.get(f"/files/{fid}/raw?token=nope")).status_code == 401


async def test_health(make_client, settings):
    async with make_client() as c:
        h = (await c.get("/health")).json()
    assert h["status"] == "ok" and h["sandbox"]["mode"] == "none" and h["sandbox"]["available"]["none"] is True
    assert set(h["converters"]) == {"tesseract"} and h["auth"] is False and "name" in h["model"]


async def test_unusable_sandbox_is_refused_at_startup(make_client):
    with pytest.raises(RuntimeError, match="bwrap"):
        make_client(sandbox="bwrap")


async def test_runs_survive_a_restart(make_client, settings):
    async with make_client() as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        before = (await c.get(f"/runs/{rid}")).json()
    # a run that was executing when the process died
    dead = dict(before, id="ab" * 8, status="running", answers=None)
    (settings.data_dir / "runs" / dead["id"]).mkdir()
    (settings.data_dir / "runs" / dead["id"] / "run.json").write_text(json.dumps(dead))
    (settings.data_dir / "runs" / ("cd" * 8)).mkdir()
    (settings.data_dir / "runs" / ("cd" * 8) / "run.json").write_text("{corrupt")
    async with make_client(script()) as c:                              # a new app over the same data dir
        after = (await c.get(f"/runs/{rid}")).json()
        assert after == before and after["status"] == "done" and after["answers"][0]["answer"] == 5
        assert (await c.get(f"/runs/{rid}/artifacts/render.png")).status_code == 200
        assert sse_events((await c.get(f"/runs/{rid}/events")).text) == before["events"]
        assert (await c.get(f"/files/{fid}/raw")).status_code == 200
        lost = (await c.get(f"/runs/{dead['id']}")).json()
        assert lost["status"] == "error" and "restart" in lost["error"]
        tail = sse_events((await c.get(f"/runs/{dead['id']}/events")).text)[-1]     # the stream still ends properly
        assert tail["type"] == "run_finished" and tail["status"] == "error" and tail["seq"] == len(lost["events"])
        assert (await c.get("/runs/" + "cd" * 8)).status_code == 404


async def test_concurrency_limit(make_client):
    steps = []
    for i in range(3):
        steps += [[function_call("shell", dict(command="sleep 0.6", timeout_s=None), call_id=f"c{i}")], [final()]]

    async with make_client(script(*steps), max_concurrent=1) as c:
        fid = await upload(c)
        t0 = time.monotonic()
        rs = await asyncio.gather(*[c.post("/ask", json={"file_ids": [fid], **Q}) for _ in range(3)])
        assert all(r.status_code == 200 for r in rs) and time.monotonic() - t0 >= 1.8   # one at a time


async def test_model_text_between_tool_calls_is_an_event(make_client):
    from agents.testing import assistant_message
    model = script([assistant_message("Looking at the layers first."),
                    function_call("shell", dict(command="true", timeout_s=None), call_id="c1")], [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        run = (await c.get(f"/runs/{(await c.post('/ask', json={'file_ids': [fid], **Q})).json()['run_id']}")).json()
    assert [e["text"] for e in events_of(run, "message")] == ["Looking at the layers first."]   # the final JSON is not one


# ---- regressions from the review ---------------------------------------------------------------------------------
async def test_last_event_id_that_int_rejects(make_client):
    async with make_client() as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        for bad in ("9" * 5000, "²", "-1", "1e3"):
            r = await c.get(f"/runs/{rid}/events", headers={"Last-Event-ID": bad.encode("latin-1")})
            assert r.status_code == 200 and len(sse_events(r.text)) == 7, bad[:10]


async def test_shell_output_flood_is_killed_and_never_hits_the_disk(make_client, settings):
    model = script([function_call("shell", dict(command="echo HEAD; yes AAAAAAAAAAAAAAAA", timeout_s=None), call_id="c1")], [final()])
    async with make_client(model, max_output_bytes=1 << 20, max_output_chars=2000) as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        e = events_of((await c.get(f"/runs/{rid}")).json(), "tool_finished")[0]
    assert e["ended"] == "overflow" and e["exit_code"] is None and e["seconds"] < 10 and e["output"].startswith("HEAD")
    assert "killed: printed more than 1 MB" in str(model.last_call.input)
    run_dir = settings.data_dir / "runs" / rid
    assert sum(f.stat().st_size for f in run_dir.rglob("*") if f.is_file()) < 100_000   # nothing was captured to a file


async def test_background_processes_do_not_outlive_the_call(make_client, settings):
    cmd = ("nohup sleep 301 >/dev/null 2>&1 & echo $! > nohup.pid; "
           "setsid sh -c 'echo $$ > setsid.pid; exec sleep 302' >/dev/null 2>&1 & "
           "sleep 303 & echo $! > pipe.pid; "            # this one inherits the output pipe and would hold it open
           "sleep 0.3; echo started")
    model = script([function_call("shell", dict(command=cmd, timeout_s=None), call_id="c1")], [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        e = events_of((await c.get(f"/runs/{rid}")).json(), "tool_finished")[0]
    assert e["exit_code"] == 0 and e["seconds"] < 5 and "started" in e["output"]
    await asyncio.sleep(0.2)
    for name in ("nohup.pid", "setsid.pid", "pipe.pid"):
        stat = Path(f"/proc/{int((settings.data_dir / 'runs' / rid / 'ws' / name).read_text())}/stat")
        assert not stat.exists() or stat.read_text().split(")")[-1].split()[0] == "Z", f"{name} survived"


async def test_upload_is_bounded_before_auth_and_before_spooling(make_client):
    """A chunked body (no Content-Length) must not be pulled in before the token and the size limit are checked."""
    pulled = 0

    async def body():
        nonlocal pulled
        yield b'--B\r\nContent-Disposition: form-data; name="file"; filename="a.dxf"\r\n\r\n'
        for _ in range(32):
            pulled += 1
            yield b"x" * (1 << 20)
        yield b"\r\n--B--\r\n"
    multipart = {"content-type": "multipart/form-data; boundary=B"}
    async with make_client(api_token="tok", max_upload_mb=1) as c:
        assert (await c.post("/files", content=body(), headers=multipart)).status_code == 401
        assert pulled == 0
        r = await c.post("/files", content=body(), headers={**multipart, "Authorization": "Bearer tok"})
        assert r.status_code == 413 and pulled <= 3, pulled
        r = await c.post("/ask", content=body(), headers={"content-type": "application/json", "Authorization": "Bearer tok"})
        assert r.status_code == 413 and pulled <= 6, pulled     # the budget covers every route, not only uploads


async def test_oversized_image_is_refused_before_decoding(make_client, monkeypatch):
    monkeypatch.setattr("service.tools.MAX_PIXELS", 1_000_000)
    make = "python -c \"from PIL import Image; Image.new('L', (1900, 1000)).save('big.png')\""   # 1.9x the limit
    model = script([function_call("shell", dict(command=make, timeout_s=None), call_id="c1")],
                   [function_call("view_image", dict(path="big.png"), call_id="c2")], [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        run = (await c.get(f"/runs/{(await c.post('/ask', json={'file_ids': [fid], **Q})).json()['run_id']}")).json()
    e = events_of(run, "tool_finished")[1]
    assert e["exit_code"] == 1 and "image too large: 1900x1000" in e["output"] and "view" not in e


async def test_calls_that_never_reach_a_tool_are_still_in_the_trace(make_client):
    model = script([function_call("shell", dict(command="echo a\x00b", timeout_s=None), call_id="c1")],   # fails inside the tool
                   [function_call("shell", dict(cmd="ls"), call_id="c2")],                              # arguments do not fit
                   [function_call("Bash", dict(command="ls"), call_id="c3")],                           # no such tool
                   [final()])
    async with make_client(model) as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], **Q})
        assert r.status_code == 200, r.text                      # none of these costs the run
        run = (await c.get(f"/runs/{r.json()['run_id']}")).json()
    started = {e["call_id"]: e for e in events_of(run, "tool_started")}
    finished = {e["call_id"]: e for e in events_of(run, "tool_finished")}
    assert list(started) == list(finished) == ["c1", "c2", "c3"]
    assert [finished[k]["ended"] for k in finished] == ["failed", "rejected", "rejected"]
    assert started["c3"]["tool"] == "Bash" and "ls" in started["c3"]["arguments"] and finished["c2"]["output"]
    assert all(started[k]["seq"] < finished[k]["seq"] for k in started)


async def test_final_answer_wrapped_in_a_code_fence_is_salvaged(make_client):
    from agents.testing import assistant_message
    answer = dict(n=1, answer="two", unit=None, method="m", evidence=[], evidence_total=0, check="none", confidence=0.5)
    fenced = "Here are the answers:\n```json\n" + json.dumps({"answers": [answer]}) + "\n```"
    async with make_client(script([assistant_message(fenced)])) as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], **Q})
        assert r.status_code == 200 and r.json()["answers"][0]["answer"] == "two", r.text
        run = (await c.get(f"/runs/{r.json()['run_id']}")).json()
    assert not events_of(run, "message") and events_of(run, "answers")
