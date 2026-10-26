-- 024_split_ken_overmerged_entities.sql
-- Split two Kenyan entities that entity resolution chained together.
--
-- Kenya's 2024 and 2025 CBMs each list these labs as separate entries in the
-- same submission. Their names score 88 on token_sort_ratio across years
-- (threshold 85), so Union-Find linked e.g. "HIV Ref Lab 2024" with "TB Ref
-- Lab 2025" and merged both labs' histories into one entity.
--
--   KEN_001  National HIV Reference Laboratory (NHRL)       stays KEN_001
--            National TB Reference Laboratory (NTRL)        -> KEN_016
--            (different agents: HIV/SARS-CoV-2 vs TB)
--   KEN_003  Microbiology Reference Laboratory (KEMRI, BSL-3) stays KEN_003
--            National Microbiology Reference Laboratory      -> KEN_017
--            (different organisation: NPHL; BSL-2)
--
-- KEN_016/017 have never been used, in the database or in script 05 output.
-- Moves 4 facility_years rows (2 per new entity); deletes nothing.
-- Safe to re-run: the row moves match on the old id + exact name.

BEGIN;

INSERT INTO facilities (canonical_facility_id, country_iso3, canonical_name, all_names)
VALUES ('KEN_016', 'KEN', 'National TB Reference Laboratory (NTRL)',
        ARRAY['National TB Reference Laboratory (NTRL)']),
       ('KEN_017', 'KEN', 'National Microbiology Reference Laboratory',
        ARRAY['National Microbiology Reference Laboratory'])
ON CONFLICT (canonical_facility_id) DO NOTHING;

UPDATE facility_years SET canonical_facility_id = 'KEN_016'
WHERE  canonical_facility_id = 'KEN_001'
  AND  facility_name = 'National TB Reference Laboratory (NTRL)';

UPDATE facility_years SET canonical_facility_id = 'KEN_017'
WHERE  canonical_facility_id = 'KEN_003'
  AND  facility_name = 'National Microbiology Reference Laboratory';

-- Recompute names, years and latest containment/area for all four entities
UPDATE facilities f
SET    all_names = ARRAY(SELECT DISTINCT facility_name FROM facility_years fy
                         WHERE fy.canonical_facility_id = f.canonical_facility_id
                         AND   fy.facility_name IS NOT NULL ORDER BY 1),
       canonical_name = latest.facility_name,
       years_declared = ARRAY(SELECT DISTINCT year::smallint FROM facility_years fy
                              WHERE fy.canonical_facility_id = f.canonical_facility_id ORDER BY 1),
       latest_containment = latest.highest_containment,
       latest_area_m2     = COALESCE(latest.bsl4_area_m2, latest.bsl3_area_m2)
FROM  (SELECT DISTINCT ON (canonical_facility_id)
              canonical_facility_id, facility_name, highest_containment, bsl4_area_m2, bsl3_area_m2
       FROM   facility_years
       WHERE  canonical_facility_id IN ('KEN_001', 'KEN_003', 'KEN_016', 'KEN_017')
       ORDER  BY canonical_facility_id, year DESC) latest
WHERE  f.canonical_facility_id = latest.canonical_facility_id;

COMMIT;
