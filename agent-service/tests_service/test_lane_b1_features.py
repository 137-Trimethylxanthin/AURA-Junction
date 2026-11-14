"""Unit and integration tests for Lane B1 deliverables:
- DWG removal & zero AGPL/GPL references
- Evidence multi-group color coding, directional lead_to vectors, multi-plan file fields & 250-item cap
- relay_tool_images 4-image context cap
- DELETE /files/{file_id} & Btrfs CoW _precomputed workspace seeding
- Podman XDG_RUNTIME_DIR/TMPDIR preservation & /opt/junction/{bin,areas} read-only mount
- Live crop_px / image_px / scale telemetry on view_image
- JUNCTION_REASONING_EFFORT="low" with HTTP 400 retry fallback
- POST /runs/{run_id}/cancel task + subprocess cleanup
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
from agents.run_config import CallModelData, ModelInputData
from agents.testing import assistant_message, function_call

from service.agent import Answers, Evidence, instructions, normalise, relay_tool_images
from service.config import ALLOWED_EXT, ROOT, Settings, sandbox_available
from service.tools import _argv, _client_env

from .conftest import SECRET, final, script, upload

pytestmark = pytest.mark.asyncio
Q = {"questions": ["Show all stairs and where they lead to."]}


async def test_dwg_disallowed_and_prompt_guardrails(make_client):
    assert "dwg" not in ALLOWED_EXT
    async with make_client() as c:
        r = await c.post("/files", files={"file": ("plan.dwg", b"AC1027")})
        assert r.status_code == 422

    inst = instructions()
    low = inst.lower()
    assert ("pymu" + "pdf") not in low
    assert ("fi" + "tz") not in low
    assert ("dwg" + "2dxf") not in low
    assert ("/root/" + "junction") not in low
    assert "_precomputed" in inst
    assert "crop_" in inst
    assert "250" in inst
    assert "group_label" in inst
    assert "lead_to_px" in inst
    assert "target_file" in inst
    assert "Assumed plan-top = North (no compass arrow printed)" in inst
    assert "Reference-region similarity queries" in inst
    assert "Prior conversation context:" in inst


async def test_delete_file_endpoint(make_client):
    async with make_client(api_token="tok123") as c:
        auth = {"Authorization": "Bearer tok123"}
        fid = (await c.post("/files", headers=auth, files={"file": ("a.dxf", b"0\nEOF\n")})).json()["file_id"]
        assert (await c.get(f"/files/{fid}/raw", headers=auth)).status_code == 200

        # Unauthenticated delete rejected
        assert (await c.delete(f"/files/{fid}")).status_code == 401

        # Authenticated delete succeeds
        d = await c.delete(f"/files/{fid}", headers=auth)
        assert d.status_code == 200 and d.json() == {"deleted": fid}

        # Gone afterwards; second delete is 404
        assert (await c.get(f"/files/{fid}/raw", headers=auth)).status_code == 404
        assert (await c.delete(f"/files/{fid}", headers=auth)).status_code == 404
        assert (await c.delete("/files/not-a-valid-id", headers=auth)).status_code == 404
        assert (await c.delete("/files/" + "0" * 16, headers=auth)).status_code == 404


async def test_precomputed_cow_placement_and_artifacts_exclusion(make_client, register_root, monkeypatch):
    pre1 = register_root / "pre_floor1"
    (pre1 / "tiles").mkdir(parents=True)
    (pre1 / "index.json").write_text('{"floor": 1}')
    (pre1 / "areas.json").write_text('{"rooms": []}')
    (pre1 / "tiles" / "t0.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    pre2 = register_root / "pre_floor2"
    pre2.mkdir(parents=True)
    (pre2 / "project_manifest.json").write_text('{"floors": ["drawing.dxf", "drawing_2.dxf"]}')
    (register_root / "sub" / "floor2.dxf").write_text("0\nEOF\n")

    check_cmd = (
        "python -c \"import json, os; "
        "assert os.path.isfile('_precomputed/index.json') and not os.path.islink('_precomputed') "
        "and not os.path.islink('_precomputed/index.json'); "
        "assert os.path.isfile('_precomputed/tiles/t0.png'); "
        "assert os.path.isfile('_precomputed_2/project_manifest.json') and not os.path.islink('_precomputed_2'); "
        "open('agent_note.txt', 'w').write('verified')\""
    )
    model = script([function_call("shell", dict(command=check_cmd, timeout_s=None), call_id="c1")], [final()])

    async with make_client(model) as c:
        # Escaping or missing precomputed_dir rejected
        assert (await c.post("/files/register", json={"path": "sub/Secret Name A-801.dxf", "precomputed_dir": "../../etc"})).status_code == 403
        assert (await c.post("/files/register", json={"path": "sub/Secret Name A-801.dxf", "precomputed_dir": "missing_dir"})).status_code == 404

        r1 = await c.post("/files/register", json={"path": "sub/Secret Name A-801.dxf", "precomputed_dir": "pre_floor1"})
        r2 = await c.post("/files/register", json={"path": "sub/floor2.dxf", "precomputed_dir": str(pre2)})
        assert r1.status_code == 200 and r2.status_code == 200
        fid1, fid2 = r1.json()["file_id"], r2.json()["file_id"]

        ask = await c.post("/ask", json={"file_ids": [fid1, fid2], **Q})
        assert ask.status_code == 200, ask.text
        rid = ask.json()["run_id"]
        run = (await c.get(f"/runs/{rid}")).json()
        sh = [e for e in run["events"] if e["type"] == "tool_finished"][0]
        assert sh["exit_code"] == 0, sh["output"]

        arts = (await c.get(f"/runs/{rid}/artifacts")).json()["artifacts"]
        assert [a["name"] for a in arts] == ["agent_note.txt"]

    # Also test shutil.copytree fallback when `cp --reflink=auto` fails
    import subprocess as sp
    real_run = sp.run
    monkeypatch.setattr("service.workspace.subprocess.run",
                        lambda *a, **kw: sp.CompletedProcess(a[0], 1, b"", b"reflink unsupported")
                        if a and a[0][:2] == ["cp", "--reflink=auto"] else real_run(*a, **kw))
    model2 = script([function_call("shell", dict(command="test -f _precomputed/index.json", timeout_s=None), call_id="c2")], [final()])
    async with make_client(model2) as c:
        r1 = await c.post("/files/register", json={"path": "sub/Secret Name A-801.dxf", "precomputed_dir": "pre_floor1"})
        ask = await c.post("/ask", json={"file_ids": [r1.json()["file_id"]], **Q})
        run = (await c.get(f"/runs/{ask.json()['run_id']}")).json()
        assert [e for e in run["events"] if e["type"] == "tool_finished"][0]["exit_code"] == 0


async def test_evidence_multi_group_leads_to_and_250_cap(make_client):
    ev_stair = Evidence(
        kind="image",
        bbox_px=[100.0, 120.0, 220.0, 260.0],
        image="drawing.png",
        label="Stair S1",
        file="drawing.pdf",
        group="stair-S1",
        group_label="Main Stair S1 -> 1. OG",
        lead_to_px=[160.0, 80.0],
        lead_to_cad=[8.5, 4.2],
        lead_to_label="Leads to 1. OG Hallway 2.01",
        target_file="drawing_2.pdf",
    )
    many = [Evidence(kind="cad", handle=f"H{i}", layer="DOORS", bbox=[0, 0, 1, 1], file="drawing.dxf", group="apt-A")
            for i in range(300)]
    raw_answers = Answers(answers=[
        dict(n=1, answer=1, unit="count", method="inspected stairs", evidence=[ev_stair], evidence_total=1,
             check="verified across floors", confidence=0.92),
        dict(n=2, answer=300, unit="count", method="counted all", evidence=many, evidence_total=None,
             check="ok", confidence=0.95),
    ])
    norm = normalise(raw_answers, 2)
    e0 = norm[0]["evidence"][0]
    assert e0["file"] == "drawing.pdf"
    assert e0["group"] == "stair-S1"
    assert e0["group_label"] == "Main Stair S1 -> 1. OG"
    assert e0["lead_to_px"] == [160.0, 80.0]
    assert e0["lead_to_cad"] == [8.5, 4.2]
    assert e0["lead_to_label"] == "Leads to 1. OG Hallway 2.01"
    assert e0["target_file"] == "drawing_2.pdf"
    assert "guid" not in e0  # None fields still omitted

    assert len(norm[1]["evidence"]) == 250
    assert norm[1]["evidence_total"] == 300


async def test_relay_tool_images_caps_to_latest_4_views():
    inputs = []
    for i in range(1, 7):
        inputs.append({"type": "function_call", "call_id": f"v{i}", "name": "view_image", "arguments": "{}"})
        inputs.append({
            "type": "function_call_output",
            "call_id": f"v{i}",
            "output": [
                {"type": "input_text", "text": f"crop_{i}.png: 400x400 px"},
                {"type": "input_image", "image_url": f"data:image/png;base64,IMG{i}", "detail": "high"},
            ],
        })
        inputs.append({"role": "assistant", "content": f"step {i}"})

    data = CallModelData(model_data=ModelInputData(input=inputs, instructions="sys"), agent=None, context=None)
    filtered = relay_tool_images(data)

    # Collect all input_image blocks emitted across the entire filtered input
    emitted_images = [
        part["image_url"]
        for item in filtered.input
        if isinstance(item, dict) and isinstance(item.get("content"), list)
        for part in item["content"]
        if isinstance(part, dict) and part.get("type") == "input_image"
    ]
    assert emitted_images == [f"data:image/png;base64,IMG{i}" for i in (3, 4, 5, 6)]

    by_call = {
        item["call_id"]: item["output"]
        for item in filtered.input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }
    assert "[earlier image view omitted from context to save tokens]" in by_call["v1"]
    assert "[earlier image view omitted from context to save tokens]" in by_call["v2"]
    assert "(image attached in the next message)" in by_call["v3"]
    assert "(image attached in the next message)" in by_call["v6"]


async def test_view_image_crop_telemetry(make_client):
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000), "white").save(buf, "PNG")
    prep = (
        "python -c \"import json; from PIL import Image; "
        "Image.new('RGB', (400, 400), 'red').save('crop_100_200_500_600.png'); "
        "Image.new('RGB', (300, 200), 'blue').save('zoom.png'); "
        "open('zoom.png.crop.json', 'w').write(json.dumps({"
        "'crop_px': [50, 60, 350, 260], 'image_px': [2000, 1000], 'source_image': 'drawing.png', 'scale': 1.0}))\""
    )
    model = script(
        [function_call("shell", dict(command=prep, timeout_s=None), call_id="c1")],
        [function_call("view_image", dict(path="crop_100_200_500_600.png"), call_id="c2"),
         function_call("view_image", dict(path="zoom.png"), call_id="c3")],
        [final()],
    )
    async with make_client(model) as c:
        fid = await upload(c, "plan.png", buf.getvalue())
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        run = (await c.get(f"/runs/{rid}")).json()

    started = {e["call_id"]: e for e in run["events"] if e["type"] == "tool_started" and e.get("tool") == "view_image"}
    finished = {e["call_id"]: e for e in run["events"] if e["type"] == "tool_finished" and e.get("tool") == "view_image"}

    for ev_map in (started, finished):
        assert ev_map["c2"]["crop_px"] == [100, 200, 500, 600]
        assert ev_map["c2"]["image_px"] == [2000, 1000]
        assert ev_map["c2"]["path"] == "crop_100_200_500_600.png"
        assert isinstance(ev_map["c2"]["scale"], (int, float))

        assert ev_map["c3"]["crop_px"] == [50, 60, 350, 260]
        assert ev_map["c3"]["image_px"] == [2000, 1000]
        assert ev_map["c3"]["source_image"] == "drawing.png"
        assert ev_map["c3"]["scale"] == 1.0


async def test_cancel_endpoint_kills_running_task_and_subprocess(make_client, settings):
    slow = script([function_call("shell", dict(command="echo $$ > slow.pid; sleep 120", timeout_s=None), call_id="c1")],
                  [final()])
    async with make_client(slow) as c:
        reg = c._transport.app.state.registry
        fid = await upload(c)
        rid = (await c.post("/runs", json={"file_ids": [fid], **Q})).json()["run_id"]
        assert rid in reg.task_by_id

        pid_file = settings.data_dir / "runs" / rid / "ws" / "slow.pid"
        for _ in range(200):
            if pid_file.is_file() and pid_file.read_text().strip():
                break
            await asyncio.sleep(0.02)
        assert pid_file.is_file()
        pid = int(pid_file.read_text().strip())

        cancel_r = await c.post(f"/runs/{rid}/cancel")
        assert cancel_r.status_code == 200 and cancel_r.json() == {"id": rid, "status": "cancelled"}
        assert rid not in reg.task_by_id

        await asyncio.sleep(0.1)
        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split(")")[-1].split()[0] == "Z"

        run_doc = (await c.get(f"/runs/{rid}")).json()
        assert run_doc["status"] == "cancelled"
        assert run_doc["events"][-1]["type"] == "run_finished"
        assert run_doc["events"][-1]["status"] == "cancelled"

        # Idempotent on already-cancelled run, and unknown run -> 404
        assert (await c.post(f"/runs/{rid}/cancel")).json() == {"id": rid, "status": "cancelled"}
        assert (await c.post("/runs/" + "0" * 16 + "/cancel")).status_code == 404

    # Cancelling an already completed run returns its existing terminal status ("done")
    async with make_client() as c:
        fid = await upload(c)
        done_rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        r_done = await c.post(f"/runs/{done_rid}/cancel")
        assert r_done.status_code == 200 and r_done.json() == {"id": done_rid, "status": "done"}


async def test_reasoning_effort_and_http_400_fallback(make_client, monkeypatch):
    monkeypatch.delenv("JUNCTION_REASONING_EFFORT", raising=False)
    monkeypatch.setenv("JUNCTION_MODEL", "gemini-3.8-flash")
    assert Settings.from_env().reasoning_effort == "low"
    monkeypatch.setenv("JUNCTION_REASONING_EFFORT", "medium")
    assert Settings.from_env().reasoning_effort == "medium"
    monkeypatch.setenv("JUNCTION_REASONING_EFFORT", "off")
    assert Settings.from_env().reasoning_effort is None

    seen_bodies: list[dict] = []
    ans_payload = json.dumps({
        "answers": [{
            "n": 1, "answer": 3, "unit": "count", "method": "ok",
            "evidence": [], "evidence_total": 0, "check": "ok", "confidence": 0.9,
        }]
    })

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            body = json.loads(raw)
            seen_bodies.append(body)
            if "reasoning_effort" in body:
                err = json.dumps({"error": {"message": "Unknown parameter: 'reasoning_effort'.", "type": "invalid_request_error"}}).encode()
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            chunk1 = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                      "choices": [{"index": 0, "delta": {"role": "assistant", "content": ans_payload}, "finish_reason": "stop"}]}
            chunk2 = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                      "choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 12, "total_tokens": 62}}
            sse = f"data: {json.dumps(chunk1)}\n\ndata: {json.dumps(chunk2)}\n\ndata: [DONE]\n\n".encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(sse)))
            self.end_headers()
            self.wfile.write(sse)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        async with make_client(False, model="gemini-3.8-flash", openai_base_url=url, openai_api_key="k",
                               api_style="chat", reasoning_effort="low") as c:
            fid = await upload(c)
            r = await c.post("/ask", json={"file_ids": [fid], **Q})
            assert r.status_code == 200, r.text
            assert r.json()["answers"][0]["answer"] == 3
        assert len(seen_bodies) == 2
        assert seen_bodies[0].get("reasoning_effort") == "low"
        assert "reasoning_effort" not in seen_bodies[1]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not sandbox_available("docker")[0], reason="docker/podman image not available")
async def test_podman_env_and_junction_areas_in_docker_sandbox(make_client, monkeypatch):
    xdg_val = "/run/user/1000"
    monkeypatch.setenv("XDG_RUNTIME_DIR", xdg_val)
    monkeypatch.setenv("TMPDIR", "/tmp")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    cenv = _client_env()
    assert cenv.get("XDG_RUNTIME_DIR") == xdg_val
    assert cenv.get("TMPDIR") == "/tmp"
    assert "OPENAI_API_KEY" not in cenv

    s = Settings(sandbox="docker")
    argv = _argv(s, Path("/tmp/ws"), "junction-areas --help", "junction-test")
    joined = " ".join(argv)
    assert f"{ROOT / 'bin'}:/opt/junction/bin:ro" in joined
    assert f"{ROOT / 'areas'}:/opt/junction/areas:ro" in joined
    assert "PYTHONPATH=/opt/junction" in joined

    # Restore real XDG_RUNTIME_DIR for rootless podman execution
    monkeypatch.undo()
    cmd = "which junction-areas && junction-areas --help && python -c 'import areas, pdfplumber, pypdfium2; print(\"SANDBOX_IMPORTS_OK\")'"
    model = script([function_call("shell", dict(command=cmd, timeout_s=None), call_id="c1")], [final()])
    async with make_client(model, sandbox="docker") as c:
        fid = await upload(c)
        rid = (await c.post("/ask", json={"file_ids": [fid], **Q})).json()["run_id"]
        run = (await c.get(f"/runs/{rid}")).json()
    out = [e for e in run["events"] if e["type"] == "tool_finished"][0]["output"]
    assert "/opt/junction/bin/junction-areas" in out
    assert "usage: junction-areas" in out
    assert "SANDBOX_IMPORTS_OK" in out
