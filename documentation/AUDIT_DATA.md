# CBM Tool — Data Quality Audit

**Date:** 2026-03-17
**Scope:** Entity resolution, extraction anomalies, OCR artifacts, geocoding gaps

This document extends VALIDATION_CHECKLIST.md with additional findings from systematic analysis.

---

## 1. Entity Resolution Failures

### 1A. GBR: 31 entities should be ~10 (CRITICAL)

The UK's serial renaming of agencies (HPA -> PHE -> UKHSA) combined with fuzzy matching failures creates ~21 excess entities. **The dedup_entities.py script contains zero GBR merge groups.** This is the single largest entity resolution failure in the dataset.

| Canonical facility | Entity IDs to merge |
|---|---|
| Porton Down (HPA/PHE/UKHSA Centre for Emergency Preparedness) | GBR_001, GBR_006, GBR_019, GBR_026, GBR_030 |
| Colindale (HPA/PHE/UKHSA Centre for Infections) | GBR_002, GBR_005, GBR_018, GBR_027, GBR_028 |
| Dstl Porton Down (Defence Science & Technology Laboratory) | GBR_004, GBR_013 |
| VLA / AHVLA / APHA (Animal Health) | GBR_008, GBR_015, GBR_020 |
| IAH / Pirbright Institute | GBR_009, GBR_014, GBR_017 |
| Merial / Boehringer Ingelheim (Pirbright site) | GBR_010, GBR_016, GBR_023, GBR_024 |
| NIMR / Francis Crick Institute | GBR_007, GBR_021, GBR_022 |
| NIBSC / MHRA (South Mimms) | GBR_003, GBR_029, GBR_031 |
| Schering-Plough / Intervet (Upper Hale) | GBR_011, GBR_012 |

**Note:** This confirms the user's suspicion about HPA/PHE/UKHSA being treated as multiple entities.

### 1B. AUS: AAHL/ACDP not merged (HIGH)

AUS_001 (Australian Animal Health Laboratory, 2006-2020) and AUS_005 (Australian Centre for Disease Preparedness, 2021-2025) are the **same physical facility** at the same address. CSIRO renamed it in 2020. Treated as 2 separate entities.

### 1C. DNK: 21 entities for what is likely 1 facility (MEDIUM)

Denmark has 14 null-named entities (DNK_008 through DNK_021), each appearing in exactly 1 year. These appear to be the same unnamed facility declared annually from 2012-2025, getting a new canonical ID each time because entity resolution cannot match NULL names.

### 1D. CYP: 7 entities for 7 records, all unnamed (MEDIUM)

Same pattern as Denmark. Cyprus declares 1 unnamed facility each year (2019-2025).

### 1E. SVK: 5 null-named entities (2018-2024), each single-year (MEDIUM)

Same pattern as Denmark/Cyprus.

### 1F. Defence entity fragmentation is severe (HIGH)

- **CHE:** 40 defence entities — many are German/French/English name variants of the same 4 facilities
- **CAN:** 9 defence entities — DRDC Suffield and Valcartier each appear as ~5 entities
- **BEL:** 12 defence entities — CTMA/DLD-Bio appears as ~7 entities
- **USA:** 63 defence entities — Edgewood Chemical Biological Center split into USA_D004 and USA_D008

### 1G. Vaccine entity fragmentation: BGR BulBio (MEDIUM)

Bulgaria's BulBio-NCIPD vaccine facility at 26 Yanko Sakazov Blvd, Sofia appears as 6 separate canonical entities (BGR_V001 through BGR_V006) due to minor spelling variations.

### 1H. CHE/USA/UKR/DEU dedup script exists but effects don't persist (HIGH)

`dedup_entities.py` defines 7 merge groups (CHE, USA, UKR, DEU) that operate on the PostgreSQL `facilities` table. However, `06_load_database.py` reloads from the undeduped CSV/JSON outputs every time. The dedup must be re-applied after every database reload, and there is no automation for this. The annual_update.sh script does not call `dedup_entities.py`.

---

## 2. Anomalous BSL Areas (Beyond VALIDATION_CHECKLIST.md)

### 2A. AUS AAHL: 11,000 m2 BSL-4 in 2006-2009, then 567 m2 from 2012 (HIGH)

The 11,000 m2 figure is almost certainly total facility floor area misattributed to BSL-4 containment. The actual BSL-4 area is 567 m2 (consistent 2012 onwards). This inflates aggregate BSL-4 capacity by ~20x for those years.

### 2B. Consistency check: all BSL-4 areas > 5,000 m2

Review these against source documents:
- USA Plum Island: 17,643 m2 BSL-3 (2010-2011) — likely total lab area
- AUS AAHL: 11,000 m2 BSL-4 (2006-2009) — likely total facility area
- CIV CEPRIS: 1,000 m2 BSL-4 (2025) — likely incorrect BSL level

---

## 3. Zero and Low Confidence Records

### 3A. PRT_003: Zero-confidence ghost record (HIGH)

Portugal 2011 has a facility_years record with:
- confidence = 0.000
- No facility name
- No address
- No city
- containment = unknown

This is an extraction artifact. The record contains zero usable data.

### 3B. Three defence facility records with confidence < 0.4 (LOW)

- BEL_D009 (LORARC, 2017, conf=0.3) — work_description may describe a different facility
- TLS 2025 (conf=0.3)

### 3C. 18 records with confidence < 0.5 (1.1% of dataset) (LOW)

These should be spot-checked against source PDFs.

---

## 4. Legislation Data Quality

### 4A. 14 AUT legislation rows marked "substantive" but have ALL NULL booleans (MEDIUM)

For AUT years 2009-2012, 2015, 2017-2025, the compliance says Form E is "substantive" but every boolean in the legislation table is NULL. Notes say these are blank templates or illegible fax transmissions. The compliance status should be "limited" or "absent".

### 4B. 40 legislation records (12%) with ALL NULL booleans (MEDIUM)

Beyond AUT: CZE, DEU, HRV, JPN, and others have legislation rows where the LLM couldn't extract any Yes/No data. These rows add noise without signal.

---

## 5. Unnamed Facility Patterns

### 5A. 38 entities (8.3%) are unnamed/null

Countries with genuinely unnamed facilities (confirmed correct):
- DNK (2012-2025): unnamed but should be merged into 1 entity
- CYP (2019-2025): unnamed but should be merged into 1 entity
- SVN (2014-2020): unnamed, correct
- SVK (2018-2024): unnamed but should be merged into 1 entity

### 5B. 236 facility-year records (15%) have no address AND no city

Ungeocodeable. Concentrated in CHE (multilingual forms), DNK (unnamed), SVK, CYP, SVN.

---

## 6. Geocoding Gaps (Extending VALIDATION_CHECKLIST.md)

### 6A. Estonia: 82.9% missing from map (58/70 facility-years)

Estonian CBMs give institution names rather than street addresses. Manual geocoding of 5-10 institutions would fix this.

### 6B. Mexico: 59.7% missing from map (77/129 facility-years)

Likely address formatting issue. Spot-check MEX records for address field content.

### 6C. AUS QHFSS: Coordinate jump in 2024

Brisbane (2006-2023) to Canberra (2024). The facility did not move. Likely a head-office address in the 2024 CBM.

---

## 7. Year and Temporal Anomalies

### 7A. Year 2026 data present (LOW)

BLZ and TUV have 2026 records. These are CBM submissions for calendar year 2025 that use 2026 reporting dates. Not a bug but creates confusion in year filtering.

### 7B. 16-year gap in coverage (1991-2005)

Records exist for 1988-1990 and 2006-2026. Reflects the available corpus (pre-2000 scans + 2006+ digital), not a pipeline error.

---

## 8. Containment Level Anomalies

### 8A. Non-standard containment values (LOW)

`BSL-2+`, `BSL-3+`, `Enhanced BSL-2` are real values from submissions but fall into the "unknown" bucket in the dashboard's BSL color mapping. Consider normalizing or adding explicit support.

### 8B. 37 records have has_bsl4=True but bsl4_area_m2=NULL (LOW)

Common for CHE and older AUS submissions where BSL-4 is confirmed but area not specified. Not wrong, but limits area analysis.

---

## 9. Non-Standard Data Patterns

### 9A. JPN Murayama intermittent BSL-4 area (from VALIDATION_CHECKLIST)

Same 2,270 m2 figure alternates between BSL-3 and BSL-4 across years. Likely represents total high-containment area.

### 9B. DEU FLI containment gap then jump (from VALIDATION_CHECKLIST)

2014-2017 gap in BSL-4, then jump to 1,251 m2 in 2023. Reflects real facility expansion (new building 2013-2020).

---

## Priority Actions

| Priority | Action | Records affected | Effort |
|----------|--------|-----------------|--------|
| 1 | Add GBR merge groups to dedup_entities.py | ~31 entities → ~10 | 1 hr |
| 2 | Merge AUS_001/AUS_005 (AAHL/ACDP) | 2 entities → 1 | 15 min |
| 3 | Merge DNK/CYP/SVK unnamed single-year entities | ~26 entities → 3 | 30 min |
| 4 | Fix AUS AAHL BSL-4 area 11,000 → verify/correct | 4 year-records | 15 min |
| 5 | Delete PRT_003 ghost record | 1 record | 5 min |
| 6 | Integrate dedup_entities.py into annual_update.sh | All entities | 15 min |
| 7 | Add defence entity merge groups (CHE, CAN, BEL) | ~60 excess entities | 2 hrs |
| 8 | Fix AUT legislation compliance status | 14 rows | 30 min |
| 9 | Manual geocoding for top 5 EST institutions | ~58 records | 30 min |
| 10 | Correct AUS QHFSS 2024 coordinates | 1 record | 5 min |

**Estimated true unique facilities after dedup: ~390-400** (vs current claim of ~457)
