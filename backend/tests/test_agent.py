"""The recorded function-calling verification on gemini-3.1-flash-lite (2 live requests, P1),
replayed through GeminiClient with FakeTransport."""

from __future__ import annotations

import json
import re

import pytest

from aura import tools
from aura.budget import Budget
from aura.config import Settings
from aura.gemini.client import FakeTransport, GeminiClient
from tests.conftest import FIXTURES, FRONTEND_DRAWING

SYSTEM = ("You answer questions about one floor plan by calling tools. Never write a number "
          "yourself; every number must come from a tool result. Finish with final_answer.")


@pytest.fixture
def client():
    s = Settings()
    return GeminiClient(s, Budget(s), FakeTransport(FIXTURES / "gemini"))


async def test_flash_lite_function_calling_round_trip(client):
    d = tools.load_file(FRONTEND_DRAWING)
    decl = [{"functionDeclarations": tools.TOOL_DECLARATIONS}]
    contents = [{"role": "user", "parts": [{"text": "how many doors does apartment B have?"}]}]
    g1 = await client.generate(contents, tools=decl, purpose="agent", bucket="dev", system=SYSTEM)
    assert [(f.name, f.args) for f in g1.function_calls] == [
        ("count_objects", {"subject": "door", "scope": "unit:B"})]
    assert g1.model == "gemini-3.1-flash-lite" and g1.input_tokens > 0

    fc = g1.function_calls[0]
    tr = tools.count_objects(d, fc.args["subject"], tools.parse_scope_arg(fc.args["scope"]))
    assert tr.value == 9
    contents += [g1.content, {"role": "user", "parts": [{"functionResponse": {
        "name": fc.name,
        "response": {"result_id": tr.result_id, "value": tr.value, "summary": tr.summary}}}]}]
    g2 = await client.generate(contents, tools=decl, purpose="agent", bucket="dev", system=SYSTEM)
    (final,) = g2.function_calls
    assert final.name == "final_answer" and final.args["result_id"] == tr.result_id
    # Every number in the model's explanation is in the tool result.
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", final.args["explanation"])]
    assert nums and all(n in [*tr.numbers, tr.value] for n in nums)
    assert client.budget.event_used() == 41 + 2


def test_fixtures_hold_no_image_bytes_or_key():
    for f in (FIXTURES / "gemini").glob("*.json"):
        text = f.read_text()
        fx = json.loads(text)
        assert fx["source"] in ("recorded", "derived", "synthetic"), f.name
        assert "x-goog-api-key" not in text.lower() and "AIza" not in text
        for part in fx["request"].get("input", []):
            if part.get("type") == "image":
                assert part["data"].startswith("sha256:")
