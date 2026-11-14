"""A refusal caused by the budget, a rate limit, presenter-only models or the kill switch is
temporary: the abstention it leads to is never stored in the answer cache."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from aura import answer as A
from aura import budget, pipeline
from aura.budget import BudgetError
from tests.test_agent_client import MockSidecarState, make_hybrid_app

Q = "what would it cost to repaint the corridor ceiling in teal?"


def test_refusals_are_seen_across_tasks_of_one_turn():
    async def turn():
        seen = budget.watch_refusals()

        async def step():
            try:
                raise BudgetError("rate_limited", "slow down")
            except BudgetError:
                pass  # swallowed, as a tool step does

        await asyncio.gather(step(), asyncio.create_task(step()))
        return seen

    assert asyncio.run(turn()) == ["rate_limited", "rate_limited"]
    assert budget._turn_refusals.get() is None  # nothing leaks out of the turn


@pytest.mark.parametrize("code", ["budget_exhausted", "rate_limited", "presenter_required", "models_off", None])
def test_abstention_after_a_swallowed_refusal_is_not_replayed(tmp_path, monkeypatch, code):
    calls = []

    async def tools_step(svc, d, question, lang, caller, stats, history=None, scope=None):
        calls.append(question)
        if code:
            try:
                raise BudgetError(code, "refused")
            except BudgetError:
                pass  # the step gives up quietly and abstains
        return A.unclear(question, lang, d, intent_tag="tools", look="The model step stopped.")

    monkeypatch.setattr(pipeline, "answer_with_tools", tools_step)
    with TestClient(make_hybrid_app(tmp_path, sidecar=MockSidecarState())) as c:
        svc = c.app.state.services if hasattr(c.app.state, "services") else None
        if svc is not None:
            monkeypatch.setattr(svc, "models_usable", lambda: True, raising=False)
        did = c.get("/drawings").json()["drawings"][0]["id"]
        first = c.post("/ask", json={"drawing_id": did, "question": Q}).json()
        assert calls and first["answer"]["kind"] == "abstain", first
        again = c.post("/ask", json={"drawing_id": did, "question": Q}).json()
        if code:
            assert again["route"] != "cache" and len(calls) == 2
        else:  # the control: an ordinary abstention is cached, so the check above means something
            assert again["route"] == "cache" and len(calls) == 1
