"""Container sandbox escape proof (S0.4):
Verifies that shell commands executed through `service.tools.run_shell` in `docker` mode:
1. Cannot read service secrets (`JUNCTION_API_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY`) from `/proc/*/environ`.
2. Have zero outbound network connectivity (`wget -T3 -qO- https://example.com` fails).
3. Are killed when exceeding `JUNCTION_SHELL_TIMEOUT`, leaving `docker ps -q --filter name=junction-` empty.
"""
from __future__ import annotations

import subprocess
import pytest
from agents.testing import function_call

from service.config import sandbox_available
from .conftest import SECRET, final, script, upload

pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(not sandbox_available("docker")[0], reason="docker/podman sandbox image unavailable")
async def test_container_sandbox_blocks_proc_environ_network_and_orphans(make_client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("JUNCTION_OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("JUNCTION_API_TOKEN", SECRET)

    probe = (
        "strings /proc/*/environ 2>/dev/null | grep -E '^(OPENAI_API_KEY|GEMINI_API_KEY|JUNCTION_API_TOKEN|JUNCTION_OPENAI_API_KEY)=' || echo PROC_CLEAN; "
        "if wget -T3 -qO- https://example.com >/dev/null 2>&1; then echo NET_OPEN; else echo NET_BLOCKED; fi"
    )
    model = script(
        [function_call("shell", dict(command=probe, timeout_s=None), call_id="probe_1"),
         function_call("shell", dict(command="sleep 120", timeout_s=999), call_id="timeout_1")],
        [final()],
    )
    async with make_client(model, sandbox="docker", shell_timeout=2) as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], "questions": ["How many doors?"]})
        assert r.status_code == 200, r.text
        run = (await c.get(f"/runs/{r.json()['run_id']}")).json()

    by = {e["call_id"]: e for e in run["events"] if e["type"] == "tool_finished"}
    out = by["probe_1"]["output"]
    assert SECRET not in out
    assert "PROC_CLEAN" in out
    assert "NET_BLOCKED" in out
    assert "NET_OPEN" not in out

    assert by["timeout_1"]["timed_out"] is True
    leftover = subprocess.run(["docker", "ps", "-q", "--filter", "name=junction-"], capture_output=True, timeout=10)
    assert leftover.stdout.strip() == b""
