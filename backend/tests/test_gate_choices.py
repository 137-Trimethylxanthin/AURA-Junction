"""Choice questions with fewer than 2 options are never sent (Decisions API: HTTP 400)."""
from __future__ import annotations

import pytest

from aura.gemini.understand import build_request, decide_hybrid_route


class _Res:
    cost_usd = 0.0
    input_tokens = 0
    latency_ms = 0
    model = "gemini-decision-1"

    def noul(self, k):
        return 0.9

    def choice(self, k):
        return None, 0.0, {}


class _Client:
    def __init__(self):
        self.sent: list[dict] = []

    async def decisions(self, parts, questions, purpose, **kw):
        self.sent.append(questions)
        return _Res()


@pytest.mark.parametrize(("units", "labels", "has_scope"), [([], [], False), (["A"], [], True),
                                                            ([], ["Bad"], True)])
def test_scope_question_needs_two_options(units, labels, has_scope):
    _, questions, _ = build_request("how many doors", units, labels, None)
    assert ("scope" in questions) is has_scope
    assert all(len(q["criteria"]) >= 2 for q in questions.values() if q["type"] == "choice")


@pytest.mark.parametrize(("units", "labels"), [([], []), ([], ["only one label"])])
async def test_hybrid_gate_never_sends_a_one_option_choice(units, labels):
    c = _Client()
    hd = await decide_hybrid_route(c, "how many doors", drawing_kind="raster", units=units,
                                   labels=labels if len(labels) != 1 else [], image_path=None)
    for q in c.sent:
        assert all(len(v["criteria"]) >= 2 for v in q.values() if v["type"] == "choice")
    assert hd.scope is None
