-- IFC additions: extra measures per shape and the model-facing views that make model questions one short query.
--
--   q.elements        every object of a model with its category, storey, stored and measured quantities
--   q.spaces          rooms (IfcSpace) with the area rule applied
--   q.storeys         one row per building storey: elevation, number of rooms, net room area
--   q.storey_totals   per storey and category: count, total length / side area / volume with the set rule applied
--   q.model_totals    the same for the whole model
--
-- Conventions (general IFC practice, not tied to a dataset):
--   * category        of an occurrence (null for type objects): the class without 'Ifc' and without
--                     StandardCase / ElementedCase; a generic occurrence
--                     (IfcFlowTerminal, IfcBuildingElementProxy ...) takes the category of its type object
--                     (IFC2X3: IfcFlowTerminal typed by IfcSanitaryTerminalType -> SanitaryTerminal)
--   * is_part         the object sits inside an assembly of its own category (a flight in a stair, a wall panel
--                     in a wall): counts of "stairs" or "walls" take the top-level objects only
--   * storey          the storey the object is assigned to in the spatial structure
--   * room area       stored NetFloorArea when present and non-zero, else the measured footprint
--   * set totals      stored quantities when every object of the set carries one, else measured geometry for all

alter table jstore.ifc_geometry add column if not exists length_m double precision;  -- long edge of the footprint's oriented rectangle
alter table jstore.ifc_geometry add column if not exists width_m  double precision;  -- short edge
alter table jstore.ifc_geometry add column if not exists side_m2  double precision;  -- area of the faces seen from one side
alter table jstore.ifc_geometry add column if not exists from_parts boolean not null default false;  -- no body of its own: sum of its parts (surface_m2 stays null)

-- The few properties q.elements reads per object (measures and IsExternal), without the vendor bulk.
create index if not exists ifc_property_measure on jstore.ifc_property (file_id, guid)
  where unit is not null or name = 'IsExternal';

create or replace function q.ifc_base(cls text) returns text language sql immutable as
$$ select regexp_replace(regexp_replace(cls, '^Ifc', ''), '(StandardCase|ElementedCase|Type|Style)$', '') $$;

create or replace function q.ifc_category(cls text, type_cls text) returns text language sql immutable as
$$ select case when type_cls is not null
                and q.ifc_base(cls) ~ '^(Flow(Terminal|Segment|Fitting|Controller|MovingDevice|StorageDevice|TreatmentDevice)|EnergyConversionDevice|Distribution(Control|Flow)?Element|DistributionChamberElement|BuildingElementProxy|FurnishingElement|ElectricalElement|TransportElement)$'
                and q.ifc_base(type_cls) !~ '^(BuildingElementProxy|FurnishingElement|TypeObject|TypeProduct|ElementType|DistributionElement|DistributionFlowElement)?$'
              then q.ifc_base(type_cls) else q.ifc_base(cls) end $$;

-- The first 17 columns keep the base definition's names and types; the rest is appended.
create or replace view q.elements as
select el.file_id, el.guid, el.ifc_class, el.name, el.long_name, el.type_name, el.predefined_type, el.tag, el.storey,
       s.is_external,
       coalesce(s.q_length, s.p_length) as stored_length_m,
       s.net_area as stored_net_area_m2,
       s.q_net_volume as stored_net_volume_m3,
       g.footprint_m2 as measured_footprint_m2, g.volume_m3 as measured_volume_m3, g.surface_m2 as measured_surface_m2,
       g.footprint as geom,
       el.storey_guid, el.parent_guid, el.type_guid, ty.ifc_class as type_class,
       el.ifc_class ~ '(Type|Style)$' as is_type,
       case when el.ifc_class !~ '(Type|Style)$' then q.ifc_category(el.ifc_class, ty.ifc_class) end as category,
       coalesce(q.ifc_base(p1.ifc_class) = q.ifc_base(el.ifc_class)
             or q.ifc_base(p2.ifc_class) = q.ifc_base(el.ifc_class)
             or q.ifc_base(p3.ifc_class) = q.ifc_base(el.ifc_class), false) as is_part,
       coalesce(s.q_net_floor, s.p_net_floor) as stored_net_floor_area_m2,
       coalesce(s.q_gross_floor, s.p_gross_floor, s.p_area) as stored_other_floor_area_m2,
       coalesce(s.q_net_side, s.q_gross_side, s.p_net_side, s.p_gross_side) as stored_side_area_m2,
       coalesce(s.q_net_volume, s.q_gross_volume, s.p_net_volume, s.p_gross_volume, s.p_volume) as stored_volume_m3,
       g.length_m as measured_length_m, g.width_m as measured_width_m, g.side_m2 as measured_side_area_m2,
       g.z_max - g.z_min as measured_height_m, g.z_min, g.z_max,
       (el.attrs->>'OverallWidth')::double precision as width_m,
       (el.attrs->>'OverallHeight')::double precision as height_m
from jstore.ifc_element el
left join jstore.ifc_geometry g on g.file_id = el.file_id and g.guid = el.guid
left join jstore.ifc_element ty on ty.file_id = el.file_id and ty.guid = el.type_guid
left join jstore.ifc_element p1 on p1.file_id = el.file_id and p1.guid = el.parent_guid
left join jstore.ifc_element p2 on p2.file_id = el.file_id and p2.guid = p1.parent_guid
left join jstore.ifc_element p3 on p3.file_id = el.file_id and p3.guid = p2.parent_guid
left join lateral (
  -- q_* from quantity sets, p_* from measure-typed properties (vendor sets); 0 is an unset placeholder.
  -- A name given twice: the object's own value beats the type's, and the later one beats the earlier one.
  select bool_or(lower(p.value_text) in ('true', '.t.', 't')) filter (where p.name = 'IsExternal') as is_external,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and p.name in ('NetFloorArea', 'NetSideArea', 'NetArea')))[1] as net_area,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm' and n = 'length'))[1] as q_length,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm' and n = 'length'))[1] as p_length,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm2' and n = 'netfloorarea'))[1] as q_net_floor,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm2' and n = 'netfloorarea'))[1] as p_net_floor,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm2' and n = 'grossfloorarea'))[1] as q_gross_floor,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm2' and n = 'grossfloorarea'))[1] as p_gross_floor,
         (array_agg(v order by ft, id desc) filter (where v is not null and u = 'm2' and n in ('area', 'floorarea')))[1] as p_area,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm2' and n = 'netsidearea'))[1] as q_net_side,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm2' and n = 'grosssidearea'))[1] as q_gross_side,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm2' and n = 'netsidearea'))[1] as p_net_side,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm2' and n = 'grosssidearea'))[1] as p_gross_side,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm3' and n = 'netvolume'))[1] as q_net_volume,
         (array_agg(v order by ft, id desc) filter (where v is not null and k and u = 'm3' and n = 'grossvolume'))[1] as q_gross_volume,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm3' and n = 'netvolume'))[1] as p_net_volume,
         (array_agg(v order by ft, id desc) filter (where v is not null and not k and u = 'm3' and n = 'grossvolume'))[1] as p_gross_volume,
         (array_agg(v order by ft, id desc) filter (where v is not null and u = 'm3' and n = 'volume'))[1] as p_volume
  from (select pr.id, pr.name, pr.value_text, pr.from_type as ft, pr.kind = 'quantity' as k, pr.unit as u,
               nullif(pr.value_num, 0) as v,
               lower(regexp_replace(pr.name, '[^A-Za-z]', '', 'g')) as n
        from jstore.ifc_property pr
        where pr.file_id = el.file_id and pr.guid = el.guid and (pr.unit is not null or pr.name = 'IsExternal')) p
) s on true
where el.file_id = any (q.scope_files());

create or replace view q.spaces as
select e.file_id, e.guid, e.storey, e.storey_guid, e.name as number, e.long_name as label,
       case when e.long_name is not null and e.name is not null then e.long_name || ' (' || e.name || ')'
            else coalesce(e.long_name, e.name) end as full_label,
       coalesce(e.stored_net_floor_area_m2, e.measured_footprint_m2) as area_m2,
       case when e.stored_net_floor_area_m2 is not null then 'stored NetFloorArea'
            when e.measured_footprint_m2 is not null then 'measured footprint' end as area_source,
       e.stored_net_floor_area_m2, e.stored_other_floor_area_m2, e.measured_footprint_m2,
       e.measured_height_m as height_m, e.is_part, e.geom
from q.elements e where e.ifc_class = 'IfcSpace';

create or replace view q.storeys as
select st.file_id, st.guid as storey_guid, st.name as storey, st.long_name,
       (st.attrs->>'Elevation')::double precision as elevation_m,
       pg.page_no,
       (select count(*) from q.spaces s where s.file_id = st.file_id and s.storey_guid = st.guid and not s.is_part) as n_spaces,
       (select round(sum(s.area_m2)::numeric, 3) from q.spaces s
         where s.file_id = st.file_id and s.storey_guid = st.guid and not s.is_part) as net_room_area_m2,
       (select count(*) from jstore.ifc_element e where e.file_id = st.file_id and e.storey_guid = st.guid) as n_elements
from jstore.ifc_element st
left join jstore.page pg on pg.file_id = st.file_id and pg.meta->>'guid' = st.guid
where st.ifc_class = 'IfcBuildingStorey' and st.file_id = any (q.scope_files());

-- Totals per storey and category.  A total is the sum of the stored quantities when every object of the set
-- has one, otherwise the sum of the measured geometry (one definition for the whole set); *_source says which.
create or replace view q.storey_totals as
select file_id, storey, storey_guid, category, count(*) as n,
       case when count(stored_length_m) = count(*) then sum(stored_length_m) else sum(measured_length_m) end as length_m,
       case when count(stored_length_m) = count(*) then 'stored' else 'measured' end as length_source,
       case when count(stored_side_area_m2) = count(*) then sum(stored_side_area_m2) else sum(measured_side_area_m2) end as side_area_m2,
       case when count(stored_side_area_m2) = count(*) then 'stored' else 'measured' end as side_area_source,
       case when count(stored_volume_m3) = count(*) then sum(stored_volume_m3) else sum(measured_volume_m3) end as volume_m3,
       case when count(stored_volume_m3) = count(*) then 'stored' else 'measured' end as volume_source,
       sum(measured_footprint_m2) as measured_footprint_m2
from q.elements where not is_part and not is_type and storey_guid is not null
group by file_id, storey, storey_guid, category;

create or replace view q.model_totals as
select file_id, category, count(*) as n,
       case when count(stored_length_m) = count(*) then sum(stored_length_m) else sum(measured_length_m) end as length_m,
       case when count(stored_length_m) = count(*) then 'stored' else 'measured' end as length_source,
       case when count(stored_side_area_m2) = count(*) then sum(stored_side_area_m2) else sum(measured_side_area_m2) end as side_area_m2,
       case when count(stored_side_area_m2) = count(*) then 'stored' else 'measured' end as side_area_source,
       case when count(stored_volume_m3) = count(*) then sum(stored_volume_m3) else sum(measured_volume_m3) end as volume_m3,
       case when count(stored_volume_m3) = count(*) then 'stored' else 'measured' end as volume_source,
       sum(measured_footprint_m2) as measured_footprint_m2
from q.elements where not is_part and not is_type
group by file_id, category;
