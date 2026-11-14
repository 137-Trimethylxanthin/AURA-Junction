-- Lossless project store.  Idempotent: safe to run on every start.
--
-- Layer 0  registry        project, source_file, page, frame
-- Layer 1  everything parsed, no decisions (a queryable mirror of each file)
--          cad_layer, cad_entity | pdf_path, pdf_text | raster_word, raster_detection
--          ifc_element, ifc_property, ifc_relation, ifc_geometry
-- Layer 2  interpretations on top, each pointing back at Layer 1
--          definition, entity, entity_source, claim, issue
-- Schema q  what the question-answering model may read: views scoped to the current run.
--
-- Coordinates
--   Layer 1 geometry is in the file's own coordinates: drawing units (CAD), points with the
--   origin top-left and y down (PDF), pixels (raster), metres (IFC, world XY).
--   frame maps those into the drawing's shared frame in metres; entity.geom is in that frame.
--   page.to_image maps file coordinates onto the rendered page image in pixels.

create extension if not exists postgis;
create extension if not exists vector;
create extension if not exists pg_trgm;
create extension if not exists unaccent;

create schema if not exists jstore;
set search_path = jstore, public;

-- ------------------------------------------------------------------ Layer 0: registry
create table if not exists project (
  project_id  text primary key,
  title       text,
  source      text,                       -- dataset folder, or 'upload'
  meta        jsonb not null default '{}'
);

create table if not exists source_file (
  file_id      text primary key,          -- first 16 hex digits of the content sha256
  project_id   text not null references project on delete cascade,
  path         text not null,             -- relative to the dataset root, or the upload name
  format       text not null,             -- ifc | dxf | dwg | pdf | png | jpg | svg
  bytes        bigint,
  sha256       text,
  drawing_key  text not null,             -- equal for files that show the same drawing in another format
  storey       text,                      -- storey the file shows, when known
  status       text not null default 'registered',  -- registered | parsed | interpreted | failed
  error        text,
  meta         jsonb not null default '{}',          -- units, scale, schema, title block ...
  ingested_at  timestamptz
);
create index if not exists source_file_drawing on source_file (drawing_key);
create index if not exists source_file_project on source_file (project_id);

create table if not exists page (          -- a PDF page, a raster image, a CAD space, an IFC storey
  file_id   text not null references source_file on delete cascade,
  page_no   int  not null,                -- 0-based
  name      text,                         -- layout / storey / sheet name
  width     double precision, height double precision,
  unit      text,                         -- 'pt' | 'px' | 'unit' (drawing units) | 'm'
  image     text,                         -- rendered image of the page, relative to the store directory
  image_w   int, image_h int,
  to_image  double precision[],           -- [a,b,c,d,e,f]: px = a*x + b*y + c ; py = d*x + e*y + f
  meta      jsonb not null default '{}',
  primary key (file_id, page_no)
);

create table if not exists frame (         -- file page coordinates -> the drawing's shared frame (metres)
  file_id   text not null references source_file on delete cascade,
  page_no   int  not null,
  m         double precision[] not null,  -- [a,b,c,d,e,f]: X = a*x + b*y + c ; Y = d*x + e*y + f
  method    text not null,                -- 'native' | 'units' | 'text_anchors' | 'scale_text' | 'twin_dpi' | 'correlation'
  residual  double precision,             -- RMS error of the fit, metres
  n_anchors int,
  primary key (file_id, page_no)
);

-- ------------------------------------------------------------------ Layer 1: CAD
create table if not exists cad_layer (
  file_id text not null references source_file on delete cascade,
  name text not null, color int, linetype text, is_on boolean, n_entities int,
  primary key (file_id, name)
);

create table if not exists cad_entity (
  id        bigserial primary key,
  file_id   text not null references source_file on delete cascade,
  handle    text not null,                -- DXF handle; '<insert handle>/<n>' for entities expanded from a block
  space     text not null default 'model',-- 'model' or the paper-space layout name
  parent    text,                         -- handle of the INSERT this entity was expanded from
  block     text,                         -- block name (INSERT itself, and its expanded entities)
  dxftype   text not null,
  layer     text,
  color     int, linetype text,
  closed    boolean,
  n_vertices int,
  text      text,                         -- TEXT / MTEXT / ATTRIB content
  geom      geometry,                     -- 2D, drawing units, curves flattened; exact parameters are in attrs
  attrs     jsonb not null default '{}'   -- radius, angles, bulges, height, rotation, attribs, measurement ...
);
create index if not exists cad_entity_file on cad_entity (file_id, layer);
create index if not exists cad_entity_geom on cad_entity using gist (geom);
create unique index if not exists cad_entity_key on cad_entity (file_id, handle);

-- ------------------------------------------------------------------ Layer 1: PDF
create table if not exists pdf_path (
  id      bigserial primary key,
  file_id text not null references source_file on delete cascade,
  page_no int not null, seq int not null,
  kind    text,                           -- line | rect | curve
  stroke  text, fill text,                -- '#rrggbb' or null
  width   real, dash text,
  closed  boolean, n_vertices int,
  geom    geometry,                       -- points, origin top-left, y down
  attrs   jsonb not null default '{}'
);
create index if not exists pdf_path_file on pdf_path (file_id, page_no);
create index if not exists pdf_path_geom on pdf_path using gist (geom);

create table if not exists pdf_text (
  id      bigserial primary key,
  file_id text not null references source_file on delete cascade,
  page_no int not null, seq int not null,
  text    text not null,
  x0 real, y0 real, x1 real, y1 real,     -- points, origin top-left
  font text, size real,
  geom    geometry                        -- centre point
);
create index if not exists pdf_text_file on pdf_text (file_id, page_no);
create index if not exists pdf_text_geom on pdf_text using gist (geom);

-- ------------------------------------------------------------------ Layer 1: raster
create table if not exists raster_word (
  id      bigserial primary key,
  file_id text not null references source_file on delete cascade,
  page_no int not null default 0,
  text    text not null, conf real,
  x0 real, y0 real, x1 real, y1 real,     -- pixels
  engine  text,
  geom    geometry
);
create index if not exists raster_word_file on raster_word (file_id);

create table if not exists raster_detection (  -- every candidate, also low-scoring ones
  id      bigserial primary key,
  file_id text not null references source_file on delete cascade,
  page_no int not null default 0,
  class   text not null, score real,
  x0 real, y0 real, x1 real, y1 real,     -- pixels
  detector text,                          -- model or reader name and version
  attrs   jsonb not null default '{}',
  geom    geometry
);
create index if not exists raster_detection_file on raster_detection (file_id, class);

-- ------------------------------------------------------------------ Layer 1: IFC
create table if not exists ifc_element (     -- every rooted object: products, spatial elements, types, groups
  file_id text not null references source_file on delete cascade,
  guid    text not null,
  step_id int,
  ifc_class text not null,
  name text, long_name text, object_type text, tag text, predefined_type text, description text,
  storey  text, storey_guid text,         -- containing storey, resolved through containment and aggregation
  parent_guid text,                       -- aggregate or container directly above
  type_guid text, type_name text,
  attrs   jsonb not null default '{}',    -- remaining direct attributes (OverallWidth, Elevation ...)
  primary key (file_id, guid)
);
create index if not exists ifc_element_class on ifc_element (file_id, ifc_class);

create table if not exists ifc_property (
  id      bigserial primary key,
  file_id text not null, guid text not null,
  pset    text not null, name text not null,
  value_text text, value_num double precision,
  unit    text,                           -- SI unit after conversion: m | m2 | m3 | null
  kind    text not null,                  -- 'property' | 'quantity'
  from_type boolean not null default false,
  foreign key (file_id, guid) references ifc_element on delete cascade
);
create index if not exists ifc_property_key on ifc_property (file_id, guid);
create index if not exists ifc_property_name on ifc_property (file_id, name);

create table if not exists ifc_relation (
  id      bigserial primary key,
  file_id text not null references source_file on delete cascade,
  rel     text not null,                  -- contains | aggregates | typed_by | fills | voids | bounds | in_group | material
  src_guid text not null, dst_guid text,  -- src --rel--> dst  (storey contains door; wall voids opening; door fills opening)
  attrs   jsonb not null default '{}'
);
create index if not exists ifc_relation_src on ifc_relation (file_id, src_guid);
create index if not exists ifc_relation_dst on ifc_relation (file_id, dst_guid);

create table if not exists ifc_geometry (    -- measured from the tessellated body, metres
  file_id text not null, guid text not null,
  footprint geometry,                     -- projection on the XY plane
  footprint_m2 double precision, volume_m3 double precision, surface_m2 double precision,
  z_min double precision, z_max double precision,
  bbox double precision[],                -- [xmin,ymin,zmin,xmax,ymax,zmax]
  primary key (file_id, guid),
  foreign key (file_id, guid) references ifc_element on delete cascade
);
create index if not exists ifc_geometry_fp on ifc_geometry using gist (footprint);

-- ------------------------------------------------------------------ Layer 2: interpretations
create table if not exists definition (     -- how a concept is derived for one file: stored, inspectable
  def_id   bigserial primary key,
  file_id  text not null references source_file on delete cascade,
  concept  text not null,                 -- room | apartment | door | window | toilet | washbasin | ...
  method   text not null,                 -- layer_role | style_cluster | ifc_class | reader | agent
  spec     jsonb not null default '{}',   -- e.g. {"layer": "ROOM_OUTLINE", "rule": "closed outline holding one room number"}
  confidence real,
  created_by text,
  created_at timestamptz not null default now()
);
create index if not exists definition_file on definition (file_id, concept);

create table if not exists entity (         -- one physical thing of a drawing, across all its source files
  entity_id bigserial primary key,
  project_id text not null references project on delete cascade,
  drawing_key text not null,
  storey    text,
  ifc_class text,                         -- IfcSpace | IfcZone | IfcDoor | IfcWindow | IfcSanitaryTerminal ...
  kind      text not null,                -- room | apartment | door | window | toilet | washbasin | bathtub | shower | sink | ...
  number    text,                         -- identifier printed on the drawing (room number, door tag)
  label     text,                         -- name as written
  label_norm text,                        -- normalised kind of room in English (kitchen, bedroom ...)
  zone      text,                         -- apartment / unit the thing belongs to
  host      text,                         -- room number the thing sits in (doors, windows, fixtures)
  geom      geometry,                     -- shared drawing frame, metres
  attrs     jsonb not null default '{}',
  duplicate_of bigint,                    -- entity_id of the first identical item, when drawn more than once
  status    text not null default 'active'
);
create index if not exists entity_drawing on entity (drawing_key, kind);
create index if not exists entity_geom on entity using gist (geom);

create table if not exists entity_source (  -- where an entity was read from, per source file
  id        bigserial primary key,
  entity_id bigint not null references entity on delete cascade,
  file_id   text not null references source_file on delete cascade,
  page_no   int not null default 0,
  ref_table text not null,                -- cad_entity | pdf_path | pdf_text | raster_word | raster_detection | ifc_element | region
  ref_key   text,                         -- handle / guid / row id
  role      text,                         -- outline | number | label | area_text | symbol | tag ...
  bbox      double precision[],           -- [x0,y0,x1,y1] in the file's own coordinates
  def_id    bigint references definition on delete set null
);
create index if not exists entity_source_entity on entity_source (entity_id);
create index if not exists entity_source_file on entity_source (file_id);

create table if not exists claim (          -- one value for one property from one source
  claim_id  bigserial primary key,
  entity_id bigint not null references entity on delete cascade,
  property  text not null,                -- net_area_m2 | width_m | height_m | length_m | volume_m3 | label | number ...
  value_num double precision, value_text text,
  unit      text,
  file_id   text references source_file on delete cascade,
  method    text not null,                -- printed | measured_outline | stored_quantity | measured_geometry | ocr | detected
  source_id bigint references entity_source on delete set null,
  confidence real,
  rank      text not null default 'normal'  -- preferred | normal | deprecated
);
create index if not exists claim_entity on claim (entity_id, property);

create table if not exists issue (          -- a disagreement or a gap, kept visible
  issue_id  bigserial primary key,
  project_id text not null, drawing_key text,
  entity_id bigint references entity on delete cascade,
  property  text,
  kind      text not null,                -- value_conflict | missing_in_source | unlinked | registration
  detail    text,
  status    text not null default 'open',
  created_at timestamptz not null default now()
);
create index if not exists issue_drawing on issue (drawing_key);
