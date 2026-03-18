-- 013_relax_geocode_checks.sql
-- Drop geocode_confidence CHECK constraints from facility_years and
-- vaccine_facility_years, matching the relaxation already applied to
-- defence_facilities in migration 004.

BEGIN;

ALTER TABLE facility_years
    DROP CONSTRAINT IF EXISTS facility_years_geocode_confidence_check;

ALTER TABLE vaccine_facility_years
    DROP CONSTRAINT IF EXISTS vaccine_facility_years_geocode_confidence_check;

COMMIT;
