-- 006_vaccine_geom.sql
-- Add geocoding columns to vaccine_facility_years (Form G).
-- Populated by 07_geocode.py, same as facility_years and defence_facilities.

BEGIN;

ALTER TABLE vaccine_facility_years
    ADD COLUMN IF NOT EXISTS geom               GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS geocode_source     TEXT,
    ADD COLUMN IF NOT EXISTS geocode_confidence TEXT
        CHECK (geocode_confidence IN ('high','medium','low') OR geocode_confidence IS NULL);

CREATE INDEX IF NOT EXISTS vaccine_facility_years_geom_idx
    ON vaccine_facility_years USING GIST (geom)
    WHERE geom IS NOT NULL;

COMMENT ON COLUMN vaccine_facility_years.geom               IS 'WGS84 point; populated by 07_geocode.py';
COMMENT ON COLUMN vaccine_facility_years.geocode_confidence IS 'Nominatim match quality: high, medium, or low';

COMMIT;
