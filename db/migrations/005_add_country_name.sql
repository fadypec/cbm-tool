-- 005_add_country_name.sql
-- Add country_name column to documents table.
-- Populated by 06_load_database.py from catalogue.json.

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS country_name TEXT;

COMMENT ON COLUMN documents.country_name IS 'Full English country name as provided by the UN portal, e.g. "United Kingdom"';

COMMIT;
