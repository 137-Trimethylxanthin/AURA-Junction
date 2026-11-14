"""Double / single doors decided by the decision model on door crops (aura.door_leaves), stubbed model."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from aura import door_leaves, steps, tools
from aura.config import Settings
from aura.gemini.client import GeminiError, Meter
from tests.conftest import FRONTEND_DRAWING
from tests.test_steps import FakeClient, validated


class LeafClient(FakeClient):
    """Planner turns as FakeClient, plus a stub Decisions API that answers per door id."""

    def __init__(self, turns, verdict, fail: bool = False):
        super().__init__(turns)
        self.verdict = verdict  # door box -> (choice, confidence)
        self.decision_calls: list[int] = []
        self.fail = fail

    async def decisions(self, parts, questions, purpose, *, meter=None, **kw):
        if meter is not None:
            meter.add(SimpleNamespace(cost_usd=0.001, input_tokens=1000, output_tokens=0))
        self.decision_calls.append(len(questions))
        if self.fail:
            raise GeminiError(503, "unavailable")
        boxes = self.batch_boxes.pop(0)
        answers = {}
        for n, b in enumerate(boxes, 1):
            c, conf = self.verdict(b)
            answers[f"d{n:02d}"] = {"choice": c, "confidence": conf, "probabilities": {c: conf}}
        return SimpleNamespace(choice=lambda k: (answers[k]["choice"], answers[k]["confidence"],
                                                 answers[k]["probabilities"]),
                               model="gemini-decision-1", latency_ms=7, cost_usd=0.001)


@pytest.fixture
def d(tmp_path, monkeypatch):
    base = tools.load_file(FRONTEND_DRAWING)
    Image.new("RGB", (1200, 900), "white").save(tmp_path / "original.png")
    door_leaves._MEM.clear()
    return dataclasses.replace(base, dir=tmp_path)


@pytest.fixture(autouse=True)
def record_batches(monkeypatch):
    """The stub sees which boxes went into each request, in order."""
    real = door_leaves._ask_batch

    async def spy(client, im, batch, meter, caller):
        if hasattr(client, "batch_boxes"):
            client.batch_boxes.append([b for b, _ids in batch])
        return await real(client, im, batch, meter, caller)

    monkeypatch.setattr(door_leaves, "_ask_batch", spy)


def doors(d):
    return steps.Toolbox(d).boxes["door"]


def expect(d, v, verdict):
    """Door ids whose crop (shared by a door drawn twice) got this verdict."""
    return {i for b, ids in door_leaves.groups_of(doors(d)) if v(b)[0] == verdict for i in ids}


def every_third_double(d):
    ids = [b.id for b in doors(d)]
    return lambda b: ("double", 0.9) if ids.index(b.id) % 3 == 0 else ("single", 0.8)


async def ask(d, client, question):
    client.batch_boxes = []
    stats = {"meter": Meter(), "route": "template"}
    svc = SimpleNamespace(client=client, settings=Settings())
    built = await steps.answer_with_tools(svc, d, question, "en", SimpleNamespace(ip_hash=None, presenter=False),
                                          stats)
    return built, stats


def turns(double=True):
    return [[("list_openings", {"kind": "door", **({"double": True} if double else {}), "finish_as": "count"})]]


async def test_double_doors_are_the_doors_the_model_sees_two_leaves_in(d):
    v = every_third_double(d)
    client = LeafClient(turns(), v)
    built, _stats = await ask(d, client, "How many double doors are there?")
    body = validated(built, _stats)
    want = expect(d, v, "double")
    assert body["answer"] == {"kind": "count", "value": len(want), "subject": "door"}
    assert {b["id"] for b in body["evidence"]["boxes"]} == want  # highlighted
    assert body["method"] == "agent_estimate" and body["estimate"]["confidence"] == 0.9
    nodes = [n for n in body["trace"] if n["id"].startswith("leaves-")]
    assert nodes and all(n["kind"] == "model" for n in nodes)
    assert sum(n["detail"]["crops"] for n in nodes) == len(door_leaves.groups_of(doors(d)))
    assert len(client.decision_calls) == len(nodes) <= door_leaves.MAX_CALLS
    assert all(n <= door_leaves.BATCH for n in client.decision_calls)


async def test_second_question_is_served_from_the_cache(d):
    client = LeafClient(turns(), every_third_double(d))
    await ask(d, client, "How many double doors?")
    sent = len(client.decision_calls)
    door_leaves._MEM.clear()  # the file in the drawing folder alone must serve it
    client2 = LeafClient(turns(), lambda b: pytest.fail("no model call expected"))
    built, _stats = await ask(d, client2, "How many double doors?")
    assert client2.decision_calls == [] and sent >= 1
    rows = json.loads((d.dir / door_leaves.CACHE_FILE).read_text())["doors"]
    assert all(r["key"].startswith(f"{d.id}:{i}:") for i, r in rows.items())
    assert built.body["answer"]["value"] == sum(1 for r in rows.values() if r["verdict"] == "double")


async def test_unclear_doors_are_reported_not_counted(d):
    first = doors(d)[0].id
    client = LeafClient(turns(), lambda b: ("unclear", 0.4) if b.id == first else ("double", 0.95))
    built, _stats = await ask(d, client, "How many double doors?")
    ids = {b["id"] for b in built.body["evidence"]["boxes"]}
    assert first not in ids
    assert "unclear, not counted" in built.body["explanation"] and first in built.body["explanation"]


async def test_single_doors_from_the_question_words(d):
    v = every_third_double(d)
    client = LeafClient(turns(double=False), v)
    built, _stats = await ask(d, client, "Wie viele einflügelige Türen gibt es?")
    want = expect(d, v, "single")
    assert {b["id"] for b in built.body["evidence"]["boxes"]} == want


async def test_no_page_image_falls_back_to_the_width_rule(d):
    (d.dir / "original.png").unlink()
    client = LeafClient(turns(), lambda b: pytest.fail("no model call expected"))
    built, _stats = await ask(d, client, "How many double doors?")
    tb = steps.Toolbox(d)
    want = {b.id for b in tb.boxes["door"] if (tb.width_m[b.id] or 0) >= steps.DOUBLE_DOOR_M}
    assert {b["id"] for b in built.body["evidence"]["boxes"]} == want
    node = next(n for n in built.body["trace"] if n["id"].endswith("-fallback"))
    assert "no page image" in node["body"] and built.body["method"] == "cad_geometry"


async def test_failed_request_falls_back_to_the_width_rule(d):
    client = LeafClient(turns(), lambda b: ("double", 0.9), fail=True)
    built, _stats = await ask(d, client, "How many double doors?")
    assert any(n["id"].endswith("-fallback") for n in built.body["trace"])
    assert "width rule" in built.body["explanation"]


async def test_call_cap_leaves_the_rest_unclear(d, monkeypatch):
    monkeypatch.setattr(door_leaves, "BATCH", 2)
    monkeypatch.setattr(door_leaves, "MAX_CALLS", 2)
    client = LeafClient(turns(), lambda b: ("double", 0.9))
    built, _stats = await ask(d, client, "How many double doors?")
    assert len(client.decision_calls) == 2
    assert built.body["answer"]["value"] <= 4 < len(doors(d))
    assert "unclear, not counted" in built.body["explanation"]


@pytest.mark.parametrize("q,want", [
    ("How many double doors?", None),  # the planner's double flag decides 'double'
    ("How many single doors are there?", "single"),
    ("Combien de portes simples ?", "single"),
    ("Kuinka monta yksilehtistä ovea?", "single"),
    ("Quante porte singole ci sono?", "single"),
    ("Wie viele Einzeltüren?", "single"),
    ("How many doors?", None),
])
def test_single_words_in_five_languages(q, want):
    assert door_leaves.wanted({"kind": "door"}, q) == want
    assert door_leaves.wanted({"kind": "door", "double": True}, q) == "double"
    assert door_leaves.wanted({"kind": "window", "double": True}, q) is None


def test_crop_is_square_and_marks_the_door():
    im = Image.new("RGB", (1000, 500), "white")
    png = door_leaves.make_crop(im, SimpleNamespace(x=95.0, y=1.0, w=4.0, h=2.0))  # at the border
    tile = Image.open(__import__("io").BytesIO(png))
    assert tile.size == (door_leaves.TILE_PX, door_leaves.TILE_PX)
    assert door_leaves.MAGENTA in {c for _n, c in tile.convert("RGB").getcolors(1 << 16)}


async def test_low_confidence_leaf_count_is_unclear(d):
    first = door_leaves.groups_of(doors(d))[0]
    client = LeafClient(turns(), lambda b: ("double", 0.7) if b.id == first[0].id else ("double", 0.95))
    built, _stats = await ask(d, client, "How many double doors?")
    assert not set(first[1]) & {b["id"] for b in built.body["evidence"]["boxes"]}
    assert "unclear, not counted" in built.body["explanation"]


async def test_cached_verdicts_show_a_cache_node(d):
    await ask(d, LeafClient(turns(), lambda b: ("double", 0.9)), "How many double doors?")
    built, _stats = await ask(d, LeafClient(turns(), lambda b: pytest.fail("cached")), "How many double doors?")
    node = next(n for n in built.body["trace"] if n["id"].endswith("-cache"))
    assert "0 calls" in node["body"]
