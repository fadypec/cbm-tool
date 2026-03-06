-- 004_relax_containment_checks.sql
-- Remove CHECK constraints on containment-level columns.
--
-- The containment values are extracted by an LLM and can include non-standard
-- strings such as "Enhanced BSL-2" or facility-specific descriptions.
-- Data quality is enforced upstream in the extraction pipeline, not here.

BEGIN;

ALTER TABLE facilities
    DROP CONSTRAINT IF EXISTS facilities_latest_containment_check;

ALTER TABLE facility_years
    DROP CONSTRAINT IF EXISTS facility_years_highest_containment_check;

ALTER TABLE defence_facilities
    DROP CONSTRAINT IF EXISTS defence_facilities_geocode_confidence_check;

COMMIT;
