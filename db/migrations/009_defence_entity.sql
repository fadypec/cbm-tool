-- Migration 009: add canonical_defence_facility_id to defence_facilities
-- Enables entity deduplication across years (mirrors canonical_facility_id on facility_years)

ALTER TABLE defence_facilities
  ADD COLUMN IF NOT EXISTS canonical_defence_facility_id TEXT;

CREATE INDEX IF NOT EXISTS idx_defence_facilities_canonical
    ON defence_facilities (canonical_defence_facility_id);
