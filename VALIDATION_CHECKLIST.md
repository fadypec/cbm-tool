# CBM Data — Human Validation Checklist

**Generated:** 2026-03-16
**Purpose:** High-leverage manual spot-checks to catch extraction errors before ISU outreach.
You do not need to review all 517 PDFs — these targets give maximum coverage per hour spent.

---

## How to access source documents

Every facility record in the dashboard → entity modal → "Source PDF" link → original UN portal document.
Direct URL pattern: `https://bwc-cbm.un.org/report/detail/{reportId}`

---

## Tier 1 — Almost certainly extraction errors; check these first

### A. USA Plum Island — 17,643 m² BSL-3 (USA_004, 2010–2011)

**What the data says:** Plum Island Animal Disease Center is declared with 17,643 m² of BSL-3 area in both the 2010 and 2011 USA CBMs.
**Why suspicious:** The entire Plum Island facility covers ~25,000 sq ft (≈ 2,300 m²) of lab space. The declared figure is ≈ 8× the known lab footprint. Almost certainly a sq ft → m² confusion, or the extraction captured the total facility site area rather than just the lab area.
**Action:** Open the 2010 and 2011 USA CBMs. Look at field 5 for Plum Island. Record the actual figure and unit. If wrong, manually UPDATE the two `facility_years` rows for `USA_004`.

---

### B. Côte d'Ivoire CEPRIS-LNSP — 1,000 m² BSL-4 (CIV_001, 2025)

**Source:** https://bwc-cbm.un.org/report/detail/13721
**What the data says:** Institut Pasteur de Côte d'Ivoire / LNSP facility in Abidjan declared with 1,000 m² BSL-4 area in 2025.
**Why suspicious:** CIV has no known BSL-4 infrastructure. This is a translated/OCR document (confidence: 0.85). 1,000 m² BSL-4 would make this one of the largest high-containment facilities in sub-Saharan Africa — a claim that would attract scrutiny in any briefing.
**Action:** Open the 2025 CIV CBM. Check whether "BSL-4" is actually stated, or whether this is a translation artefact (e.g., the French "P4" level was extracted from a sentence about aspirational plans or a different country's reference). If wrong, correct or delete the `bsl4_area_m2` and `has_bsl4` values for `CIV_001`.

---

### C. Australia AAHL — 11,000 m² BSL-4 (AUS_001, 2006–2020)

**What the data says:** Australian Animal Health Laboratory (now ACDP), Geelong, declared consistently at 11,000 m² BSL-4 across 15 years.
**Why suspicious:** The AAHL purpose-built containment building is approximately 6,500 m² gross floor area total. 11,000 m² BSL-4 appears to conflate total facility area with BSL-4 lab space, possibly including grounds, service buildings, or BSL-3 zones.
**Action:** Open any 2010–2015 Australian CBM. Verify the field 5 value for AAHL. Consistent across 15 years suggests this is a systematic extraction issue (the CBM itself may state 11,000 m² as the total site area and the extractor assigned it to BSL-4). If so, this may be correct as declared — note in record metadata.

---

## Tier 2 — Large numbers for known facilities; verify before publishing

### D. GBR Pirbright Institute — 4,167 m² BSL-4 (GBR_017, 2022–2024) and GBR Boehringer Ingelheim — 4,000 m² BSL-4 (GBR_023/024, 2019–2025)

**What the data says:** The Pirbright Institute and the adjacent Boehringer Ingelheim Animal Health UK site (former Merial) each declare ~4,000–4,167 m² BSL-4. Together, this would make the UK declare more BSL-4 area than any country except the USA.
**Why suspicious:** Both sites work on foot-and-mouth disease (a BSL-4 animal pathogen in UK classification). The areas may include containment envelope/airlock/support zones rather than pure working lab area, which is the intended field.
**Action:** Open the 2022–2024 UK CBMs. Compare field 5 for Pirbright and Boehringer. If the figure is stated as total facility area rather than BSL-4 lab area specifically, add a note to the record.

---

### E. Netherlands Wageningen WBVR — 4,500 m² BSL-4 (NLD_004, 2024–2025)

**What the data says:** Wageningen Bioveterinary Research high-containment unit declared with 4,500 m² BSL-4 in the two most recent Dutch CBMs.
**Why suspicious:** This is a new entry (only 2024–2025) and the largest declared BSL-4 area in the Dutch dataset by a large margin.
**Action:** Open the 2024 and 2025 Netherlands CBMs. Verify the figure.

---

### F. DEU Friedrich-Loeffler-Institut — BSL-4 gap 2014–2017 and jump to 1,251 m² in 2023 (DEU_012)

**What the data says:**
- 2007–2013: BSL-4, ~190 m²
- 2014: BSL-3, no BSL-4 area
- 2015: unknown containment
- 2016–2017: BSL-3, 190 m²
- 2018: BSL-4, 106 m² + 212 m² BSL-3
- 2019–2022: BSL-4, 264 m² + 917 m² BSL-3
- 2023–2025: BSL-4, 1,251 m² (BSL-3 area drops to NULL)

**Context:** FLI on Riems Island (near Greifswald) built a new BSL-4 facility from ~2013 to 2020. The gap years (2014–2017) likely reflect the old BSL-4 being decommissioned. The 2023 jump to 1,251 m² likely reflects the completed new containment building.
**Action:** This is probably correct, but the 2023 step-change (264 → 1,251 m² BSL-4, BSL-3 disappearing entirely) is analytically striking. Open the 2023 DEU CBM and verify. If correct, note it as a real facility expansion.

---

## Tier 3 — Anomalous patterns in high-significance facilities

### G. JPN Murayama — intermittent BSL-4 area reporting (JPN_002, 2012–2025)

**What the data says:** `has_bsl4 = true` in all years, but `bsl4_area_m2` is only populated in some years (2013: 2,270 m²; 2020: 2,270 m²; 2022: 2,270 m²). Most years show `has_bsl4=true` with no area figure. The same 2,270 m² value also appears as `bsl3_area_m2` in 2013 and 2024.
**Why suspicious:** The BSL-4 flag is consistent but the area figure is only populated sporadically, and when it appears it's the same round number as the BSL-3 area in adjacent years. This suggests the extractor is alternately assigning the same area figure to BSL-4 or BSL-3 depending on how the CBM phrased it that year.
**Action:** Open 2013 and 2020 JPN CBMs and compare field 5 for the Murayama Annex. Determine whether the facility has both BSL-3 and BSL-4 suites, or only BSL-3/4 depending on year. The 2,270 m² figure likely represents total high-containment lab area.

---

### H. PRT_003 — ghost record (Portugal 2011, zero confidence, no name, no address)

**Source:** https://bwc-cbm.un.org/report/detail/9718
**What the data says:** A `facility_years` record exists for PRT 2011 with no facility name, no address, no containment level, and confidence = 0.000. This appears to be a spurious extraction row.
**Action:** Open the 2011 Portugal CBM. Check whether Form A Part 1 contains any facility declaration. If it is "nothing to declare" or blank, delete the `facility_years` row for `PRT_003 / PRT_2011`. If an unnamed facility was genuinely declared, note it.

---

### I. UKR SSCIBSM — 1,374 m² BSL-4 throughout 2015–2023 (UKR_004)

**What the data says:** State Scientific Control Institute of Biotechnology and Strains of Microorganisms (Kyiv) has declared BSL-4 capacity since 2015, with a gap in 2022 (Ukraine submitted no CBM that year) and a resumption in 2023 with 1,374 m² BSL-4.
**Context:** Analytically significant — Ukraine's declaration of BSL-4 infrastructure during and after the Russian invasion is notable. The 2023 figure (1,374 m²) is larger than previous years (area field was null in 2015–2021, suggesting the area was not consistently reported, only the flag).
**Action:** Open the 2023 UKR CBM. Verify the 1,374 m² BSL-4 area for SSCIBSM. Check whether Ukraine resumed reporting after 2022 or whether only a partial CBM was submitted.

---

## Tier 4 — Entity resolution errors (DB fixes only, no PDF review needed)

These should be corrected by running `scripts/dedup_entities.py` after review. **Do not edit the database manually** — use the script which applies changes as a traceable migration.

### J. Switzerland (CHE) — 14 entities should be ~4

| Correct canonical | Current entity IDs to merge |
|---|---|
| Spiez Laboratory (Spiez/Gerzensee) | CHE_001, CHE_004, CHE_007 |
| National Reference Center for Emerging Viral Infections (HUG Geneva) | CHE_003, CHE_006, CHE_008 |
| Institut für Viruskrankheiten und Immunprophylaxe / IVI (Mittelhäusern) | CHE_002, CHE_005, CHE_010, CHE_011, CHE_013 |
| Institute of Medical Virology (Universität Zürich) | CHE_009, CHE_012, CHE_014 |

Entity resolution failed here because the same facilities are named in German, French, and English across different submission years.

---

### K. USA CDC Atlanta — 5 entities should be 1

| Correct canonical | Current entity IDs |
|---|---|
| Centers for Disease Control and Prevention (CDC) — High Containment Labs, Atlanta | USA_007, USA_009, USA_013, USA_014, USA_018 |

All are the same CDC Atlanta campus, declared under successive administrative reorganisation names (CCID → OID → DDID → "CDC"). Years declared: 2010–2025 continuously.

---

### L. Ukraine — Mechnikov Anti-Plague Institute (~5 → 1)

| Correct canonical | Current entity IDs |
|---|---|
| I.I. Mechnikov Ukrainian Research Anti-Plague Institute, Odesa | UKR_001, UKR_009, UKR_011, UKR_012, UKR_018 |

Multiple Russian/Ukrainian transliterations of the same Odesa institute.

---

### M. Germany — Marburg Virology (2 → 1)

| Correct canonical | Current entity IDs |
|---|---|
| Institut für Virologie, Philipps-Universität Marburg | DEU_013 (German name, 2007–2019), DEU_015 (English name, 2020–2025) |

Same institution, different language used in submission. Years run continuously 2007–2025.

---

## Tier 5 — Geocoding gaps (affect map coverage)

### N. Estonia — 58 of 70 facility-years not on map (82.9% missing)

Estonia is one of the most active CBM submitters but nearly invisible on the map. Estonian CBMs likely give institution names rather than full street addresses in field 3, which Nominatim cannot resolve. A manual lookup of the 5–10 most frequently declared Estonian institutions (e.g. National Institute for Health Development, Health Board laboratory) and hard-coding their coordinates would dramatically improve Estonian coverage.

---

### O. Mexico — 77 of 129 facility-years not on map (59.7% missing)

Mexico has among the highest facility-year counts in the dataset. Most Mexican facilities should be geocodable — the issue is likely address formatting. Spot-check ~10 MEX records in the DB for address field content.

---

### P. Australia QHFSS — coordinate jump in 2024

Queensland Health Forensic Scientific Services geocodes to Brisbane (−27.56°, 153.04°) consistently from 2006–2023, but to Canberra (−35.31°, 149.14°) in 2024. The facility did not move to Canberra. The 2024 CBM likely gave a different address (possibly a head-office address after a reorganisation). Manually correct the 2024 record's geom to the Brisbane coordinates.

---

## Summary priority order

If preparing for an ISU briefing:

| Priority | Item | Action | Time |
|---|---|---|---|
| 1 | USA_004 Plum Island area | Open 2010 USA CBM, verify field 5 | 10 min |
| 2 | CIV_001 BSL-4 claim | Open CIV_2025 CBM, verify field 5 | 10 min |
| 3 | AUS_001 AAHL area | Open any 2010-2015 AUS CBM, verify field 5 | 10 min |
| 4 | Run `scripts/dedup_entities.py` | Fix CHE/USA/UKR/DEU entity merges | 15 min |
| 5 | GBR Pirbright + Boehringer | Open 2022–2024 UK CBM | 15 min |
| 6 | NLD Wageningen | Open 2024–2025 NLD CBMs | 10 min |
| 7 | Delete PRT_003 ghost record | Open PRT 2011, confirm blank, delete row | 5 min |
| 8 | Fix AUS QHFSS 2024 coord | Update geom for QHFSS 2024 to Brisbane | 5 min |
| 9 | Geocode 5 EST facilities | Manual lookup, update geom | 30 min |
| 10 | Check DEU FLI 2023 jump | Open 2023 DEU CBM | 10 min |

Total estimated time: ~2 hours
