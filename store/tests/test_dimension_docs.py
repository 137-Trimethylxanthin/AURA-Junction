"""What the drawing document says about printed dimensions and room sizes (pure parts of aura_model)."""
from types import SimpleNamespace

from jstore.service import aura_model as am


def test_room_size_is_printed_measured_or_an_estimate():
    stated, nominal = SimpleNamespace(frame_method="pixel_scale"), SimpleNamespace(frame_method="nominal_scale")
    row = dict(length_m=4.1, width_m=3.2, size_source="printed", size_text="3.20 x 4.10")
    assert am._room_size(row, stated) == dict(length_m=4.1, width_m=3.2, source="printed", text="3.20 x 4.10")
    row = dict(length_m=4.123456, width_m=3.2, size_source="measured_bounding_box", size_text=None)
    assert am._room_size(row, stated) == dict(length_m=4.123, width_m=3.2, source="measured")
    assert am._room_size(row, nominal)["source"] == "estimate"
    assert am._room_size(None, stated) is None and am._room_size(dict(length_m=None, width_m=None), stated) is None


def test_dimension_doc_carries_text_metres_box_and_line():
    it = dict(id="dim-01", entity_id=7, dim_kind="linear", label="12' - 6\"", value_m=3.81, dim_unit="ft-in",
              orientation="horizontal", a_m=None, b_m=None, room_id=None, page_no=0, line=[[10.0, 5.0], [30.0, 5.0]],
              box=dict(x=9.7, y=3.0, w=20.6, h=2.3))
    d = am.dimension_doc(it)
    assert d["text"] == "12' - 6\"" and d["value_m"] == 3.81 and d["store_ref"] == 7
    assert (d["x"], d["w"]) == (9.7, 20.6) and d["line"][1] == [30.0, 5.0]
    assert "x" not in am.dimension_doc(it | dict(box=None, line=None))   # on another page: cited, not drawn


def test_room_poly_exports_the_size():
    r = dict(room_id="a", num="1", name="K", unit=None, outdoor=False, points=[], label_at=[1, 1], area_source=None,
             area_m2=None, size=dict(length_m=4.0, width_m=3.0, source="measured"))
    assert am.room_poly(r)["size"]["width_m"] == 3.0
    r.pop("size")
    assert "size" not in am.room_poly(r)
