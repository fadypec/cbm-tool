-- 018_merge_duplicate_entities.sql
-- Merge duplicate facility entities that the automated deduplication
-- (rapidfuzz token_sort_ratio ≥ 85) missed due to appended translations,
-- "formerly X" suffixes, or minor naming variations.
--
-- Each merge: reassign facility_years → surviving entity, absorb name
-- variants into surviving entity's all_names, recompute years_declared,
-- delete orphaned facilities row.

BEGIN;

-- ── Helper function ─────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION _merge_entity(p_deprecated text, p_survivor text)
RETURNS void AS $$
BEGIN
    -- Reassign all facility-year rows
    UPDATE facility_years
    SET    canonical_facility_id = p_survivor
    WHERE  canonical_facility_id = p_deprecated;

    -- Absorb name variants into surviving entity
    UPDATE facilities
    SET    all_names = (
               SELECT array_agg(DISTINCT n ORDER BY n)
               FROM (
                   SELECT unnest(all_names) AS n
                   FROM   facilities
                   WHERE  canonical_facility_id IN (p_survivor, p_deprecated)
               ) sub
           ),
           years_declared = (
               SELECT array_agg(DISTINCT year::smallint ORDER BY year::smallint)
               FROM   facility_years
               WHERE  canonical_facility_id = p_survivor
           )
    WHERE  canonical_facility_id = p_survivor;

    -- Remove orphaned entity
    DELETE FROM facilities WHERE canonical_facility_id = p_deprecated;
END;
$$ LANGUAGE plpgsql;


-- ═══════════════════════════════════════════════════════════════════════════
-- CHE — Swiss facilities with German name ± English translation in parens
-- ═══════════════════════════════════════════════════════════════════════════

-- Centre National de Référence pour les Infections Virales Emergentes
SELECT _merge_entity('CHE_008', 'CHE_006');

-- Institut für Medizinische Virologie
SELECT _merge_entity('CHE_012', 'CHE_009');

-- Institut für Viruskrankheiten und Immunprophylaxe
SELECT _merge_entity('CHE_010', 'CHE_005');

-- Institut für Virologie und Immunologie
SELECT _merge_entity('CHE_013', 'CHE_011');


-- ═══════════════════════════════════════════════════════════════════════════
-- GBR — "formerly X" suffixes and organisational renames
-- ═══════════════════════════════════════════════════════════════════════════

-- Boehringer Ingelheim Animal Health UK Limited
-- (GBR_024 adds "formerly Merial Animal Health, Biological Laboratory")
SELECT _merge_entity('GBR_024', 'GBR_023');

-- The Francis Crick Institute Containment 4
-- (GBR_021 = "Building C (formerly NIMR)"; GBR_022 = "facility")
SELECT _merge_entity('GBR_022', 'GBR_021');

-- The Medicines and Healthcare Products Regulatory Agency
-- (GBR_031 adds "Formerly listed as NIBSC")
SELECT _merge_entity('GBR_031', 'GBR_029');

-- UK Health Security Agency – Porton
-- (GBR_026 adds "Formerly Public Health England - Porton")
SELECT _merge_entity('GBR_030', 'GBR_026');

-- UK Health Security Agency - Colindale
-- (GBR_027 adds "Formely Public Health England - Colindale")
SELECT _merge_entity('GBR_028', 'GBR_027');


-- ═══════════════════════════════════════════════════════════════════════════
-- CZE — Institute of Molecular Pathology with/without CAS suffix
-- ═══════════════════════════════════════════════════════════════════════════

SELECT _merge_entity('CZE_013', 'CZE_004');


-- ═══════════════════════════════════════════════════════════════════════════
-- DEU — Robert Koch Institute (RKI) ZBS, different name length
-- ═══════════════════════════════════════════════════════════════════════════

SELECT _merge_entity('DEU_016', 'DEU_014');


-- ═══════════════════════════════════════════════════════════════════════════
-- EST — Estonian labs with/without parent organisation suffix
-- ═══════════════════════════════════════════════════════════════════════════

-- Laboratory for Mycobacteriosis (± "of University of Life Sciences")
SELECT _merge_entity('EST_012', 'EST_004');

-- Laboratory of Communicable Diseases (± "of Estonian Health Board")
SELECT _merge_entity('EST_010', 'EST_006');


-- ═══════════════════════════════════════════════════════════════════════════
-- FIN — renamed organisations and capitalisation variants
-- ═══════════════════════════════════════════════════════════════════════════

-- Finnish Defence Forces, Centre for Military Medicine
-- (FIN_018 = with "Research and Development Department"; FIN_019 = shorter)
SELECT _merge_entity('FIN_019', 'FIN_018');

-- National Institute for Health and Welfare (THL) — successor of KTL/NPHI
SELECT _merge_entity('FIN_010', 'FIN_009');

-- University of Turku, Institute of Biomedicine / Tyks Laboratories
-- Three-way: FIN_020 (original), FIN_021 (adds "wellbeing"), FIN_023 (adds "Wellbeing")
SELECT _merge_entity('FIN_023', 'FIN_020');
SELECT _merge_entity('FIN_021', 'FIN_020');


-- ═══════════════════════════════════════════════════════════════════════════
-- IRL — Irish facilities with varying name detail
-- ═══════════════════════════════════════════════════════════════════════════

-- Public Health Laboratory (PHL) — Cherry Orchard / HSE Dublin
SELECT _merge_entity('IRL_027', 'IRL_002');

-- Institute for Molecular Medicine (± "Trinity College Centre for Health Sciences")
SELECT _merge_entity('IRL_015', 'IRL_011');

-- School of Medicine and Medical Science Centre (± "for Research in Infectious Diseases")
SELECT _merge_entity('IRL_018', 'IRL_012');


-- ═══════════════════════════════════════════════════════════════════════════
-- LUX — National Health Laboratory BSL-3 singular/plural
-- ═══════════════════════════════════════════════════════════════════════════

-- "Additional BSL-3 Laboratory" vs "Additional BSL-3 laboratories"
SELECT _merge_entity('LUX_009', 'LUX_008');


-- ═══════════════════════════════════════════════════════════════════════════
-- LVA — Riga East University Hospital, different name structure
-- ═══════════════════════════════════════════════════════════════════════════

SELECT _merge_entity('LVA_007', 'LVA_005');


-- ═══════════════════════════════════════════════════════════════════════════
-- MEX — Spanish/English name variants and state lab duplicates
-- ═══════════════════════════════════════════════════════════════════════════

-- CIATEJ Jalisco: English vs Spanish directorate name
SELECT _merge_entity('MEX_023', 'MEX_020');

-- IPN National School of Biological Sciences — Vaccinology Lab
SELECT _merge_entity('MEX_070', 'MEX_060');

-- LESP VER — Veracruz state lab ± "Dr. Mauro Loyo Varela"
SELECT _merge_entity('MEX_049', 'MEX_034');

-- LESP SON — Sonora state lab (4 entities → MEX_008)
SELECT _merge_entity('MEX_059', 'MEX_008');
SELECT _merge_entity('MEX_041', 'MEX_008');
SELECT _merge_entity('MEX_027', 'MEX_008');

-- LESP NAY — Nayarit state lab (4 entities → MEX_040)
SELECT _merge_entity('MEX_072', 'MEX_040');
SELECT _merge_entity('MEX_058', 'MEX_040');
SELECT _merge_entity('MEX_048', 'MEX_040');

-- Aguascalientes state lab ± "Ministry of Health"
SELECT _merge_entity('MEX_046', 'MEX_037');


-- ── Clean up ────────────────────────────────────────────────────────────
DROP FUNCTION _merge_entity(text, text);

COMMIT;
