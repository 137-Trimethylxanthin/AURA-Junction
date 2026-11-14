-- Service additions (idempotent).  Hardening for the model-facing reader role:
-- the scope of a run lives in two settings (jstore.drawings, jstore.files).  The service sets them with SET on
-- its reader connection; nothing that runs inside a SELECT may change them, so the functions that can do that
-- (set_config directly, or the ones that execute a query given as text) are taken away from non-superusers.
revoke execute on function pg_catalog.set_config(text, text, boolean) from public;
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'pg_catalog'
             and p.proname in ('query_to_xml', 'query_to_xmlschema', 'query_to_xml_and_xmlschema', 'cursor_to_xml',
                               'cursor_to_xmlschema', 'table_to_xml', 'table_to_xmlschema', 'table_to_xml_and_xmlschema',
                               'schema_to_xml', 'schema_to_xmlschema', 'schema_to_xml_and_xmlschema', 'database_to_xml',
                               'database_to_xmlschema', 'database_to_xml_and_xmlschema', 'ts_stat', 'ts_rewrite',
                               'lo_import', 'lo_export')
  loop
    execute format('revoke execute on function %s from public', f.sig);
  end loop;
end $$;
