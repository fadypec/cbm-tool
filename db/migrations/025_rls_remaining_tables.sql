-- 025_rls_remaining_tables.sql
-- Enable Row Level Security on the tables created after 021 (or missed by it).
--
-- On Supabase, a public table without RLS can be read AND written through the
-- auto-generated REST API by anyone holding the project's anon key. The data
-- tables get the same permissive read-only policy as in 021; schema_migrations
-- gets no policy, so only the owner/service role (which the API and migrate.sh
-- use, and which bypass RLS) can see it. No API query is affected.
-- (spatial_ref_sys is owned by the PostGIS extension and cannot be altered.)

BEGIN;

ALTER TABLE defence_entities   ENABLE ROW LEVEL SECURITY;
ALTER TABLE vaccine_facilities ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbreaks          ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read" ON defence_entities;
DROP POLICY IF EXISTS "public read" ON vaccine_facilities;
DROP POLICY IF EXISTS "public read" ON outbreaks;
CREATE POLICY "public read" ON defence_entities   FOR SELECT USING (true);
CREATE POLICY "public read" ON vaccine_facilities FOR SELECT USING (true);
CREATE POLICY "public read" ON outbreaks          FOR SELECT USING (true);

COMMIT;
