"""MCP: tools over the shared pipeline and budget; the HTTP mount answers at /mcp/."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from fastmcp import Client

from aura.mcp import build_mcp
from tests.conftest import make_app


def _rpc(c: TestClient, method: str, params: dict, i: int = 1) -> dict:
    r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": i, "method": method, "params": params},
               headers={"accept": "application/json, text/event-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def test_mcp_tools_in_memory(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        svc = c.app.state.svc
        mcp = build_mcp(lambda: svc)

        async def go():
            async with Client(mcp) as m:
                names = {t.name for t in await m.list_tools()}
                assert names == {"list_drawings", "ask_drawing", "evidence_csv_for"}
                lib = (await m.call_tool("list_drawings", {})).structured_content
                drawings = lib["result"] if isinstance(lib, dict) and "result" in lib else lib
                assert drawings[0]["id"] == "swiss-46639" and "window" in drawings[0]["capabilities"]
                a = (await m.call_tool("ask_drawing", {
                    "drawing_id": "swiss-46639",
                    "question": "How many windows are drawn on this floor plan?"})).structured_content
                assert a["answer"]["value"] == 12 and a["calls"] == 0
                await svc.writer.flush()
                csv = (await m.call_tool("evidence_csv_for", {"answer_id": a["answer_id"]})).data
                assert csv.startswith("kind,id,subject") and "win-01" in csv

        c.portal.call(go)


def test_mcp_http_mount(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        init = _rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})
        assert init["result"]["serverInfo"]["name"] == "AURA"
        tools = _rpc(c, "tools/list", {}, 2)["result"]["tools"]
        assert {t["name"] for t in tools} >= {"ask_drawing", "list_drawings"}
        res = _rpc(c, "tools/call", {"name": "ask_drawing", "arguments": {
            "drawing_id": "swiss-46639", "question": "What is the net floor area of apartment A?"}},
            3)["result"]
        body = res.get("structuredContent") or json.loads(res["content"][0]["text"])
        assert body["answer"]["value"] == 139.34
