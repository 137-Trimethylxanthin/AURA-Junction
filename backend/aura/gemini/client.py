"""Gemini client: Decisions API and generateContent over httpx, every call through the budget.

Public interface (published to L1):

    client = GeminiClient(settings, budget, transport=None)   # transport defaults to HTTP
    res = await client.decisions(input_parts, questions, purpose, bucket="public", ...)
    res = await client.generate(contents, tools=None, purpose=..., bucket="public", ...)
    FakeTransport(fixtures_dir)   # replays recorded fixtures, raises on any unknown request

`input_parts` items: `text_part("...")` or `image_part(png_bytes, resolution="medium")`.
`contents` is the generateContent `contents` list (or a plain string for one user turn).

The key is sent only in the `x-goog-api-key` header; it is never logged, put in a URL or
written to a fixture. HTTP is refused under pytest unless AURA_LIVE=1.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from aura.budget import Bucket, Budget, BudgetError, cost_usd
from aura.config import Settings, live_allowed

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

__all__ = [
    "BudgetError",
    "DecisionsResult",
    "FakeTransport",
    "GeminiClient",
    "GeminiError",
    "GenerateResult",
    "HttpTransport",
    "UnknownFixture",
    "image_part",
    "request_key",
    "strip_images",
    "text_part",
]


log = logging.getLogger("aura.gemini")


class GeminiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Gemini HTTP {status}: {message}")
        self.status = status


class UnknownFixture(AssertionError):
    """FakeTransport got a request it has no recording for."""


# ---- request parts ----------------------------------------------------------------------


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(data: bytes, resolution: str = "medium", mime_type: str = "image/png") -> dict:
    return {
        "type": "image",
        "data": base64.b64encode(data).decode(),
        "mime_type": mime_type,
        "resolution": resolution,
    }


def strip_images(obj: Any) -> Any:
    """Copy of a request body with image bytes replaced by their sha256 prefix.

    Fixtures keep this form only: no image bytes ever land in the repo.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "data" and isinstance(v, str) and len(v) > 64:
                out[k] = "sha256:" + hashlib.sha256(v.encode()).hexdigest()[:16]
            else:
                out[k] = strip_images(v)
        return out
    if isinstance(obj, list):
        return [strip_images(x) for x in obj]
    return obj


def _key_view(path: str, body: dict) -> dict:
    """What identifies a request for replay: endpoint, model, text, question keys and wording,
    tool names, image count/resolution. Never image bytes (tiles re-render byte-differently)."""
    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            if o.get("type") == "image" or "inline_data" in o or "inlineData" in o:
                inner = o.get("inline_data") or o.get("inlineData") or o
                return {"image": inner.get("resolution", inner.get("mime_type", ""))}
            return {k: walk(v) for k, v in sorted(o.items()) if k not in {"generationConfig"}}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    return {"path": path, "body": walk(body)}


def request_key(path: str, body: dict) -> str:
    view = json.dumps(_key_view(path, body), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(view.encode()).hexdigest()[:24]


# ---- results ----------------------------------------------------------------------------


@dataclass
class DecisionsResult:
    answers: dict[str, dict]
    input_tokens: int
    cost_usd: float
    latency_ms: int
    model: str
    raw: dict = field(repr=False, default_factory=dict)

    def noul(self, key: str) -> float | None:
        a = self.answers.get(key) or {}
        v = a.get("noul")
        return float(v) if v is not None else None

    def choice(self, key: str) -> tuple[str | None, float, dict[str, float]]:
        a = self.answers.get(key) or {}
        return a.get("choice"), float(a.get("confidence") or 0.0), dict(a.get("probabilities") or {})

    def score(self, key: str) -> tuple[float | None, float]:
        a = self.answers.get(key) or {}
        v = a.get("score")
        return (float(v) if v is not None else None), float(a.get("confidence") or 0.0)


@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any]


@dataclass
class GenerateResult:
    text: str
    function_calls: list[FunctionCall]
    input_tokens: int
    output_tokens: int  # candidates + thinking (thinking is billed as output)
    cost_usd: float
    latency_ms: int
    model: str
    content: dict = field(default_factory=dict)  # the model turn, to append to `contents`
    raw: dict = field(repr=False, default_factory=dict)


# ---- transports -------------------------------------------------------------------------


class Transport(Protocol):
    async def post(self, path: str, body: dict) -> tuple[int, dict]: ...


class HttpTransport:
    def __init__(self, api_key: str, timeout_s: float = 120.0):
        if os.environ.get("PYTEST_CURRENT_TEST") and not live_allowed():
            raise RuntimeError("Live Gemini calls are disabled in tests (set AURA_LIVE=1).")
        self._key = api_key
        self._client = httpx.AsyncClient(base_url=API_BASE, timeout=timeout_s)

    async def post(self, path: str, body: dict) -> tuple[int, dict]:
        r = await self._client.post(
            path, json=body, headers={"x-goog-api-key": self._key, "content-type": "application/json"}
        )
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:500]}}
        return r.status_code, data

    async def aclose(self) -> None:
        await self._client.aclose()


class FakeTransport:
    """Replays fixtures from `fixtures_dir/*.json`.

    Fixture file: {"source": "recorded|derived|synthetic", "note": str, "path": str,
                   "request": <stripped body>, "response": <body>, "status": 200}
    The match key is `request_key(path, request)`; anything unknown raises UnknownFixture.
    """

    def __init__(self, fixtures_dir: str | Path, extra: list[dict] | None = None):
        self.fixtures: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        d = Path(fixtures_dir)
        files = sorted(d.glob("*.json")) if d.exists() else []
        for f in files:
            self.add(json.loads(f.read_text()), name=f.name)
        for fx in extra or []:
            self.add(fx)

    def add(self, fx: dict, name: str = "<inline>") -> None:
        fx = dict(fx)
        fx.setdefault("name", name)
        self.fixtures[request_key(fx["path"], fx["request"])] = fx

    async def post(self, path: str, body: dict) -> tuple[int, dict]:
        key = request_key(path, body)
        self.calls.append((path, strip_images(body)))
        fx = self.fixtures.get(key)
        if fx is None:
            summary = json.dumps(strip_images(body), ensure_ascii=False)[:400]
            raise UnknownFixture(f"No fixture for {path} key={key}: {summary}")
        return int(fx.get("status", 200)), fx["response"]


class RecordingTransport:
    """Wraps a live transport and writes each exchange as a fixture (image bytes stripped)."""

    def __init__(self, inner: Transport, out_dir: str | Path, note: str = ""):
        self.inner = inner
        self.out = Path(out_dir)
        self.note = note

    async def post(self, path: str, body: dict) -> tuple[int, dict]:
        status, data = await self.inner.post(path, body)
        self.out.mkdir(parents=True, exist_ok=True)
        key = request_key(path, body)
        fx = {
            "source": "recorded",
            "note": self.note,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "path": path,
            "request": strip_images(body),
            "status": status,
            "response": data,
        }
        (self.out / f"rec_{key}.json").write_text(json.dumps(fx, indent=1, ensure_ascii=False))
        return status, data


class Meter:
    """Counts every request actually sent (success, error or cancel) and its cost, so a
    response can report calls/cost truthfully even when the model step failed."""

    def __init__(self) -> None:
        self.calls = 0
        self.cost_usd = 0.0
        self.tokens = 0

    def add(self, rec) -> None:
        self.calls += 1
        self.cost_usd += rec.cost_usd
        self.tokens += rec.input_tokens + rec.output_tokens


# ---- client -----------------------------------------------------------------------------


class GeminiClient:
    def __init__(self, settings: Settings, budget: Budget, transport: Transport | None = None):
        self.s = settings
        self.budget = budget
        self._transport = transport

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            if self.s.gemini_api_key is None:
                raise BudgetError("models_off", "No GEMINI_API_KEY configured.")
            self._transport = HttpTransport(self.s.gemini_api_key.get_secret_value())
        return self._transport

    def available(self) -> bool:
        return self._transport is not None or self.s.gemini_api_key is not None

    async def _send(
        self,
        path: str,
        body: dict,
        *,
        model: str,
        purpose: str,
        bucket: Bucket,
        ip_hash: str | None,
        presenter: bool,
        meter: Meter | None = None,
    ) -> tuple[dict, int, int, int]:
        res = self.budget.reserve(purpose, bucket, ip_hash=ip_hash, presenter=presenter)
        try:
            transport = self.transport
        except BaseException:
            self.budget.release(res)
            raise
        t0 = time.perf_counter()
        try:
            status, data = await transport.post(path, body)
        except UnknownFixture:
            self.budget.release(res)
            raise
        except Exception as e:
            # Sent (or possibly sent): it counts. Network errors surface as GeminiError.
            rec = self.budget.commit(res, model=model, status=0,
                               latency_ms=int((time.perf_counter() - t0) * 1000))
            if meter is not None:
                meter.add(rec)
            raise GeminiError(0, f"{type(e).__name__}") from e
        except BaseException:  # cancelled (deadline): it still counts
            rec = self.budget.commit(res, model=model, status=0,
                               latency_ms=int((time.perf_counter() - t0) * 1000))
            if meter is not None:
                meter.add(rec)
            raise
        latency = int((time.perf_counter() - t0) * 1000)
        tin, tout = _usage(data)
        rec = self.budget.commit(res, model=model, input_tokens=tin, output_tokens=tout,
                                 latency_ms=latency, status=status)
        if meter is not None:
            meter.add(rec)
        if status != 200:
            msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            log.warning("gemini error %s: %s", status, str(msg)[:300])  # details in the log only
            raise GeminiError(status, "the model service returned an error")  # L7
        return data, tin, tout, latency

    async def decisions(
        self,
        input_parts: list[dict],
        questions: dict[str, dict],
        purpose: str,
        *,
        bucket: Bucket = "public",
        ip_hash: str | None = None,
        presenter: bool = False,
        model: str | None = None,
        meter: Meter | None = None,
    ) -> DecisionsResult:
        model = model or self.s.decision_model
        body = {"model": model, "input": input_parts, "questions": questions}
        data, tin, _tout, latency = await self._send(
            "/decisions", body, model=model, purpose=purpose, bucket=bucket,
            ip_hash=ip_hash, presenter=presenter, meter=meter,
        )
        return DecisionsResult(
            answers=dict(data.get("answers") or {}),
            input_tokens=tin,
            cost_usd=cost_usd(model, tin, 0),
            latency_ms=latency,
            model=model,
            raw=data,
        )

    async def generate(
        self,
        contents: list[dict] | str,
        tools: list[dict] | None = None,
        purpose: str = "agent",
        *,
        bucket: Bucket = "public",
        ip_hash: str | None = None,
        presenter: bool = False,
        model: str | None = None,
        system: str | None = None,
        config: dict | None = None,
        tool_config: dict | None = None,
        meter: Meter | None = None,
    ) -> GenerateResult:
        model = model or self.s.agent_model
        if isinstance(contents, str):
            contents = [{"role": "user", "parts": [{"text": contents}]}]
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = tools
        if tool_config:
            body["toolConfig"] = tool_config
        body["generationConfig"] = {"temperature": 0, **(config or {})}
        data, tin, tout, latency = await self._send(
            f"/models/{model}:generateContent", body, model=model, purpose=purpose,
            bucket=bucket, ip_hash=ip_hash, presenter=presenter, meter=meter,
        )
        cand = (data.get("candidates") or [{}])[0]
        content = cand.get("content") or {}
        texts: list[str] = []
        calls: list[FunctionCall] = []
        for p in content.get("parts") or []:
            if "text" in p and not p.get("thought"):
                texts.append(p["text"])
            fc = p.get("functionCall") or p.get("function_call")
            if fc:
                calls.append(FunctionCall(name=fc.get("name", ""), args=dict(fc.get("args") or {})))
        return GenerateResult(
            text="".join(texts),
            function_calls=calls,
            input_tokens=tin,
            output_tokens=tout,
            cost_usd=cost_usd(model, tin, tout),
            latency_ms=latency,
            model=model,
            content=content,
            raw=data,
        )


def _usage(data: dict) -> tuple[int, int]:
    if not isinstance(data, dict):
        return 0, 0
    u = data.get("usage")
    if isinstance(u, dict):  # Decisions API
        return int(u.get("total_input_tokens") or 0), int(u.get("total_output_tokens") or 0)
    m = data.get("usageMetadata")
    if isinstance(m, dict):  # generateContent
        out = int(m.get("candidatesTokenCount") or 0) + int(m.get("thoughtsTokenCount") or 0)
        return int(m.get("promptTokenCount") or 0), out
    return 0, 0
