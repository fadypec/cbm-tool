-- 019: Add missing indexes on document_id in child tables and composite indexes.
--
-- The GeoJSON endpoints and country-detail queries JOIN child tables back to
-- documents via document_id.  Without these indexes, each JOIN does a seq scan
-- on the child table.  With ~1.6K facility_years and ~1K defence_facilities
-- this is tolerable today, but prevents degradation as data grows.

BEGIN;

-- Child table indexes on document_id (used in JOINs to documents)
CREATE INDEX IF NOT EXISTS idx_facility_years_document_id
    ON facility_years (document_id);

CREATE INDEX IF NOT EXISTS idx_vaccine_facility_years_document_id
    ON vaccine_facility_years (document_id);

CREATE INDEX IF NOT EXISTS idx_defence_facilities_document_id
    ON defence_facilities (document_id);

CREATE INDEX IF NOT EXISTS idx_defence_programmes_document_id
    ON defence_programmes (document_id);

CREATE INDEX IF NOT EXISTS idx_past_programmes_document_id
    ON past_programmes (document_id);

CREATE INDEX IF NOT EXISTS idx_legislation_document_id
    ON legislation (document_id);

-- Composite index for form_compliance — queries filter on (document_id, form)
CREATE INDEX IF NOT EXISTS idx_form_compliance_doc_form
    ON form_compliance (document_id, form);

COMMIT;
