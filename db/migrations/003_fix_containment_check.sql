-- 003_fix_containment_check.sql
-- Extend facilities.latest_containment check to allow 'unknown',
-- matching the constraint already on facility_years.

BEGIN;

ALTER TABLE facilities
    DROP CONSTRAINT facilities_latest_containment_check;

ALTER TABLE facilities
    ADD CONSTRAINT facilities_latest_containment_check
    CHECK (
        latest_containment IN ('BSL-1','BSL-2','BSL-3','BSL-4','unknown')
        OR latest_containment IS NULL
    );

COMMIT;
