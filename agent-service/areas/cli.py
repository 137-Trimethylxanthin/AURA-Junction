"""junction-areas <file>: room areas of a floor plan (PDF, DXF, PNG/JPG) as JSON, using the frozen readers that
were verified on held-out drawings (areas/APPROACHES.md).  No model involved."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from areas import common  # noqa: E402

READERS = {".pdf": "areas.frozen.pdf_vector_final", ".dxf": "areas.frozen.dxf_generic_final",
           ".png": "areas.frozen.png_vec_v11", ".jpg": "areas.frozen.png_vec_v11", ".jpeg": "areas.frozen.png_vec_v11"}
KEEP = ("number", "label", "area", "unit", "printed", "bbox", "bbox_px", "conf")


def rooms_of(path):
    import importlib
    ext = os.path.splitext(path)[1].lower()
    if ext not in READERS:
        raise SystemExit(f"junction-areas: unsupported file type {ext!r} (supported: {', '.join(READERS)})")
    mod = importlib.import_module(READERS[ext])
    seen = {}
    real = common.answer_all

    def capture(rooms, queries):  # every reader ends by handing its room records to the shared answer logic
        seen["rooms"] = rooms
        return real(rooms, queries)

    common.answer_all = capture
    try:
        mod.solve(path, [])
    finally:
        common.answer_all = real
    return READERS[ext].rsplit(".", 1)[-1], seen.get("rooms") or []


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print("usage: junction-areas <floor plan .pdf|.dxf|.png|.jpg>")
        return
    if len(sys.argv) != 2:
        raise SystemExit("usage: junction-areas <floor plan .pdf|.dxf|.png|.jpg>")
    reader, rooms = rooms_of(sys.argv[1])
    out = []
    for r in rooms:
        d = {k: r.get(k) for k in KEEP if r.get(k) is not None}
        if "area" in d:
            d["area_m2"] = round(float(d.pop("area")), 2)
        if "unit" in d:
            d["apartment"] = d.pop("unit")
        d["printed"] = bool(r.get("printed"))
        d["area_source"] = "printed" if r.get("printed") else "measured outline"
        d["indoor"] = common.indoor(r)
        out.append(d)
    numbered = [r for r in out if r.get("number")]
    print(json.dumps(dict(reader=reader, rooms_found=len(out), numbered_rooms=len(numbered),
                          total_numbered_indoor_m2=round(sum(r.get("area_m2", 0) for r in numbered if r["indoor"]), 2),
                          rooms=out), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


if __name__ == "__main__":
    main()
