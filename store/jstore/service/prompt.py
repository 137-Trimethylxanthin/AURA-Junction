"""prompts/STORE.md, the schema document the model reads, generated from the live database so it cannot drift.

    python -m jstore.service.prompt          rewrite prompts/STORE.md
    python -m jstore.service.prompt --check  exit 1 when the file differs from what the database says

The prose lives in TEMPLATE; `{view}` placeholders become `q.view(column, column, ...)` with the live columns.
"""
from __future__ import annotations

import re
import sys

from ..db import DB
from .config import STORE_ROOT

PATH = STORE_ROOT / "prompts" / "STORE.md"

TEMPLATE = """\
# The store: schema q (PostgreSQL 17 + PostGIS)

Every view shows only the drawings of this run. Answer from these views whenever they cover the question.

## Layer 2: interpreted things (look here first)
One row per physical thing of a drawing, merged across its source files (DXF, PDF, image, IFC).
- {rooms} - `area_m2` is the NET floor area (the preferred source; `area_source` says which: printed, measured_outline, stored_quantity ...). `perimeter_m` is the length of the room outline (inner wall faces). `is_indoor` is false for balconies, terraces, loggias. `agreement`: agree | single | conflict between the `n_sources` files. `apartment` is the unit the room belongs to.
- {apartments} - `rooms` counts its rooms, `net_area_m2` sums its indoor rooms.
- {doors}; q.windows and q.fixtures have the same columns (all three are slices of q.items). `kind` tells fixtures apart (toilet, washbasin, sink, bathtub, shower ...); `room` is the number of the host room.
- {facts} - one row per entity and property with the preferred value and the agreement.
- {sources} - every value behind a fact, one row per source file.
- {entity_sources} - the Layer 1 primitives each entity was read from.
- {issues} - open conflicts and gaps. q.definitions - how each concept was derived per file.

Conventions
- Net area: "area", "floor area", "living area" mean sum(area_m2) over rooms with `is_indoor`; include outdoor rooms only when the question asks for them.
- Drawn twice: a thing drawn more than once has `is_duplicate` = true on the later copies. Counts and sums include every drawn copy (the drawing is the record); when copies exist, say in the answer how many of the total are duplicates.
- Scans and PDFs without CAD: doors, windows and fixtures there are detected on the page image (`attrs.score`, `attrs.page_no`); room areas are printed values where the sheet prints them, else measured from pixels with lower confidence. A sheet set that repeats a storey on several pages raises the issue `repeated_sheets`: count per page (`attrs->>'page_no'`) and say which page.
- Measured on a scan (image, or PDF without CAD): `perimeter_m` is always measured on an outline read from the pixels, and `area_m2` is too where `area_source` is measured_outline; both are approximate. Trust = `confidence` of the row in q.sources (outline x scale confidence): 0.7 and more = confirmed outline, almost always within 5 %; 0.45 to 0.7 = plausible, usually within a few percent; below 0.45 = an estimate, say so. A room with neither a printed nor a measured area has `area_m2` null: do not derive one from `geom`. `area_source` nominal_outline: the sheet states no scale, the outline was sized with an ESTIMATED one (q.definitions: units / nominal_scale) - a rough estimate, say so.
- Trust the store: `agreement` agree or single needs no re-measuring. On conflict report the preferred value, name the conflict in `check` and lower the confidence.

## Layer 1: the files as parsed (for everything outside rooms, doors, windows, fixtures)
Geometry here is in the file's own coordinates (drawing units, PDF points, pixels; metres for IFC); q.files.units and q.frames (file coordinates to metres) say how to convert.
- {files}; {pages} (`image` is null when there is no render to look at); q.frames
- {cad_layers}
- {cad_entities} - one row per DXF/DWG entity. Entities expanded from a block INSERT carry the INSERT's handle in `parent`: count symbols with `dxftype = 'INSERT'`, plain drawn entities with `parent is null`. `area` (closed outlines) and `length` are in drawing units.
- {texts} - every text of every source (`source` = cad | pdf | ocr).
- {pdf_texts}; q.pdf_paths (vector strokes); q.ocr_words and q.detections (raster plans)
- {elements} - IFC building elements with stored_* (quantity sets) and measured_* (geometry) quantities.
- {ifc_elements}; {ifc_properties}; {ifc_relations} (`src --rel--> dst`: contains, aggregates, typed_by, fills, voids, bounds ...); q.ifc_geometry

## Examples
```sql
select count(*) as n, count(*) filter (where is_duplicate) as duplicates, array_agg(entity_id) as ids from q.doors
select round(sum(area_m2)::numeric, 2) as m2, array_agg(entity_id) as ids from q.rooms where apartment = 'B' and is_indoor
select number, label, area_m2, area_source, agreement, entity_id from q.rooms where number = '1.03'
select property, value, unit, method, format from q.sources where entity_id = 123
select layer, dxftype, count(*) as n from q.cad_entities where parent is null group by 1, 2 order by 1, 2
select count(*) as n, min(file_id) as file_id, array_agg(handle) as handles from q.cad_entities where dxftype = 'HATCH' and layer ilike '%wall%'
select source, text, x, y, ref from q.texts where text ilike '%kitchen%'
select ifc_class, count(*) as n, round(sum(stored_net_area_m2)::numeric, 2) as m2 from q.elements group by 1 order by 2 desc
```
Do sums, counts and unit conversions in SQL, so the number you report is literally in a result. Never select `geom` itself: use ST_Area(geom), ST_Length(geom), ST_AsText(ST_Envelope(geom)).
"""


def columns(db: DB) -> dict[str, list[str]]:
    """view name -> its columns, for every view of schema q."""
    out: dict[str, list[str]] = {}
    for r in db.run("""select table_name, column_name from information_schema.columns
                       where table_schema = 'q' order by table_name, ordinal_position"""):
        out.setdefault(r["table_name"], []).append(r["column_name"])
    return out


def render(db: DB) -> str:
    cols = columns(db)

    def view(m: re.Match) -> str:
        name = m[1]
        if name not in cols:
            raise KeyError(f"STORE.md documents q.{name}, which the database does not have")
        return f"**q.{name}**({', '.join(cols[name])})"
    return re.sub(r"\{(\w+)\}", view, TEMPLATE)


def documented(text: str) -> set[str]:
    """Views the document names (q.<name>)."""
    return set(re.findall(r"\bq\.([a-z_]+)\b", text))


if __name__ == "__main__":
    with DB() as db:
        text = render(db)
    if "--check" in sys.argv:
        same = PATH.is_file() and PATH.read_text(encoding="utf-8") == text
        print("STORE.md is up to date" if same else "STORE.md differs from the database: run python -m jstore.service.prompt")
        sys.exit(0 if same else 1)
    PATH.write_text(text, encoding="utf-8")
    print(f"wrote {PATH} ({len(text.encode())} bytes)")
