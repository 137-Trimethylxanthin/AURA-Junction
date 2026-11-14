"""Pricing and metering of the drawing store's ingestion calls (aura/ingest_cost.py)."""

from __future__ import annotations

import json
import urllib.request

import pytest

from aura import ingest_cost as ic
from aura.budget import PRICES

GEN = "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-carbon:generateContent"
DEC = "https://generativelanguage.googleapis.com/v1beta/decisions"


def test_usage_of_generate_content_bills_thinking_as_output():
    body = json.dumps({"usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200,
                                         "thoughtsTokenCount": 300, "totalTokenCount": 1500}}).encode()
    assert ic.usage_of(GEN, None, 200, body) == ("gemini-4-carbon", 1000, 500)


def test_usage_of_decisions_reads_model_from_request_and_input_tokens():
    req = json.dumps({"model": "gemini-decision-1", "questions": {}}).encode()
    body = json.dumps({"answers": {}, "usage": {"total_input_tokens": 1286}}).encode()
    assert ic.usage_of(DEC, req, 200, body) == ("gemini-decision-1", 1286, 0)


def test_usage_of_errors_and_other_hosts():
    assert ic.usage_of(GEN, None, 429, b'{"error": {}}') == ("gemini-4-carbon", 0, 0)
    assert ic.usage_of("https://example.com/x", None, 200, b"{}") is None


def test_price_uses_the_budget_price_table():
    led = {"gemini-4-carbon": {"calls": 3, "input_tokens": 1_000_000, "output_tokens": 100_000},
           "gemini-decision-1": {"calls": 2, "input_tokens": 2_000_000, "output_tokens": 0}}
    p = ic.price(led)
    pin, pout = PRICES["gemini-4-carbon"]
    dpin, _ = PRICES["gemini-decision-1"]
    assert p["calls"] == 5 and p["input_tokens"] == 3_000_000 and p["output_tokens"] == 100_000
    assert p["cost_usd"] == pytest.approx(pin + pout * 0.1 + 2 * dpin)
    assert p["by_model"]["gemini-4-carbon"]["cost_usd"] == pytest.approx(pin + pout * 0.1)


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status = body, status

    def read(self, *a):
        return self.body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *e):
        pass


def _job(key: str, n: int) -> str:
    """Stands in for jstore: Gemini calls through urllib from inside the job."""
    for _ in range(n):
        req = urllib.request.Request(GEN, data=b"{}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["usageMetadata"]
    with urllib.request.urlopen("https://example.com/other") as r:  # not metered
        r.read()
    return "done"


def test_metered_call_writes_a_ledger_per_job_and_accumulates_reruns(tmp_path, monkeypatch):
    usage = json.dumps({"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10,
                                          "thoughtsTokenCount": 5}}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, *a, **k: _Resp(usage))
    monkeypatch.setattr(ic, "_installed", False)
    monkeypatch.setenv("JSTORE_DATA_DIR", str(tmp_path))
    assert ic.metered_call(_job, ("file-1",), {"n": 2}) == "done"
    assert ic.metered_call(_job, ("file-1",), {"n": 1}) == "done"  # a retry adds to the same file
    ic.metered_call(_job, ("key-1",), {"n": 1})
    spent = ic.spent_for("file-1", "key-1")
    assert spent["calls"] == 4 and spent["input_tokens"] == 400 and spent["output_tokens"] == 60
    assert spent["cost_usd"] == pytest.approx(ic.cost_usd("gemini-4-carbon", 400, 60))
    assert ic.totals() == {"ingest_calls_total": 4, "ingest_cost_usd_total": spent["cost_usd"]}
    # outside a job nothing is recorded
    _job("x", 1)
    assert ic.spent_for("file-1", "key-1")["calls"] == 4
