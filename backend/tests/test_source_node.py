"""The 'Pick the best source' trace node follows the evidence the answer really used."""

from __future__ import annotations

from aura import answer as A
from aura import tools
from tests.conftest import FRONTEND_DRAWING


def drawing(**extra):
    base = tools.load_file(FRONTEND_DRAWING).drawing
    data = {**base, **extra}
    return tools.LoadedDrawing(id=data["id"], dir=None, drawing=data, engine=tools.BundleEngine(data))


def source(built):
    return next(n for n in built.body["trace"] if n["id"] == "source")


STORE = {"evidence_method": {"room": "pdf_vector", "door": "vision"}, "source_ext": ".pdf"}


def test_cad_drawing_keeps_the_cad_wording():
    d = drawing()
    n = source(A.count("q", "en", d, tools.count_objects(d, "door"), {"kind": "floor"}, None))
    assert "CAD geometry" in n["body"] and n["kind"] == "exact"


def test_pdf_vector_rooms_never_claim_cad_geometry():
    d = drawing(**STORE)
    tr = tools.count_objects(d, "room")
    assert tr.method == "pdf_vector"
    for built in (A.count("q", "en", d, tr, {"kind": "floor"}, None),
                  A.area("q", "en", d, tools.room_areas(d), {"kind": "floor"}, None)):
        n = source(built)
        assert "CAD" not in n["body"] and "vector" in n["body"] and n["kind"] == "exact"


def test_store_detected_doors_are_an_estimate():
    d = drawing(**STORE)
    tr = tools.count_objects(d, "door")
    assert tr.method == "vision"
    n = source(A.count("q", "en", d, tr, {"kind": "floor"}, None))
    assert n["kind"] == "model" and "estimate" in n["body"] and "CAD" not in n["body"]


def test_ifc_wording():
    d = drawing(kind="ifc")
    n = source(A.count("q", "en", d, tools.count_objects(d, "door"), {"kind": "floor"}, None))
    assert "IFC" in n["body"] and "CAD" not in n["body"]
