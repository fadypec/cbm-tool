-- FEATURE 11: Canonical defence facility entity registry
-- Mirrors the 'facilities' table for research facilities (Form A1).
-- Populated by 06_load_database.py build_defence_entities() after loading
-- defence_facilities.
CREATE TABLE IF NOT EXISTS defence_entities (
    canonical_defence_facility_id TEXT PRIMARY KEY,
    country_iso3  CHAR(3)   NOT NULL,
    canonical_name  TEXT,
    first_year      SMALLINT,
    last_year       SMALLINT,
    all_names       TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_de_country ON defence_entities (country_iso3);
