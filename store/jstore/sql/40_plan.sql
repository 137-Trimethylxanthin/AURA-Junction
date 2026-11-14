-- Layer 2 for plans: indexes that keep re-interpreting a drawing fast.
-- Deleting a drawing's entities cascades through these foreign keys; without an index each deleted row
-- scans the referencing table (claim.source_id, issue.entity_id, entity_source.def_id).
create index if not exists claim_source on jstore.claim (source_id);
create index if not exists claim_file on jstore.claim (file_id);
create index if not exists issue_entity on jstore.issue (entity_id);
create index if not exists entity_source_def on jstore.entity_source (def_id);
