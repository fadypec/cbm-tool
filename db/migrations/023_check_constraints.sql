-- 023: Add CHECK constraints for data integrity
--
-- Prevents invalid values from being inserted via pipeline scripts or manual SQL.
-- These ranges match the CBM dataset: earliest submission 1987, latest plausibly ~2099.

-- Year must be within plausible CBM submission range
ALTER TABLE facility_years
    ADD CONSTRAINT ck_facility_years_year
    CHECK (year >= 1980 AND year <= 2099);

ALTER TABLE vaccine_facility_years
    ADD CONSTRAINT ck_vaccine_facility_years_year
    CHECK (year >= 1980 AND year <= 2099);

-- Defence personnel counts must be non-negative (NULL is allowed = unreported)
ALTER TABLE defence_facilities
    ADD CONSTRAINT ck_defence_facilities_personnel_total
    CHECK (personnel_total IS NULL OR personnel_total >= 0);

ALTER TABLE defence_facilities
    ADD CONSTRAINT ck_defence_facilities_personnel_military
    CHECK (personnel_military IS NULL OR personnel_military >= 0);

ALTER TABLE defence_facilities
    ADD CONSTRAINT ck_defence_facilities_personnel_civilian
    CHECK (personnel_civilian IS NULL OR personnel_civilian >= 0);

-- Confidence scores are 0-1 probabilities (already enforced on some tables; ensure coverage)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_facility_years_confidence'
    ) THEN
        ALTER TABLE facility_years
            ADD CONSTRAINT ck_facility_years_confidence
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;
END $$;
