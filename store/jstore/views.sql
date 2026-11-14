-- Schema q: the only thing the question-answering model can read.
-- Every view is scoped to the current run through two settings the service sets per query:
--   jstore.drawings  comma-separated drawing keys   (Layer 2 and the file list)
--   jstore.files     comma-separated file ids       (Layer 1)
-- Rebuilt from scratch on every start (q holds nothing but views and helper functions).

drop schema if exists q cascade;
create schema q;

create function q.scope_drawings() returns text[] language sql stable as
$$ select coalesce(string_to_array(nullif(current_setting('jstore.drawings', true), ''), ','), '{}') $$;
create function q.scope_files() returns text[] language sql stable as
$$ select coalesce(string_to_array(nullif(current_setting('jstore.files', true), ''), ','), '{}') $$;

-- ------------------------------------------------------------------ files and pages
create view q.files as
select f.file_id, f.path, f.format, f.drawing_key as drawing, f.project_id as project, f.storey, f.status,
       f.meta->>'units' as units, f.meta->>'scale' as scale, f.meta
from jstore.source_file f
where f.drawing_key = any (q.scope_drawings());

create view q.pages as
select p.file_id, p.page_no, p.name, p.width, p.height, p.unit, p.image, p.image_w, p.image_h, p.to_image
from jstore.page p where p.file_id = any (q.scope_files());

create view q.frames as
select fr.file_id, fr.page_no, fr.m, fr.method, fr.residual, fr.n_anchors
from jstore.frame fr where fr.file_id = any (q.scope_files());

-- ------------------------------------------------------------------ Layer 2: facts
-- One row per entity and property: the preferred value, how many source files gave a value,
-- and whether they agree (numbers within 2 %).
create view q.facts as
with c as (
  select cl.*, e.kind, e.number
  from jstore.claim cl join jstore.entity e using (entity_id)
  where e.drawing_key = any (q.scope_drawings()) and e.status = 'active'
), agg as (
  select entity_id, property,
         count(distinct file_id) as n_sources,
         min(value_num) filter (where rank <> 'deprecated') as vmin,
         max(value_num) filter (where rank <> 'deprecated') as vmax,
         count(distinct value_text) filter (where rank <> 'deprecated' and value_num is null) as n_texts,
         count(*) filter (where rank = 'deprecated') as n_deprecated
  from c group by 1, 2
), pref as (
  select distinct on (entity_id, property)
         entity_id, kind, number, property, value_num, value_text, unit, method, file_id, confidence
  from c
  order by entity_id, property, (rank = 'preferred') desc, (rank = 'normal') desc, confidence desc nulls last, claim_id
)
select p.entity_id, p.kind, p.number, p.property, p.value_num as value, p.value_text, p.unit, p.method,
       p.file_id as from_file, a.n_sources,
       case when a.n_sources <= 1 then 'single'
            when a.n_deprecated > 0 or a.n_texts > 1 then 'conflict'
            when a.vmax is null or a.vmax - a.vmin <= 0.02 * greatest(abs(a.vmax), 1e-9) then 'agree'
            else 'conflict' end as agreement
from pref p join agg a using (entity_id, property);

-- Every value behind a fact, one row per source.
create view q.sources as
select cl.entity_id, e.kind, e.number, cl.property, cl.value_num as value, cl.value_text, cl.unit, cl.method, cl.rank,
       cl.confidence, f.file_id, f.format, f.path
from jstore.claim cl
join jstore.entity e using (entity_id)
left join jstore.source_file f on f.file_id = cl.file_id
where e.drawing_key = any (q.scope_drawings()) and e.status = 'active';

create view q.rooms as
select e.entity_id, e.drawing_key as drawing, e.storey, e.number, e.label, e.label_norm as room_type,
       e.zone as apartment,
       a.value as area_m2, a.method as area_source,
       -- the preferred perimeter claim (scans: measured on the room outline), else the length of the outline itself
       -- (entity.geom is in metres)
       coalesce(p.value, case when geometrytype(e.geom) in ('POLYGON', 'MULTIPOLYGON')
                              then round(st_perimeter(e.geom)::numeric, 3)::double precision end) as perimeter_m,
       coalesce((e.attrs->>'indoor')::boolean, true) as is_indoor,
       coalesce(a.n_sources, 0) as n_sources, a.agreement,
       e.duplicate_of is not null as is_duplicate,
       e.geom
from jstore.entity e
left join q.facts a on a.entity_id = e.entity_id and a.property = 'net_area_m2'
left join q.facts p on p.entity_id = e.entity_id and p.property = 'perimeter_m'
where e.kind = 'room' and e.status = 'active' and e.drawing_key = any (q.scope_drawings());

-- Printed (else measured) length and width of each room: the room size printed in it ("3.20 x 4.10"), else the
-- sides of the smallest rotated rectangle around its outline.
create view q.room_sizes as
select e.entity_id, e.drawing_key as drawing, e.number, e.label,
       coalesce(l.value, round(greatest(st_distance(st_pointn(b.ring, 1), st_pointn(b.ring, 2)),
                                        st_distance(st_pointn(b.ring, 2), st_pointn(b.ring, 3)))::numeric, 2)::double precision) as length_m,
       coalesce(w.value, round(least(st_distance(st_pointn(b.ring, 1), st_pointn(b.ring, 2)),
                                     st_distance(st_pointn(b.ring, 2), st_pointn(b.ring, 3)))::numeric, 2)::double precision) as width_m,
       case when l.value is not null then 'printed' when b.ring is not null then 'measured_bounding_box' end as size_source,
       l.value_text as size_text
from jstore.entity e
left join q.facts l on l.entity_id = e.entity_id and l.property = 'length_m' and l.method = 'printed'
left join q.facts w on w.entity_id = e.entity_id and w.property = 'width_m' and w.method = 'printed'
left join lateral (select st_exteriorring(st_orientedenvelope(e.geom)) as ring
                   where geometrytype(e.geom) in ('POLYGON', 'MULTIPOLYGON') and st_area(e.geom) > 0) b on true
where e.kind = 'room' and e.status = 'active' and e.drawing_key = any (q.scope_drawings());

-- Dimensions printed on the sheet: one row per dimension line, room size, printed area or level mark.
-- value_m is the printed value in metres (room_size: the longer side; a_m x b_m as printed), geom the dimension
-- line in the drawing frame (null on a page without a frame), `check` = printed value / length of the drawn line.
create view q.dimensions as
select e.entity_id, e.drawing_key as drawing, e.attrs->>'dim_kind' as kind, e.attrs->>'text' as text,
       (e.attrs->>'value_m')::double precision as value_m, e.attrs->>'unit' as unit,
       (e.attrs->>'a_m')::double precision as a_m, (e.attrs->>'b_m')::double precision as b_m,
       (e.attrs->>'area_m2')::double precision as area_m2,
       e.attrs->>'orientation' as orientation, e.attrs->>'room' as room,
       (e.attrs->>'room_id')::bigint as room_id,
       coalesce((e.attrs->>'page_no')::int, 0) as page_no, (e.attrs->>'check')::double precision as "check",
       (select min(s.file_id) from jstore.entity_source s where s.entity_id = e.entity_id) as file_id,
       e.geom
from jstore.entity e
where e.kind = 'dimension' and e.status = 'active' and e.drawing_key = any (q.scope_drawings());

create view q.apartments as
select e.entity_id, e.drawing_key as drawing, e.storey, e.number as apartment, e.label,
       (select count(*) from q.rooms r where r.drawing = e.drawing_key and r.apartment = e.number) as rooms,
       (select round(sum(r.area_m2)::numeric, 2) from q.rooms r
         where r.drawing = e.drawing_key and r.apartment = e.number and r.is_indoor) as net_area_m2,
       e.geom
from jstore.entity e
where e.kind = 'apartment' and e.status = 'active' and e.drawing_key = any (q.scope_drawings());

-- Doors, windows and fixtures share one shape.  `kind` tells fixtures apart
-- (toilet, washbasin, sink, bathtub, shower, ...).  A thing drawn twice has is_duplicate = true on the later copy.
create view q.items as
select e.entity_id, e.drawing_key as drawing, e.storey, e.kind, e.ifc_class, e.number as tag, e.label,
       e.host as room, e.zone as apartment,
       (select f.value from q.facts f where f.entity_id = e.entity_id and f.property = 'width_m') as width_m,
       (select f.value from q.facts f where f.entity_id = e.entity_id and f.property = 'height_m') as height_m,
       (select count(distinct s.file_id) from jstore.entity_source s where s.entity_id = e.entity_id) as n_sources,
       e.duplicate_of is not null as is_duplicate, e.duplicate_of,
       e.attrs, e.geom
from jstore.entity e
where e.kind not in ('room', 'apartment', 'dimension') and e.status = 'active' and e.drawing_key = any (q.scope_drawings());

create view q.doors    as select * from q.items where kind = 'door';
create view q.windows  as select * from q.items where kind = 'window';
create view q.fixtures as select * from q.items where kind not in ('door', 'window');

-- Where each entity was read from: one row per source file and primitive.
create view q.entity_sources as
select s.entity_id, e.kind, e.number, s.file_id, f.format, s.page_no, s.ref_table, s.ref_key, s.role, s.bbox
from jstore.entity_source s
join jstore.entity e using (entity_id)
join jstore.source_file f using (file_id)
where e.drawing_key = any (q.scope_drawings());

create view q.definitions as
select d.def_id, d.file_id, d.concept, d.method, d.spec, d.confidence, d.created_by
from jstore.definition d where d.file_id = any (q.scope_files());

create view q.issues as
select i.issue_id, i.drawing_key as drawing, i.entity_id, i.property, i.kind, i.detail, i.status
from jstore.issue i where i.drawing_key = any (q.scope_drawings());

-- ------------------------------------------------------------------ Layer 1: CAD
create view q.cad_layers as
select l.file_id, l.name as layer, l.color, l.linetype, l.is_on, l.n_entities
from jstore.cad_layer l where l.file_id = any (q.scope_files());

create view q.cad_entities as
select c.file_id, c.handle, c.space, c.parent, c.block, c.dxftype, c.layer, c.color, c.linetype, c.closed,
       c.n_vertices, c.text, c.attrs, c.geom,
       case when geometrytype(c.geom) in ('POLYGON', 'MULTIPOLYGON') then st_area(c.geom) end as area,
       st_length(c.geom) as length
from jstore.cad_entity c where c.file_id = any (q.scope_files());

-- ------------------------------------------------------------------ Layer 1: PDF and raster
create view q.pdf_paths as
select p.file_id, p.page_no, p.seq, p.kind, p.stroke, p.fill, p.width, p.dash, p.closed, p.n_vertices, p.attrs, p.geom
from jstore.pdf_path p where p.file_id = any (q.scope_files());

create view q.pdf_texts as
select t.file_id, t.page_no, t.seq, t.text, t.x0, t.y0, t.x1, t.y1, t.font, t.size, t.geom
from jstore.pdf_text t where t.file_id = any (q.scope_files());

create view q.ocr_words as
select w.file_id, w.page_no, w.text, w.conf, w.x0, w.y0, w.x1, w.y1, w.engine, w.geom
from jstore.raster_word w where w.file_id = any (q.scope_files());

create view q.detections as
select d.file_id, d.page_no, d.class, d.score, d.x0, d.y0, d.x1, d.y1, d.detector, d.attrs, d.geom
from jstore.raster_detection d where d.file_id = any (q.scope_files());

-- Every text of every source in one place (file coordinates).
create view q.texts as
select file_id, 0 as page_no, 'cad' as source, text, st_x(st_centroid(geom)) as x, st_y(st_centroid(geom)) as y,
       layer as style, handle as ref
from jstore.cad_entity where text is not null and text <> '' and file_id = any (q.scope_files())
union all
select file_id, page_no, 'pdf', text, (x0 + x1) / 2, (y0 + y1) / 2, font, id::text
from jstore.pdf_text where file_id = any (q.scope_files())
union all
select file_id, page_no, 'ocr', text, (x0 + x1) / 2, (y0 + y1) / 2, engine, id::text
from jstore.raster_word where file_id = any (q.scope_files());

-- ------------------------------------------------------------------ Layer 1: IFC
create view q.ifc_elements as
select el.file_id, el.guid, el.ifc_class, el.name, el.long_name, el.object_type, el.tag, el.predefined_type,
       el.description, el.storey, el.storey_guid, el.parent_guid, el.type_guid, el.type_name, el.attrs
from jstore.ifc_element el where el.file_id = any (q.scope_files());

create view q.ifc_properties as
select p.file_id, p.guid, p.pset, p.name, p.value_text, p.value_num, p.unit, p.kind, p.from_type
from jstore.ifc_property p where p.file_id = any (q.scope_files());

create view q.ifc_relations as
select r.file_id, r.rel, r.src_guid, r.dst_guid, r.attrs
from jstore.ifc_relation r where r.file_id = any (q.scope_files());

create view q.ifc_geometry as
select g.file_id, g.guid, g.footprint, g.footprint_m2, g.volume_m3, g.surface_m2, g.z_min, g.z_max, g.bbox
from jstore.ifc_geometry g where g.file_id = any (q.scope_files());

-- Building elements of a model with the quantities questions ask for most.
-- stored_* come from the model's quantity sets, measured_* from the tessellated body.
create view q.elements as
select el.file_id, el.guid, el.ifc_class, el.name, el.long_name, el.type_name, el.predefined_type, el.tag, el.storey,
       (select bool_or(lower(p.value_text) in ('true', '.t.', 't')) from jstore.ifc_property p
         where p.file_id = el.file_id and p.guid = el.guid and p.name = 'IsExternal') as is_external,
       (select max(p.value_num) from jstore.ifc_property p
         where p.file_id = el.file_id and p.guid = el.guid and p.kind = 'quantity' and p.name = 'Length') as stored_length_m,
       (select max(p.value_num) from jstore.ifc_property p
         where p.file_id = el.file_id and p.guid = el.guid and p.kind = 'quantity'
           and p.name in ('NetFloorArea', 'NetSideArea', 'NetArea')) as stored_net_area_m2,
       (select max(p.value_num) from jstore.ifc_property p
         where p.file_id = el.file_id and p.guid = el.guid and p.kind = 'quantity' and p.name = 'NetVolume') as stored_net_volume_m3,
       g.footprint_m2 as measured_footprint_m2, g.volume_m3 as measured_volume_m3, g.surface_m2 as measured_surface_m2,
       g.footprint as geom
from jstore.ifc_element el
left join jstore.ifc_geometry g on g.file_id = el.file_id and g.guid = el.guid
where el.file_id = any (q.scope_files());
