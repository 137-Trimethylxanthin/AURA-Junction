"""Graceful shutdown under a real uvicorn: an open event stream or a blocking /ask must not keep the server alive
until the run finishes."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

SERVER = """
import dataclasses, sys
from pathlib import Path
import uvicorn
from agents.testing import ScriptedModel, function_call
from service.app import create_app
from service.config import Settings
model = ScriptedModel([[function_call("shell", {"command": "echo $$ > pid; sleep 120", "timeout_s": None}, call_id="c1")]] * 2)
settings = dataclasses.replace(Settings(), model="scripted", data_dir=Path(sys.argv[1]))
uvicorn.run(create_app(settings, model=model), host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
"""


@pytest.mark.parametrize("hold", ["events", "ask"])
def test_sigterm_cancels_runs_while_a_client_is_connected(tmp_path: Path, hold: str):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = Path(__file__).resolve().parent.parent
    server = subprocess.Popen([sys.executable, "-c", SERVER, str(tmp_path), str(port)], cwd=root,
                              env={**os.environ, "PYTHONPATH": str(root)})
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            for _ in range(100):
                try:
                    c.get("/health")
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            fid = c.post("/files", files={"file": ("a.dxf", b"0\nEOF\n")}).json()["file_id"]
            body = {"file_ids": [fid], "questions": ["q"], "stream": hold == "ask"}
            with c.stream("POST", "/ask" if hold == "ask" else "/runs", json=body) as first:
                rid = first.headers.get("x-run-id") or json.loads(first.read())["run_id"]
                with c.stream("GET", f"/runs/{rid}/events") as stream:   # a UI watching the run
                    lines = stream.iter_lines()
                    while "tool_started" not in next(lines):
                        pass
                    pid = tmp_path / "runs" / rid / "ws" / "pid"
                    while not pid.exists() or not pid.read_text().strip():
                        time.sleep(0.05)
                    t0 = time.monotonic()
                    server.send_signal(signal.SIGTERM)
                    rest = "\n".join(lines)                              # the stream ends instead of hanging
            assert server.wait(timeout=15) is not None and time.monotonic() - t0 < 10
        assert '"type": "run_finished"' in rest and "shutting down" in rest
        run = json.loads((tmp_path / "runs" / rid / "run.json").read_text())
        assert run["status"] == "error" and "shutting down" in run["error"]
        assert [e["type"] for e in run["events"]][-2:] == ["tool_finished", "run_finished"]   # the cancelled call is closed
        assert not Path(f"/proc/{int(pid.read_text())}").exists()        # and its command did not outlive the server
    finally:
        server.kill()
        server.wait()
