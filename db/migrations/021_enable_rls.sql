-- 021_enable_rls.sql
-- Enable Row Level Security on all public tables to satisfy Supabase security checks.
-- All data is publicly accessible (open government CBM submissions), so each table
-- gets a permissive SELECT policy for all roles.  The API connects via the service
-- role (DATABASE_URL) which bypasses RLS, so no existing queries are affected.

BEGIN;

-- Enable RLS on all 9 tables
ALTER TABLE documents               ENABLE ROW LEVEL SECURITY;
ALTER TABLE facilities              ENABLE ROW LEVEL SECURITY;
ALTER TABLE facility_years          ENABLE ROW LEVEL SECURITY;
ALTER TABLE vaccine_facility_years  ENABLE ROW LEVEL SECURITY;
ALTER TABLE defence_programmes      ENABLE ROW LEVEL SECURITY;
ALTER TABLE defence_facilities      ENABLE ROW LEVEL SECURITY;
ALTER TABLE past_programmes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE legislation             ENABLE ROW LEVEL SECURITY;
ALTER TABLE form_compliance         ENABLE ROW LEVEL SECURITY;

-- Add permissive SELECT policies (all rows are public CBM data)
CREATE POLICY "public read" ON documents              FOR SELECT USING (true);
CREATE POLICY "public read" ON facilities             FOR SELECT USING (true);
CREATE POLICY "public read" ON facility_years         FOR SELECT USING (true);
CREATE POLICY "public read" ON vaccine_facility_years FOR SELECT USING (true);
CREATE POLICY "public read" ON defence_programmes     FOR SELECT USING (true);
CREATE POLICY "public read" ON defence_facilities     FOR SELECT USING (true);
CREATE POLICY "public read" ON past_programmes        FOR SELECT USING (true);
CREATE POLICY "public read" ON legislation            FOR SELECT USING (true);
CREATE POLICY "public read" ON form_compliance        FOR SELECT USING (true);

COMMIT;
