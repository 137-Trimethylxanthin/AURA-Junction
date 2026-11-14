# The store: schema q (PostgreSQL 17 + PostGIS)

Every view shows only the drawings of this run. Answer from these views whenever they cover the question.

## Layer 2: interpreted things (look here first)
One row per physical thing of a drawing, merged across its source files (DXF, PDF, image, IFC).
- **q.rooms**(entity_id, drawing, storey, number, label, room_type, apartment, area_m2, area_source, perimeter_m, is_indoor, n_sources, agreement, is_duplicate, geom) - `area_m2` is the NET floor area (the preferred source; `area_source` says which: printed, measured_outline, stored_quantity ...). `perimeter_m` is the length of the room outline (inner wall faces). `is_indoor` is false for balconies, terraces, loggias. `agreement`: agree | single | conflict between the `n_sources` files. `apartment` is the unit the room belongs to.
- **q.apartments**(entity_id, drawing, storey, apartment, label, rooms, net_area_m2, geom) - `rooms` counts its rooms, `net_area_m2` sums its indoor rooms.
- **q.doors**(entity_id, drawing, storey, kind, ifc_class, tag, label, room, apartment, width_m, height_m, n_sources, is_duplicate, duplicate_of, attrs, geom); q.windows and q.fixtures have the same columns (all three are slices of q.items). `kind` tells fixtures apart (toilet, washbasin, sink, bathtub, shower ...); `room` is the number of the host room.
- **q.facts**(entity_id, kind, number, property, value, value_text, unit, method, from_file, n_sources, agreement) - one row per entity and property with the preferred value and the agreement.
- **q.sources**(entity_id, kind, number, property, value, value_text, unit, method, rank, confidence, file_id, format, path) - every value behind a fact, one row per source file.
- **q.entity_sources**(entity_id, kind, number, file_id, format, page_no, ref_table, ref_key, role, bbox) - the Layer 1 primitives each entity was read from.
- **q.issues**(issue_id, drawing, entity_id, property, kind, detail, status) - open conflicts and gaps. q.definitions - how each concept was derived per file.

Conventions
- Net area: "area", "floor area", "living area" mean sum(area_m2) over rooms with `is_indoor`; include outdoor rooms only when the question asks for them.
- Drawn twice: a thing drawn more than once has `is_duplicate` = true on the later copies. Counts and sums include every drawn copy (the drawing is the record); when copies exist, say in the answer how many of the total are duplicates.
- Scans and PDFs without CAD: doors, windows and fixtures there are detected on the page image (`attrs.score`, `attrs.page_no`); room areas are printed values where the sheet prints them, else measured from pixels with lower confidence. A sheet set that repeats a storey on several pages raises the issue `repeated_sheets`: count per page (`attrs->>'page_no'`) and say which page.
- Measured on a scan (image, or PDF without CAD): `perimeter_m` is always measured on an outline read from the pixels, and `area_m2` is too where `area_source` is measured_outline; both are approximate. Trust = `confidence` of the row in q.sources (outline x scale confidence): 0.7 and more = confirmed outline, almost always within 5 %; 0.45 to 0.7 = plausible, usually within a few percent; below 0.45 = an estimate, say so. A room with neither a printed nor a measured area has `area_m2` null: do not derive one from `geom`. `area_source` nominal_outline: the sheet states no scale, the outline was sized with an ESTIMATED one (q.definitions: units / nominal_scale) - a rough estimate, say so.
- Trust the store: `agreement` agree or single needs no re-measuring. On conflict report the preferred value, name the conflict in `check` and lower the confidence.

## Layer 1: the files as parsed (for everything outside rooms, doors, windows, fixtures)
Geometry here is in the file's own coordinates (drawing units, PDF points, pixels; metres for IFC); q.files.units and q.frames (file coordinates to metres) say how to convert.
- **q.files**(file_id, path, format, drawing, project, storey, status, units, scale, meta); **q.pages**(file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image) (`image` is null when there is no render to look at); q.frames
- **q.cad_layers**(file_id, layer, color, linetype, is_on, n_entities)
- **q.cad_entities**(file_id, handle, space, parent, block, dxftype, layer, color, linetype, closed, n_vertices, text, attrs, geom, area, length) - one row per DXF/DWG entity. Entities expanded from a block INSERT carry the INSERT's handle in `parent`: count symbols with `dxftype = 'INSERT'`, plain drawn entities with `parent is null`. `area` (closed outlines) and `length` are in drawing units.
- **q.texts**(file_id, page_no, source, text, x, y, style, ref) - every text of every source (`source` = cad | pdf | ocr).
- **q.pdf_texts**(file_id, page_no, seq, text, x0, y0, x1, y1, font, size, geom); q.pdf_paths (vector strokes); q.ocr_words and q.detections (raster plans)
- **q.elements**(file_id, guid, ifc_class, name, long_name, type_name, predefined_type, tag, storey, is_external, stored_length_m, stored_net_area_m2, stored_net_volume_m3, measured_footprint_m2, measured_volume_m3, measured_surface_m2, geom, storey_guid, parent_guid, type_guid, type_class, is_type, category, is_part, stored_net_floor_area_m2, stored_other_floor_area_m2, stored_side_area_m2, stored_volume_m3, measured_length_m, measured_width_m, measured_side_area_m2, measured_height_m, z_min, z_max, width_m, height_m) - IFC building elements with stored_* (quantity sets) and measured_* (geometry) quantities.
- **q.ifc_elements**(file_id, guid, ifc_class, name, long_name, object_type, tag, predefined_type, description, storey, storey_guid, parent_guid, type_guid, type_name, attrs); **q.ifc_properties**(file_id, guid, pset, name, value_text, value_num, unit, kind, from_type); **q.ifc_relations**(file_id, rel, src_guid, dst_guid, attrs) (`src --rel--> dst`: contains, aggregates, typed_by, fills, voids, bounds ...); q.ifc_geometry

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
