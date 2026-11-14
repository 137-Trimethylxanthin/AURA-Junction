"""The scan room outline: snapping, confidence and switches (no network: the Gemini answer is scripted)."""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from jstore.interpret import raster_rooms as RR
from jstore.interpret import room_outline as RO


def _plan() -> Image.Image:
    im = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(im).rectangle((100, 80, 300, 220), outline="black", width=3)      # line centre 1 px inside
    return im


def test_snap_moves_edges_onto_the_drawn_lines():
    plan = RO.Plan(_plan())
    rough = [(104, 84), (297, 84), (297, 223), (104, 223)]
    out, support = RO.snap(RO.simplify(rough, 1.0), plan, 0.02)
    assert support == pytest.approx(1.0)
    assert Polygon(out).area == pytest.approx(198 * 138, rel=0.02)
    assert RO.iou(out, [(101, 81), (299, 81), (299, 219), (101, 219)]) > 0.97


def test_outline_rooms_uses_the_answer_and_respects_the_budget(monkeypatch):
    asked = []

    def answer(view, prompt, stats, salt=""):
        asked.append(prompt)
        return {"polygon": [[193, 236], [806, 236], [806, 760], [193, 760]]}    # 0-1000 of the cut-out
    monkeypatch.setattr(RO, "generate", answer)
    room = dict(label="Bad", number="1", label_box=[180, 140, 220, 160], hint=[(100, 80), (300, 80), (300, 220), (100, 220)])
    log: dict = {}
    res = RO.outline_rooms(_plan(), [room], 0.02, 30, log)
    assert len(asked) == 1 and "'Bad 1'" in asked[0]
    assert res[0]["polygon"] is not None and res[0]["label_inside"] and not res[0]["touches_crop_border"]
    assert RO.iou(res[0]["polygon"], room["hint"]) > 0.9 and log["polygons"] == 1 and not log["budget_ended"]
    res = RO.outline_rooms(_plan(), [room], 0.02, 0, log)                              # no time: nothing is asked
    assert len(asked) == 1 and res[0]["polygon"] is None and res[0]["reason"] == "budget" and log["budget_ended"]


def test_confidence_follows_the_signals(monkeypatch):
    g = lambda iou, flags=(): dict(gem=dict(iou=iou, flags=list(flags)))        # noqa: E731
    assert RR.outline_confidence(g(0.95)) == 0.9
    assert RR.outline_confidence(g(0.85)) == RR.outline_confidence(g(0.5)) == RR.outline_confidence(g(0.95, ["label_outside"])) == 0.7
    assert RR.outline_confidence(g(None)) == 0.6
    for flag in ("other_label_inside", "open_edge", "overlaps_neighbour", "touches_crop_border"):
        assert RR.outline_confidence(g(0.95, [flag])) == 0.5
    assert RR.outline_confidence({}) == 0.4
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert RR.outline_source() == "segmentation"
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert RR.outline_source() == "gemini"
    monkeypatch.setenv("JSTORE_ROOM_OUTLINE", "segmentation")
    assert RR.outline_source() == "segmentation"


def test_open_edge_and_overlap_are_seen():
    im = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(im)
    d.line([(250, 80), (100, 80), (100, 220), (250, 220)], fill="black", width=3)     # a room open to the right
    plan = RO.Plan(im)
    half = [(101, 81), (200, 81), (200, 219), (101, 219)]                              # cut at x=200, where nothing is drawn
    assert RO.open_length(half, plan, [], 0.02) == pytest.approx(138 * 0.02, rel=0.05)
    neighbour = Polygon([(200, 81), (300, 81), (300, 219), (200, 219)])                # ... unless another room follows
    assert RO.open_length(half, plan, [neighbour], 0.02) == 0
    assert RO.open_length([(101, 81), (299, 81), (299, 219), (101, 219)], RO.Plan(_plan()), [], 0.02) == 0   # every edge on a line
    assert RO.overlap_share(Polygon(half), [Polygon([(150, 81), (300, 81), (300, 219), (150, 219)])]) == pytest.approx(0.505, abs=0.01)


def test_answer_stays_inside_the_cut_out():
    pts = RO.to_px({"polygon": [[-50, 0], [1400, 0], [1400, 1000], [0, 1200]]}, (100, 100, 300, 200))
    assert pts == [(100, 100), (300, 100), (300, 200), (100, 200)]


def test_scale_from_printed_areas_uses_the_gemini_polygons():
    def room(area, side, seg_side):
        return dict(area=area, outline="wall_segmentation", px=seg_side * seg_side,
                    gem=dict(poly=Polygon([(0, 0), (side, 0), (side, side), (0, side)]), flags=[]))
    rooms = [room(16.0, 400, 380), room(9.0, 300, 290), room(25.0, 500, 470)]          # regions run small
    got = RR.scale_from_printed(rooms, 1.0)
    assert got["outlines"] == "gemini_polygon" and got["mpp"] == pytest.approx(0.01) and got["spread"] == 0
    for r in rooms:
        del r["gem"]
    got = RR.scale_from_printed(rooms, 1.0)
    assert got["outlines"] == "wall_segmentation" and got["mpp"] > 0.0102


def test_scale_bar_is_measured_without_the_unit_written_next_to_it():
    import numpy as np
    im = Image.new("L", (1000, 100), 255)
    d = ImageDraw.Draw(im)
    d.line([(100, 50), (800, 50)], fill=0, width=2)
    for x in range(100, 801, 100):
        d.line([(x, 42), (x, 58)], fill=0, width=2)
    d.rectangle((812, 44, 830, 56), fill=0)                                            # "m" right of the end
    assert RR._bar_px(np.asarray(im), [100, 40, 800, 60], 1.0) == pytest.approx(701, abs=3)
