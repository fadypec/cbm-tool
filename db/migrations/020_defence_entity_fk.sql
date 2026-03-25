-- 020: Add foreign key from defence_facilities to defence_entities.
--
-- Ensures referential integrity: every non-NULL canonical_defence_facility_id
-- in defence_facilities must point to an existing defence_entities row.
-- NULL values are allowed (facilities not yet entity-resolved).

BEGIN;

ALTER TABLE defence_facilities
    ADD CONSTRAINT fk_defence_facilities_entity
    FOREIGN KEY (canonical_defence_facility_id)
    REFERENCES defence_entities (canonical_defence_facility_id);

COMMIT;
