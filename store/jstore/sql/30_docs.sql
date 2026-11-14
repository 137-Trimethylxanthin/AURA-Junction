-- Additions for PDF, SVG and raster pages (idempotent; applied after schema.sql and views.sql).

alter table jstore.pdf_text    add column if not exists line_no int;        -- words of one text line share it (per page)
alter table jstore.pdf_text    add column if not exists rot smallint;       -- reading direction, degrees counter-clockwise
alter table jstore.raster_word add column if not exists rot smallint;       -- same, for an OCR text line
alter table jstore.raster_word add column if not exists quad real[];        -- the detected quadrilateral, 8 numbers, pixels
create index if not exists raster_word_page on jstore.raster_word (file_id, page_no);
create index if not exists raster_detection_page on jstore.raster_detection (file_id, page_no);

-- PDF and SVG words joined back into the text lines they were printed as.
create or replace view q.pdf_lines as
select t.file_id, t.page_no, t.line_no, string_agg(t.text, ' ' order by t.seq) as text,
       min(t.x0) as x0, min(t.y0) as y0, max(t.x1) as x1, max(t.y1) as y1,
       max(t.size) as size, min(t.font) as font, min(t.rot) as rot, count(*) as n_words
from jstore.pdf_text t
where t.file_id = any (q.scope_files()) and t.line_no is not null
group by t.file_id, t.page_no, t.line_no;

-- OCR text lines with their reading direction and exact outline.
create or replace view q.ocr_lines as
select w.file_id, w.page_no, w.text, w.conf, w.x0, w.y0, w.x1, w.y1, w.rot, w.quad, w.engine
from jstore.raster_word w where w.file_id = any (q.scope_files());

-- What a page is: scale texts printed on it, whether it is a scan, how much was read from it.
create or replace view q.page_info as
select p.file_id, p.page_no, p.name, p.unit, p.meta->'scales' as scales, p.meta->>'label' as label,
       coalesce((p.meta->>'scanned')::boolean, false) as is_scan,
       (p.meta->>'rotation')::int as rotation, p.meta
from jstore.page p where p.file_id = any (q.scope_files());

-- Document structure of a file: bookmarks and links with their target pages.
create or replace view q.doc_outline as
select f.file_id, (o.item->>'level')::int as level, o.item->>'title' as title, (o.item->>'page')::int as page_no
from jstore.source_file f, jsonb_array_elements(coalesce(f.meta->'outline', '[]'::jsonb)) as o(item)
where f.file_id = any (q.scope_files());

create or replace view q.doc_links as
select f.file_id, (l.item->>'page')::int as page_no, l.item->'box' as box, l.item->>'uri' as uri,
       (l.item->>'target_page')::int as target_page
from jstore.source_file f, jsonb_array_elements(coalesce(f.meta->'links', '[]'::jsonb)) as l(item)
where f.file_id = any (q.scope_files());
