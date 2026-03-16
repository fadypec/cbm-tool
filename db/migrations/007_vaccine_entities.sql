-- 007_vaccine_entities.sql
-- Add canonical vaccine facility entity table and FK column on vaccine_facility_years.
-- Mirrors the facilities / facility_years structure used for Form A1 research facilities.

CREATE TABLE IF NOT EXISTS vaccine_facilities (
    id           TEXT     PRIMARY KEY,
    country_iso3 CHAR(3)  NOT NULL,
    canonical_name TEXT,
    first_year   SMALLINT,
    last_year    SMALLINT
);

ALTER TABLE vaccine_facility_years
    ADD COLUMN IF NOT EXISTS canonical_vaccine_facility_id TEXT
        REFERENCES vaccine_facilities(id);

CREATE INDEX IF NOT EXISTS vfy_canonical_id_idx
    ON vaccine_facility_years (canonical_vaccine_facility_id);
