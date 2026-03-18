# CBM Tool — Data Quality Audit

**Date:** 2026-03-17
**Last updated:** 2026-03-18
**Scope:** Entity resolution, extraction anomalies, OCR artifacts, geocoding gaps

This document extends VALIDATION_CHECKLIST.md with additional findings from systematic analysis.
Completed items have been removed; only outstanding work remains.

---

## 1. Entity Resolution Failures

### 1F. Defence entity fragmentation is severe (HIGH) — PENDING

- **CHE:** 40 defence entities — many are German/French/English name variants of the same 4 facilities
- **CAN:** 9 defence entities — DRDC Suffield and Valcartier each appear as ~5 entities
- **BEL:** 12 defence entities — CTMA/DLD-Bio appears as ~7 entities
- **USA:** 63 defence entities — Edgewood Chemical Biological Center split into USA_D004 and USA_D008

Fix: extend `dedup_entities.py` with `DEFENCE_MERGES` list and handlers for
`defence_entities` / `defence_facilities` tables. Requires verifying canonical IDs
in the current DB state before adding merge groups.

---

## 2. Anomalous BSL Areas

### 2A. AUS AAHL: 11,000 m² BSL-4 in 2006–2009, then 567 m² from 2012 (HIGH)

The 11,000 m² figure is almost certainly total facility floor area misattributed to BSL-4
containment. Inflates aggregate BSL-4 capacity by ~20x for those years.

**Action:** Open any 2010–2015 AUS CBM. Verify field 5 for AAHL. If confirmed wrong,
manually UPDATE the `facility_years` rows for `AUS_001` years 2006–2009.

### 2B. Consistency check: BSL-4 areas > 5,000 m² (LOW)

- USA Plum Island: 17,643 m² BSL-3 (2010–2011) — likely total lab area (see VALIDATION_CHECKLIST §A)
- AUS AAHL: 11,000 m² BSL-4 (2006–2009) — see §2A above
- CIV CEPRIS: 1,000 m² BSL-4 (2025) — likely incorrect BSL level (see VALIDATION_CHECKLIST §B)

---

## 3. Zero and Low Confidence Records

### 3A. PRT_003: Zero-confidence ghost record (HIGH)

Portugal 2011 has a `facility_years` record with confidence = 0.000, no facility name,
no address, no city, and unknown containment. Likely an extraction artifact.

**Action:** Open the 2011 Portugal CBM (https://bwc-cbm.un.org/report/detail/9718).
If Form A Part 1 is blank or "nothing to declare", delete this `facility_years` row.
See also VALIDATION_CHECKLIST §H.

### 3B. Defence facility records with confidence < 0.4 (LOW)

- BEL_D009 (LORARC, 2017, conf=0.3)
- TLS 2025 (conf=0.3)

### 3C. 18 records with confidence < 0.5 (LOW)

Spot-check against source PDFs.

---

## 4. Legislation Data Quality

### 4A. 14 AUT legislation rows: substantive status but ALL NULL booleans (MEDIUM)

For AUT years 2009–2012, 2015, 2017–2025, `form_compliance` shows Form E as "substantive"
but every boolean in the `legislation` table is NULL. These appear to be blank templates
or illegible fax transmissions.

**Action:** Determine correct status ("absent" is the most defensible). Update
`form_compliance` for these rows via a new SQL migration.

### 4B. 40 legislation records (12%) with ALL NULL booleans (MEDIUM)

Beyond AUT: CZE, DEU, HRV, JPN, and others. These rows add noise without signal.

---

## 5. Unnamed Facility Patterns

### 5B. 236 facility-year records (15%) have no address AND no city (LOW)

Ungeocodeable. Concentrated in CHE (multilingual forms), DNK (unnamed), SVK, CYP, SVN.

---

## 6. Geocoding Gaps

### 6A. Estonia: 82.9% missing from map (58/70 facility-years) (MEDIUM)

Estonian CBMs give institution names rather than street addresses. Manual geocoding of
5–10 institutions would fix this. See VALIDATION_CHECKLIST §N.

### 6B. Mexico: 59.7% missing from map (77/129 facility-years) (MEDIUM)

Likely address formatting issue. Spot-check MEX records for address field content.
See VALIDATION_CHECKLIST §O.

### 6C. AUS QHFSS: Coordinate jump in 2024 (LOW)

Brisbane (2006–2023) → Canberra (2024). Facility did not move.
**Action:** UPDATE `facility_years` geom for AUS_003 year=2024 to Brisbane coordinates
(−27.56°, 153.04°). See VALIDATION_CHECKLIST §P.

---

## 7. Year and Temporal Anomalies

### 7A. Year 2026 data present (LOW)

BLZ and TUV have 2026 records (CBM submissions for calendar year 2025 with 2026 reporting
dates). Not a bug but creates confusion in year filtering.

---

## 8. Containment Level Anomalies

### 8A. Non-standard containment values (LOW)

`BSL-2+`, `BSL-3+`, `Enhanced BSL-2` fall into the "unknown" bucket in the dashboard's
BSL colour mapping. Consider normalising or adding explicit support.

### 8B. 37 records have has_bsl4=True but bsl4_area_m2=NULL (LOW)

Common for CHE and older AUS submissions. Not wrong, but limits area analysis.

---

## Priority Actions

| Priority | Action | Records affected | Effort |
|----------|--------|-----------------|--------|
| 1 | Verify + fix AUS AAHL 11,000 m² BSL-4 (§2A) | 4 year-records | 15 min + PDF |
| 2 | Delete PRT_003 ghost record (§3A) | 1 record | 5 min + PDF |
| 3 | Fix AUT legislation compliance status (§4A) | 14 rows | 30 min |
| 4 | Add defence entity merge groups — CHE, CAN, BEL, USA (§1F) | ~60 entities | 2 hrs |
| 5 | Manual geocoding for top 5 EST institutions (§6A) | ~58 records | 30 min |
| 6 | Correct AUS QHFSS 2024 coordinates (§6C) | 1 record | 5 min |
| 7 | Investigate MEX geocoding gaps (§6B) | ~77 records | 30 min |

## Completed (removed from above)

- ✅ GBR merge groups (31 → 13 entities) — 2026-03-18
- ✅ AUS_001/AUS_005 AAHL→ACDP merge — 2026-03-18
- ✅ DNK/CYP/SVK unnamed single-year entity merges — 2026-03-18
- ✅ BGR vaccine BulBio entity merge (6 → 1) — 2026-03-18
- ✅ dedup_entities.py integrated into annual_update.sh — prior session
