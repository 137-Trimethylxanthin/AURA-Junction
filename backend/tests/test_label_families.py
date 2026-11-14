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


def test_area_of_a_named_thing_without_a_room_is_not_the_floor_total():
    ctx = DrawingCtx(labels=["Room", "K a"], kind="raster")
    for q in ("What is the gross floor area of the Lagerhalle?", "Wie groß ist die Lagerhalle?",
              "What is the area of the garage?"):
        r = route(q, ctx)
        assert r.intent == "unknown" and "named_subject" in r.matched, q
    for q in ("What is the total area?", "How big is the apartment?", "What is the area of this floor plan?",
              "What is the total net floor area of the building?"):
        assert route(q, ctx).intent == "area", q
    assert route("What is the area of the kitchen?", CTX).scope == {"kind": "label", "label": "KITCHEN"}


def test_numbered_label_named_without_hash():
    ctx = DrawingCtx(labels=["BEDROOM #1", "BEDROOM #2", "BATH"], kind="raster")
    assert route("What is the area of bedroom 2?", ctx).scope == {"kind": "label", "label": "BEDROOM #2"}
    assert route("area of bedroom no. 1", ctx).scope == {"kind": "label", "label": "BEDROOM #1"}
    assert route("What is the total area of all bedrooms?", ctx).scope == {"kind": "label", "label": "BEDROOM"}
