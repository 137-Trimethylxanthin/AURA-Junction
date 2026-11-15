"""The raster of an image plan (the library thumbnail) shows its walls, with the rooms inside them left open."""
import io

from PIL import Image
from shapely.geometry import Polygon, box

from jstore.service import aura_model as am


def test_plan_png_fills_the_walls_and_leaves_their_holes_open():
    base = am.Base(file_id="f", page_no=0, format="png", name=None, image="p.png", w=400, h=400,
                   to_image=None, frame=None, frame_method=None, registered=False)
    ring = Polygon(box(10, 10, 90, 90).exterior.coords, [box(20, 20, 80, 80).exterior.coords])
    m = am.Model(key="k", id="d", files=[], base=base, walls=[ring, box(45, 45, 55, 55)])
    im = Image.open(io.BytesIO(am.plan_png(m, 200))).convert("RGB")
    dark = lambda p: sum(im.getpixel(p)) < 300  # noqa: E731
    assert dark((30, 30)) and dark((100, 100))          # the wall ring, and the island inside its hole
    assert not dark((60, 60)) and not dark((6, 6))      # the open space inside the ring; outside the plan
    plain = am.plan_png(am.Model(key="k", id="d", files=[], base=base), 200)
    assert not any(sum(px) < 300 for px in Image.open(io.BytesIO(plain)).convert("RGB").getdata())
