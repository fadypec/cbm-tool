-- 017_fix_bsl4_misclassifications.sql
-- Correct BSL-4 misclassifications and fix migration 016 which targeted stale entity IDs.
--
-- Three issues:
--
-- 1. GBR Pirbright / Boehringer (SAPO Level 4 ≠ BSL-4)
--    Migration 016 targeted GBR_009/GBR_010 but dedup reassigned these to
--    GBR_017 (Pirbright) and GBR_023/GBR_024 (Boehringer). Re-apply with
--    correct IDs. See 016 header for full rationale (SAPO vs ACDP).
--
-- 2. HRV_001 — Croatian Institute of Public Health (2018)
--    PDF raw text: "Unit for Intervention Diagnostics of Highly Contagious
--    and Rare Viruses and Bacteria (BSL3/4), which require work in Security
--    Level 3 Conditions (BSL 3); equipped with a fully automated BSL ¾
--    system for processing hazardous samples."
--    Operational containment is BSL-3; "BSL3/4" refers to equipment rating.
--    Croatia has no BSL-4 facility.
--
-- 3. UKR_004 — SSCIBSM (2023)
--    PDF states containment level "4" with 1,374.5 m², but agents are
--    entirely agricultural (classical swine fever, Newcastle disease, bovine
--    leukemia, etc.). This is a veterinary quality-control institute using
--    a national containment scale where level 4 is the highest tier for
--    animal pathogens — same pattern as UK SAPO Level 4. Not BSL-4.

BEGIN;

-- ── GBR_017 Pirbright 2022–2024: SAPO Level 4 → BSL-3 ──────────────────
UPDATE facility_years
SET    has_bsl4            = FALSE,
       bsl4_area_m2        = NULL,
       highest_containment = 'BSL-3'
WHERE  canonical_facility_id = 'GBR_017'
  AND  has_bsl4 = TRUE;

-- ── GBR_023 Boehringer: SAPO Level 4 → unknown ─────────────────────────
UPDATE facility_years
SET    has_bsl4            = FALSE,
       bsl4_area_m2        = NULL,
       highest_containment = 'unknown'
WHERE  canonical_facility_id = 'GBR_023'
  AND  has_bsl4 = TRUE;

-- ── GBR_024 Boehringer (formerly Merial): same fix ─────────────────────
UPDATE facility_years
SET    has_bsl4            = FALSE,
       bsl4_area_m2        = NULL,
       highest_containment = 'unknown'
WHERE  canonical_facility_id = 'GBR_024'
  AND  has_bsl4 = TRUE;

-- ── HRV_001 Croatian Institute of Public Health 2018: BSL3/4 → BSL-3 ───
UPDATE facility_years
SET    has_bsl4            = FALSE,
       bsl4_area_m2        = NULL,
       highest_containment = 'BSL-3'
WHERE  canonical_facility_id = 'HRV_001'
  AND  has_bsl4 = TRUE;

-- ── UKR_004 SSCIBSM 2023: veterinary level 4 → BSL-3 ──────────────────
UPDATE facility_years
SET    has_bsl4            = FALSE,
       bsl4_area_m2        = NULL,
       highest_containment = 'BSL-3'
WHERE  canonical_facility_id = 'UKR_004'
  AND  has_bsl4 = TRUE;

COMMIT;
