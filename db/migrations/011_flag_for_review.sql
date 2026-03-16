-- FEATURE 8: Add flagging columns to facility_years for human validation workflow
ALTER TABLE facility_years ADD COLUMN IF NOT EXISTS flagged_for_review BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE facility_years ADD COLUMN IF NOT EXISTS flag_note TEXT;
CREATE INDEX IF NOT EXISTS idx_fy_flagged ON facility_years (flagged_for_review) WHERE flagged_for_review = TRUE;
