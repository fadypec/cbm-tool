-- 016_fix_gbr_abl4_bsl4_conflation.sql
-- Correct spurious BSL-4 flags for UK facilities that use SAPO / ABL Level 4
-- containment, which was incorrectly equated with BSL-4 (ACDP CL4) during
-- extraction.
--
-- Background:
--   The UK CBM reports use two parallel classification systems:
--   - ACDP CL (1–4): human pathogen containment; maps 1:1 to BSL-1 to BSL-4.
--   - SAPO Level (1–4): animal pathogen containment under the Specified Animal
--     Pathogens Order; Level 4 is the highest tier for pathogens like FMD and
--     bluetongue, but these labs operate at only ACDP CL2 or CL3 for human
--     pathogen risk.
--   "ABL 4" in the UK CBM = SAPO Level 4, NOT BSL-4.
--
-- GBR_009 — The Pirbright Institute (2022–2024)
--   PDF (2023 UK CBM) states: "No ACDP Containment Level (CL) 4 laboratories."
--   4,167 m² figure is the sum of SAPO Level 4 areas at ACDP CL2 and CL3.
--   Correct highest_containment is BSL-3 (ACDP CL3 is confirmed present:
--   413 m² explicitly stated + 257 m² SAPO Level 4 / ACDP CL3).
--   2025 was already correctly extracted as BSL-3.
--
-- GBR_010 — Boehringer Ingelheim Animal Health UK (Pirbright site) (2018–2025*)
--   PDF states "5 SAPO Level 4 containment units" for FMD/bluetongue vaccine
--   production. No ACDP CL level is stated; all years default to unknown.
--   (*2020–2021 already correctly have has_bsl4=False.)

BEGIN;

-- ── GBR_009 Pirbright: clear BSL-4 for 2022–2024 ─────────────────────────────
UPDATE facility_years
SET    has_bsl4           = FALSE,
       bsl4_area_m2       = NULL,
       highest_containment = 'BSL-3'
WHERE  canonical_facility_id = 'GBR_009'
  AND  has_bsl4 = TRUE;

-- ── GBR_010 Boehringer: clear BSL-4 for 2018, 2019, 2022–2025 ────────────────
UPDATE facility_years
SET    has_bsl4           = FALSE,
       bsl4_area_m2       = NULL,
       highest_containment = 'unknown'
WHERE  canonical_facility_id = 'GBR_010'
  AND  has_bsl4 = TRUE;

COMMIT;
