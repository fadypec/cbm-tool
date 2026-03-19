-- 015_manual_verification_corrections.sql
-- Corrections from manual PDF verification session (2026-03-19).
--
-- Items verified against source CBM PDFs:
--
--   B: CIV_001 CEPRIS-LNSP (2025)
--      PDF says "P3 et P4 sur 1000m²" — combined P3+P4 area, not BSL-4 alone.
--      has_bsl4 remains TRUE (P4 confirmed present); bsl4_area_m2 set to NULL
--      because the P4-only portion cannot be determined from the PDF.
--
--   C: AUS_001 AAHL (years where bsl4_area_m2 = 11,000)
--      2007 PDF says "Total floor space 11,000m²" for the maximum containment
--      system — this is the total building enclosure including large-animal
--      accommodation, service areas, incinerator, etc., NOT the BSL-4 lab area.
--      From 2012 onwards the PDF lists specific sub-facility areas (90 + 127 +
--      350 = 567 m²) which are correctly extracted. Years with 11,000 m² are
--      set to NULL as the true BSL-4 lab area is unknown for those declarations.
--
--   G: JPN_002 Murayama Annex (years where bsl4_area_m2 ≈ 2,270)
--      Both 2013 and 2020 PDFs say "Three P4 Laboratories, Seventeen P3
--      Laboratories and their supporting Laboratories (2,270.36 m² in totals)".
--      The figure is the combined total of all P3, P4, and support space; it
--      cannot be attributed to BSL-4 alone. has_bsl4 remains TRUE; bsl4_area_m2
--      set to NULL.
--
--   H: PRT_003 (Portugal 2011)
--      2011 Portugal CBM Form A Part 1 is blank. The facility_years row has
--      confidence=0, no name, no address, no containment — a spurious extraction
--      artefact. Row deleted; orphan facility entity also removed.
--
-- Items verified as CORRECT (no change):
--   A: USA_004 Plum Island 17,643 m² BSL-3 — PDF states that figure explicitly,
--      including 12,052 m² BSL-3 support space. Correct as declared.
--   D: GBR Pirbright 4,167 m² + Boehringer 4,000 m² — sums match PDF exactly.
--   E: NLD_004 Wageningen 4,500 m² — PDF states exactly that.
--   F: DEU_012 FLI 1,251 m² (917 vet + 334 human) — two new BSL-4 buildings;
--      jump is real and confirmed.
--   I: UKR_004 SSCIBSM 1,374 m² — PDF states "4 (1374,5 m²)"; rounding only.

BEGIN;

-- ── B: CIV_001 — clear BSL-4 area (combined P3+P4 figure, not BSL-4 only) ──
UPDATE facility_years
SET    bsl4_area_m2 = NULL
WHERE  canonical_facility_id = 'CIV_001'
  AND  bsl4_area_m2 = 1000;

-- ── C: AUS_001 — clear erroneous 11,000 m² BSL-4 (total enclosure area) ──────
UPDATE facility_years
SET    bsl4_area_m2 = NULL
WHERE  canonical_facility_id = 'AUS_001'
  AND  bsl4_area_m2 = 11000;

-- ── G: JPN_002 — clear 2,270 m² BSL-4 (combined P3+P4+support total) ─────────
UPDATE facility_years
SET    bsl4_area_m2 = NULL
WHERE  canonical_facility_id = 'JPN_002'
  AND  bsl4_area_m2 BETWEEN 2269 AND 2272;   -- 2270.36 stored as rounded int

-- ── H: PRT_003 — delete ghost facility_years row (blank Form A Part 1) ────────
DELETE FROM facility_years
WHERE  canonical_facility_id = 'PRT_003'
  AND  year = 2011;

-- Remove orphan facility entity (no remaining facility_years rows)
DELETE FROM facilities
WHERE  canonical_facility_id = 'PRT_003'
  AND  NOT EXISTS (
         SELECT 1 FROM facility_years
         WHERE canonical_facility_id = 'PRT_003'
       );

COMMIT;
