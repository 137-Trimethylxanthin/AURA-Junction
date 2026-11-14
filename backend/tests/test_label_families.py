"""Numbered room labels of one type ("BEDROOM #1", "BEDROOM #2", common on US plans) form a label family."""
from __future__ import annotations

from aura.router import DrawingCtx, label_base, label_hit, route

CTX = DrawingCtx(labels=["BEDROOM #1", "BEDROOM #2", "BATH", "KITCHEN", "LIVING", "BACKYARD"], kind="raster")


def test_label_base_strips_numbering():
    assert label_base("BEDROOM #2") == "bedroom"
    assert label_base("Bath 1") == "bath"
    assert label_base("Zimmer") == "zimmer"


def test_label_hit_matches_family_but_not_other_labels():
    assert label_hit("BEDROOM #1", "BEDROOM")
    assert label_hit("Bad", "Bad")
    assert not label_hit("BATH", "BEDROOM")
    assert not label_hit("3.04", "Bad")


def test_room_type_questions_route_to_the_family_label():
    assert route("How many bedrooms are there?", CTX).scope == {"kind": "label", "label": "BEDROOM"}
    r = route("What is the total area of the bedrooms?", CTX)
    assert r.intent == "area" and r.scope == {"kind": "label", "label": "BEDROOM"}
    assert route("How many bathrooms are there?", CTX).scope == {"kind": "label", "label": "BATH"}


def test_project_router_context_merges_floor_labels(tmp_path):
    from aura import pipeline

    class D:
        def __init__(self, id_, labels):
            self.id, self.ctx, self.registry = id_, DrawingCtx(labels=labels, kind="raster"), None

    a, b = D("a", ["BEDROOM #1"]), D("b", ["KITCHEN"])
    reg = {"a": a, "b": b}

    class R:
        def get(self, i):
            return reg.get(i)

    a.registry = b.registry = R()
    ctx = pipeline._project_ctx(a, {"kind": "project", "project_id": "p", "drawing_ids": ["a", "b"]})
    assert ctx.labels == ["BEDROOM #1", "KITCHEN"]
    assert pipeline._project_ctx(a, None) is a.ctx
