"""'All N floors' scope: the net floor area is the sum of the floors' own sums; a model file without elements of a
class answers 0 for it, not 'unknown'."""
from __future__ import annotations

from aura import answer as A
from aura import tools
from aura.tools import BundleEngine, LoadedDrawing


class _Reg:
    def __init__(self, ds):
        self.ds = {d.id: d for d in ds}

    def get(self, i):
        return self.ds.get(i)


def _room(rid, name, area, outdoor=False):
    return {"room_id": rid, "num": rid, "name": name, "area_m2": area, "outdoor": outdoor,
            "points": [[10, 10], [20, 10], [20, 20], [10, 20]], "label_at": [15, 15]}


def _floor(did, rooms, kind="cad_dxf", evidence=None, **extra):
    doc = {"id": did, "kind": kind, "title": did, "rooms": rooms, "units": [], "evidence": evidence or {},
           "m_per_pct_x": 0.1, "m_per_pct_y": 0.1, **extra}
    return LoadedDrawing(id=did, dir=None, drawing=doc, engine=BundleEngine(doc))


def _group(*floors):
    reg = _Reg(floors)
    for f in floors:
        f.registry = reg
    return floors[0], {"kind": "project", "project_id": "g", "drawing_ids": [f.id for f in floors]}


def test_net_floor_area_across_floors_is_the_sum_of_each_floor():
    a = _floor("f-a", [_room("1", "Zimmer", 20.5), _room("2", "Küche", 10.0), _room("3", "Balkon", 6.0, outdoor=True)])
    b = _floor("f-b", [_room("1", "Zimmer", 30.25)])
    d, scope = _group(a, b)
    one_a = tools.room_areas(a, {"kind": "floor"}).value
    one_b = tools.room_areas(b, {"kind": "floor"}).value
    tr = tools.room_areas(d, scope)
    assert tr.kind == "area" and tr.value == round(one_a + one_b, 2) == 60.75
    assert {r.drawing_id for r in tr.rooms} == {"f-a", "f-b"}
    assert "f-a: 30.50 m²" in tr.summary and "f-b: 30.25 m²" in tr.summary
    body = A.area("What is the total floor area across all floors?", "en", d, tr, scope, None).body
    assert body["answer"]["kind"] == "area" and body["answer"]["value"] == 60.75


def test_a_floor_without_room_areas_is_named_and_left_out():
    a = _floor("f-a", [_room("1", "Zimmer", 20.0)])
    b = _floor("f-b", [])
    d, scope = _group(a, b)
    tr = tools.room_areas(d, scope | {"drawing_ids": ["f-a", "f-b", "gone"]})
    assert tr.value == 20.0 and tr.meta["floors_missing"] == ["f-b", "gone"]
    assert "Not included: f-b, gone" in tr.summary
    none = tools.room_areas(b, {"kind": "project", "project_id": "g", "drawing_ids": ["f-b"]})
    assert none.kind == "none" and not none.supported


def test_model_file_without_windows_counts_zero_not_unknown():
    door = {"id": "door-01", "x": 1, "y": 1, "w": 2, "h": 2, "subject": "door"}
    a = _floor("m--a", [], kind="ifc", evidence={"door": [door]}, evidence_method={"door": "cad_geometry"})
    b = _floor("m--b", [], kind="ifc", evidence={"door": [door]}, evidence_method={"door": "cad_geometry"})
    d, scope = _group(a, b)
    one = tools.count_objects(a, "window", {"kind": "floor"})
    assert one.kind == "count" and one.value == 0 and "contains no window elements" in one.summary
    tr = tools.count_objects(d, "window", scope)
    assert tr.kind == "count" and tr.value == 0 and tr.meta["none_in_model"]
    body = A.count("How many windows are there?", "en", d, tr, scope, None).body
    assert body["answer"] == {"kind": "count", "value": 0, "subject": "window"}
    assert "contains no window elements" in body["explanation"]
    assert tools.count_objects(d, "door", scope).value == 2


def test_a_plan_that_does_not_extract_windows_still_abstains():
    scan = _floor("s", [], kind="raster", evidence={"door": []})
    tr = tools.count_objects(scan, "window", {"kind": "floor"})
    assert tr.kind == "none" and not tr.supported
