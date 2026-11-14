"""Model traffic through a Bifrost gateway, proven against a local stub that speaks the part of Bifrost's
OpenAI-compatible API the service uses: POST /openai/v1/responses (streamed), GET /openai/v1/models, GET /health."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from service.config import Settings

from .conftest import RENDER, SECRET, upload

pytestmark = pytest.mark.asyncio
MODEL, KEY = "anthropic/claude-test-1", "sk-bf-virtual-key-for-tests"
ANSWER = dict(n=1, answer=7, unit="count", method="counted", check="render agrees", confidence=0.8, evidence_total=1,
              evidence=[dict(kind="image", guid=None, ifc_class=None, name=None, handle=None, layer=None, bbox=None,
                             bbox_px=[1, 2, 3, 4], image="render.png", text=None, label="door", note=None)])
# What the "model" behind the gateway does, one item per request: the same shape as a real run.
SCRIPT = [dict(type="function_call", id="fc_1", call_id="call_1", name="shell", status="completed",
               arguments=json.dumps(dict(command=RENDER, timeout_s=None))),
          dict(type="function_call", id="fc_2", call_id="call_2", name="view_image", status="completed",
               arguments=json.dumps(dict(path="render.png"))),
          dict(type="message", id="msg_1", role="assistant", status="completed",
               content=[dict(type="output_text", text=json.dumps(dict(answers=[ANSWER])), annotations=[])])]


class Gateway(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.seen: list[dict] = []          # every request: method, path, authorization, body
        self.url = f"http://127.0.0.1:{self.server_address[1]}"
        threading.Thread(target=self.serve_forever, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        entry = dict(method=self.command, path=self.path, authorization=self.headers.get("authorization"),
                     body=json.loads(raw) if raw else None)
        self.server.seen.append(entry)
        return entry

    def do_GET(self) -> None:
        req = self._record()
        if self.path == "/health":
            self._reply(200, b'{"components":{"db_pings":"ok"},"status":"ok"}')
        elif self.path == "/openai/v1/models" and req["authorization"] == f"Bearer {KEY}":
            self._reply(200, json.dumps(dict(object="", data=[dict(id=MODEL, object="model")])).encode())
        else:
            self._reply(401 if self.path == "/openai/v1/models" else 404, b'{"error":{"message":"no"}}')

    def do_POST(self) -> None:
        req = self._record()
        if self.path != "/openai/v1/responses" or req["authorization"] != f"Bearer {KEY}":
            return self._reply(401, b'{"error":{"message":"virtual key is required"}}')
        n = sum(r["path"] == self.path for r in self.server.seen)
        response = dict(id=f"resp_{n}", object="response", created_at=0, model=req["body"]["model"], status="completed",
                        output=[SCRIPT[n - 1]], parallel_tool_calls=True, tool_choice="auto", tools=[],
                        usage=dict(input_tokens=100 * n, output_tokens=10, total_tokens=100 * n + 10,
                                   input_tokens_details=dict(cached_tokens=0), output_tokens_details=dict(reasoning_tokens=0)))
        events = [dict(type="response.created", sequence_number=0, response=dict(response, status="in_progress", output=[])),
                  dict(type="response.completed", sequence_number=1, response=response)]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        self._reply(200, sse.encode(), "text/event-stream")


@pytest.fixture
def gateway():
    gw = Gateway()
    yield gw
    gw.shutdown()
    gw.server_close()


async def test_run_goes_through_the_gateway(make_client, gateway, secrets_in_env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")                       # nothing may depend on an OpenAI key any more
    configs = []
    from agents import Runner
    original = Runner.run_streamed
    monkeypatch.setattr(Runner, "run_streamed", lambda *a, **k: configs.append(k["run_config"]) or original(*a, **k))
    async with make_client(False, model=MODEL, bifrost_url=gateway.url, bifrost_api_key=KEY, tracing=True) as c:
        fid = await upload(c)
        r = await c.post("/ask", json={"file_ids": [fid], "questions": ["How many doors are there?"]})
        assert r.status_code == 200, r.text
        body = r.json()
        health = await c.get("/health")
    assert body["answers"][0]["answer"] == 7 and body["answers"][0]["evidence"][0]["bbox_px"] == [1, 2, 3, 4]
    assert body["usage"] == dict(requests=3, input_tokens=600, output_tokens=30, total_tokens=630)   # as the gateway reported

    calls = [r for r in gateway.seen if r["method"] == "POST"]
    assert [c["path"] for c in calls] == ["/openai/v1/responses"] * 3            # the Responses API, at Bifrost's base path
    assert all(c["body"]["model"] == MODEL for c in calls)                       # "provider/model" reached it unmodified
    assert all(c["authorization"] == f"Bearer {KEY}" for c in calls)             # with the gateway key, not an OpenAI key
    first, last = calls[0]["body"], calls[-1]["body"]
    assert first["stream"] is True and [t["name"] for t in first["tools"]] == ["shell", "view_image"]
    assert "Counting conventions" in first["instructions"]
    fmt = first["text"]["format"]                                                # the structured final answer
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and "answers" in fmt["schema"]["properties"]
    # the image tool output travels as an input_image part of the function_call_output
    out = [i for i in last["input"] if i.get("type") == "function_call_output" and i["call_id"] == "call_2"][0]["output"]
    assert [p["type"] for p in out] == ["input_text", "input_image"] and out[1]["image_url"].startswith("data:image/png;base64,")
    shell_out = [i for i in last["input"] if i.get("type") == "function_call_output" and i["call_id"] == "call_1"][0]["output"]
    assert "render.png" in shell_out and SECRET not in shell_out and KEY not in shell_out and "BIFROST" not in shell_out

    assert configs[0].tracing_disabled is True                 # asked for, but there is no OpenAI key to export with
    h = health.json()["model"]
    assert h == dict(name=MODEL, provider="bifrost", key_present=True,
                     gateway=dict(url=gateway.url, reachable=True, model_known=True))
    assert KEY not in health.text and SECRET not in health.text


async def test_health_reports_gateway_problems_without_leaking(make_client, gateway):
    wrong_key = dict(model=MODEL, bifrost_url=gateway.url, bifrost_api_key="sk-bf-wrong")
    async with make_client(False, **wrong_key) as c:                      # model list refused: unknown, not "absent"
        h = (await c.get("/health")).json()["model"]["gateway"]
        assert h == dict(url=gateway.url, reachable=True, model_known=None)
        fid = await upload(c)                                             # and a run fails cleanly, as an error run
        r = await c.post("/ask", json={"file_ids": [fid], "questions": ["q"]})
        assert r.status_code == 502 and "AuthenticationError" in r.json()["error"] and "sk-bf-wrong" not in r.text
    async with make_client(False, model="anthropic/other", bifrost_url=gateway.url, bifrost_api_key=KEY) as c:
        assert (await c.get("/health")).json()["model"]["gateway"]["model_known"] is False
    gateway.shutdown()
    gateway.server_close()
    url = gateway.url.replace("http://", "http://user:pass@")
    async with make_client(False, model=MODEL, bifrost_url=url, bifrost_api_key=KEY) as c:
        r = await c.get("/health")
        assert r.json()["model"]["gateway"] == dict(url=gateway.url, reachable=False, model_known=None)
        assert "pass" not in r.text and KEY not in r.text


async def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("JUNCTION_BIFROST_URL", "http://bifrost:8080/")
    monkeypatch.setenv("JUNCTION_BIFROST_API_KEY", KEY)
    monkeypatch.setenv("JUNCTION_MODEL", MODEL)
    monkeypatch.setenv("JUNCTION_API_TOKEN", "tok")
    s = Settings.from_env()
    assert (s.model, s.bifrost_url, s.bifrost_api_base) == (MODEL, "http://bifrost:8080", "http://bifrost:8080/openai/v1")
    assert KEY not in repr(s) and "tok" not in repr(s)                    # settings can be logged
    monkeypatch.setenv("JUNCTION_BIFROST_URL", "https://gw.example/bifrost/openai/v1")
    assert Settings.from_env().bifrost_api_base == "https://gw.example/bifrost/openai/v1"
    monkeypatch.delenv("JUNCTION_BIFROST_URL")
    assert Settings.from_env().bifrost_url is None and Settings.from_env().bifrost_api_base is None
