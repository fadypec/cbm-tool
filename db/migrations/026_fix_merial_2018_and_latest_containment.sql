-- 026_fix_merial_2018_and_latest_containment.sql
--
-- 1. Merial Animal Health, Biological Laboratory (GBR, 2018) is still marked
--    BSL-4. It is the SAPO Level 4 / ABL 4 animal-pathogen confusion that
--    migrations 016/017 corrected for the other Pirbright-site years — they
--    matched on canonical ids, which had been renumbered by then, and missed
--    this row. Matched here on document + facility name, which are stable.
--    Same correction as 016/017 for this facility: BSL-4 -> unknown.
--
-- 2. facilities.latest_containment / latest_area_m2 were computed by script 05
--    before later corrections and merges, so some are stale (e.g. APHA shows
--    BSL-3 though its 2025 declaration is BSL-4). Recompute from facility_years:
--    the latest year's highest containment; area = its BSL-4 area, else BSL-3.
--    Same rule as scripts/06_load_database.py refresh_facilities().

BEGIN;

UPDATE facility_years
SET    has_bsl4            = FALSE,
       bsl4_area_m2        = NULL,
       highest_containment = 'unknown'
WHERE  document_id   = 'GBR_2018'
  AND  facility_name = 'Merial Animal Health, Biological Laboratory'
  AND  has_bsl4 = TRUE;

UPDATE facilities f
SET    latest_containment = latest.highest_containment,
       latest_area_m2     = COALESCE(latest.bsl4_area_m2, latest.bsl3_area_m2)
FROM  (SELECT DISTINCT ON (canonical_facility_id)
              canonical_facility_id, highest_containment, bsl4_area_m2, bsl3_area_m2
       FROM   facility_years
       ORDER  BY canonical_facility_id, year DESC,
                 CASE WHEN highest_containment ILIKE '%4%' THEN 4
                      WHEN highest_containment ILIKE '%3%' THEN 3
                      WHEN highest_containment ILIKE '%2%' THEN 2
                      WHEN highest_containment ILIKE '%1%' THEN 1
                      ELSE 0 END DESC,
                 COALESCE(bsl4_area_m2, bsl3_area_m2) DESC NULLS LAST) latest
WHERE  f.canonical_facility_id = latest.canonical_facility_id
  AND  (f.latest_containment IS DISTINCT FROM latest.highest_containment
        OR f.latest_area_m2 IS DISTINCT FROM COALESCE(latest.bsl4_area_m2, latest.bsl3_area_m2));

COMMIT;
